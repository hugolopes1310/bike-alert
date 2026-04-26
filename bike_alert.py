#!/usr/bin/env python3
"""
Bike alert bot — monitors Leboncoin, eBay, Troc-Vélo and Vinted
for specific bikes, pings you on Telegram when a new ad appears.

See README.txt in this folder for setup instructions.
"""

import os
import re
import sys
import json
import time
import email
import imaplib
import hashlib
import requests
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # Troc-Vélo will be skipped if bs4 missing

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# curl_cffi mimics a real Chrome TLS fingerprint — this is what lets us
# get through DataDome (Leboncoin) and similar protections. If it's not
# installed we fall back to regular requests (which will likely 403).
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CFFI = False


# ============ CONFIG ============
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "PUT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "PUT_CHAT_ID_HERE")

# Gmail credentials for parsing Leboncoin alert emails (free DataDome bypass)
GMAIL_USER         = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_LBC_FROM     = os.getenv("GMAIL_LBC_FROM", "leboncoin.fr")
GMAIL_LOOKBACK_HOURS = int(os.getenv("GMAIL_LOOKBACK_HOURS", "24"))

SEARCHES = [
    # --- originals ---
    {
        "name": "Cube Attain C:62 Race (M)",
        "keywords": ["cube attain c:62 race", "cube attain c62 race",
                     "cube attain c 62"],
        "require_all": ["cube", "attain"],
        "size": "M",
        "max_price": 2800,
    },
    {
        "name": "Canyon Roadlite CF 7 (M)",
        "keywords": ["canyon roadlite cf 7", "canyon roadlite cf7",
                     "canyon roadlite"],
        "require_all": ["canyon", "roadlite"],
        "size": "M",
        "max_price": 1800,
    },
    # --- Van Rysel (Decathlon) — best value-for-money ---
    {
        "name": "Van Rysel RCR Pro Di2 (M)",
        "keywords": ["van rysel rcr pro", "van rysel rcr",
                     "decathlon van rysel rcr"],
        "require_all": ["van rysel", "rcr"],
        "require_any": ["di2", "ultegra", "axs"],
        "size": "M",
        "max_price": 2800,
    },
    {
        "name": "Van Rysel EDR CF Di2 (M)",
        "keywords": ["van rysel edr cf", "van rysel edr",
                     "decathlon van rysel edr"],
        "require_all": ["van rysel", "edr"],
        "require_any": ["di2", "ultegra", "axs"],
        "size": "M",
        "max_price": 2200,
    },
    {
        "name": "Van Rysel GRVL CF GRX Di2 (M)",
        "keywords": ["van rysel grvl", "decathlon van rysel grvl",
                     "van rysel gravel"],
        "require_all": ["van rysel"],
        "require_any": ["grvl", "gravel", "grx"],
        "size": "M",
        "max_price": 2700,
    },
    # --- Canyon ---
    {
        "name": "Canyon Ultimate CF SL Di2 (M)",
        "keywords": ["canyon ultimate cf sl di2", "canyon ultimate cfsl",
                     "canyon ultimate disc"],
        "require_all": ["canyon", "ultimate"],
        "require_any": ["di2", "axs"],
        "size": "M",
        "max_price": 3000,
    },
    {
        "name": "Canyon Endurace CF Di2 (M)",
        "keywords": ["canyon endurace cf di2", "canyon endurace disc"],
        "require_all": ["canyon", "endurace"],
        "require_any": ["di2", "axs"],
        "size": "M",
        "max_price": 2600,
    },
    {
        "name": "Canyon Aeroad CF SL Di2 (M)",
        "keywords": ["canyon aeroad cf sl", "canyon aeroad disc"],
        "require_all": ["canyon", "aeroad"],
        "require_any": ["di2", "axs"],
        "size": "M",
        "max_price": 3300,
    },
    {
        "name": "Canyon Grizl CF GRX Di2 (M)",
        "keywords": ["canyon grizl cf", "canyon grizl di2",
                     "canyon grizl grx"],
        "require_all": ["canyon", "grizl"],
        "require_any": ["di2", "grx", "axs"],
        "size": "M",
        "max_price": 2800,
    },
    # --- Cube ---
    {
        "name": "Cube Attain GTC SLX Di2 (M)",
        "keywords": ["cube attain gtc slx", "cube attain slx",
                     "cube attain gtc di2"],
        "require_all": ["cube", "attain"],
        "require_any": ["slx", "di2", "ultegra"],
        "size": "M",
        "max_price": 3000,
    },
]

# Accessory / clothing keywords — if any of these appear in the title
# AND no "bike" indicator does, the ad is treated as an accessory and skipped.
ACCESSORY_TERMS = [
    "trikot", "maillot", "cuissard", "bib short", "bibshort",
    "radhose", "radshort", "pantalon", "jersey", "t-shirt", "shirt",
    "pullover", "pulli", "pull ", "jacke", "jacket", "veste", "weste",
    "gilet", "mantel", "sakko", "anzug", "blazer", "hemd", "rock",
    "kleid", "dress", "hose ",
    "schuh", "chaussure", "handschuh", "gant", "chaussette",
    "helm", "casque", "cockpit", "lenker", "guidon", "manubrio",
    "flaschen", "halter", "bidon", "bottle",
    "schutz", "garde-boue", "fender",
    "sattel", "selle", "tige ",
    "schlauch", "chambre à air", "pneu", "reifen", "tire",
    "cassette", "kette", "chaîne",
    "kurbel", "pédalier", "plateau",
]
BIKE_INDICATORS = ["vélo", "velo", "bike", "fahrrad", "bicycle",
                   "bicicleta", "cadre ", "rahmen", "frame ", "frameset",
                   "vtc", "gravel", "route", "roadbike", "rennrad",
                   "e-bike", "ebike", "mountainbike", "mtb"]

ENABLED_SOURCES = ["ebay", "vinted", "lbc_email"]
# Designed to run on GitHub Actions:
#  - ebay + vinted: scraped directly from cloud IPs (no anti-bot block)
#  - lbc_email: parses Gmail for native Leboncoin alert emails
#    (DataDome can't block what doesn't go through scraping)
# Direct LBC/Troc-Vélo scraping is left in code (search_leboncoin/
# search_trocvelo) and only works from a residential IP.
POLL_INTERVAL   = 300  # seconds between full scans

STATE_FILE  = Path(__file__).parent / "seen_ads.json"
STATS_FILE  = Path(__file__).parent / "stats.json"
PRICES_FILE = Path(__file__).parent / "prices.json"
LOG_FILE    = Path(__file__).parent / "bike_alert.log"

# How long we keep things around in the JSON state files.
SEEN_RETENTION_DAYS   = 30   # an ad we saw 30+ days ago can be re-pinged
PRICES_RETENTION_DAYS = 90   # 3 months of price history per model
STATS_RETENTION_DAYS  = 14   # only need last week for the heartbeat

# Deal scoring: ping with 🔥 if price ≤ (median × (1 - DEAL_THRESHOLD_PCT/100))
DEAL_THRESHOLD_PCT = 15
# Need at least this many historical data points before we trust the median
DEAL_MIN_SAMPLES = 5
# ================================


# ---------- logging ----------
def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------- state ----------
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def load_seen():
    """
    Returns a dict of {ad_id: first_seen_iso}.
    Backward-compatible with the old list-of-ids format.
    """
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text())
    except Exception:
        return {}
    if isinstance(raw, list):
        # migrate from old format — give every existing id a "now" timestamp
        ts = now_iso()
        return {ad_id: ts for ad_id in raw}
    if isinstance(raw, dict):
        return raw
    return {}


def save_seen(seen):
    """Prune entries older than SEEN_RETENTION_DAYS, then persist."""
    cutoff = datetime.now() - timedelta(days=SEEN_RETENTION_DAYS)
    pruned = {
        ad_id: ts for ad_id, ts in seen.items()
        if (_parse_iso(ts) or datetime.now()) >= cutoff
    }
    dropped = len(seen) - len(pruned)
    seen.clear()
    seen.update(pruned)
    STATE_FILE.write_text(json.dumps(seen, indent=0))
    if dropped:
        log(f"[*] Pruned {dropped} ads older than {SEEN_RETENTION_DAYS}d")


# ---------- stats (for heartbeat) ----------
def load_stats():
    if not STATS_FILE.exists():
        return {"runs": [], "last_heartbeat": None}
    try:
        s = json.loads(STATS_FILE.read_text())
        s.setdefault("runs", [])
        s.setdefault("last_heartbeat", None)
        return s
    except Exception:
        return {"runs": [], "last_heartbeat": None}


def save_stats(stats):
    cutoff = datetime.now() - timedelta(days=STATS_RETENTION_DAYS)
    stats["runs"] = [
        r for r in stats.get("runs", [])
        if (_parse_iso(r.get("ts")) or datetime.now()) >= cutoff
    ]
    STATS_FILE.write_text(json.dumps(stats, indent=0))


# ---------- prices (for deal scoring) ----------
def load_prices():
    if not PRICES_FILE.exists():
        return {}
    try:
        return json.loads(PRICES_FILE.read_text())
    except Exception:
        return {}


def save_prices(prices):
    cutoff = datetime.now() - timedelta(days=PRICES_RETENTION_DAYS)
    pruned = {}
    for name, points in prices.items():
        kept = [p for p in points
                if (_parse_iso(p.get("ts")) or datetime.now()) >= cutoff]
        if kept:
            pruned[name] = kept
    PRICES_FILE.write_text(json.dumps(pruned, indent=0))


def record_price(prices, search_name, price):
    """Append a price observation for a given search."""
    if not price or price <= 0:
        return
    prices.setdefault(search_name, []).append({
        "ts": now_iso(),
        "price": int(price),
    })


def get_median_price(prices, search_name):
    """Return the median price for a search, or None if too few samples."""
    points = prices.get(search_name, [])
    if len(points) < DEAL_MIN_SAMPLES:
        return None
    sorted_p = sorted(int(p["price"]) for p in points if p.get("price"))
    if not sorted_p:
        return None
    n = len(sorted_p)
    if n % 2:
        return sorted_p[n // 2]
    return (sorted_p[n // 2 - 1] + sorted_p[n // 2]) / 2


# ---------- notifications ----------
def send_telegram(text):
    if "PUT_TOKEN" in TELEGRAM_TOKEN:
        log("[!] TELEGRAM_TOKEN not configured — printing instead")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "false",
        }, timeout=10)
        if r.status_code != 200:
            log(f"[!] Telegram {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log(f"[!] Telegram error: {e}")
        return False


def send_telegram_photo(image_url, caption):
    """
    sendPhoto with a caption (HTML formatted). Telegram captions are limited
    to 1024 chars. Returns True on success; on failure caller should fall
    back to a plain text message so the user always gets the alert.
    """
    if "PUT_TOKEN" in TELEGRAM_TOKEN:
        log("[!] TELEGRAM_TOKEN not configured — printing instead")
        print(caption)
        return False
    if not image_url:
        return False
    if len(caption) > 1024:
        caption = caption[:1020] + "…"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        r = requests.post(url, data={
            "chat_id":    TELEGRAM_CHAT_ID,
            "photo":      image_url,
            "caption":    caption,
            "parse_mode": "HTML",
        }, timeout=15)
        if r.status_code != 200:
            log(f"[!] Telegram photo {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log(f"[!] Telegram photo error: {e}")
        return False


def send_mac_notification(title, subtitle, body):
    """macOS native notification banner. Silent fail on non-Mac."""
    if sys.platform != "darwin":
        return
    import subprocess
    try:
        # AppleScript escaping: replace " and \ with safe equivalents
        def esc(s): return (s or "").replace("\\", " ").replace('"', "'")
        script = (f'display notification "{esc(body)}" '
                  f'with title "{esc(title)}" subtitle "{esc(subtitle)}"')
        subprocess.run(["osascript", "-e", script],
                       timeout=5, capture_output=True)
    except Exception as e:
        log(f"[!] Mac notification error: {e}")


def notify(ad):
    """Send all enabled notifications for one new ad."""
    caption = format_ad(ad)
    sent_via_photo = False
    if ad.get("image_url"):
        sent_via_photo = send_telegram_photo(ad["image_url"], caption)
    if not sent_via_photo:
        send_telegram(caption)
    send_mac_notification(
        title=f"Nouvelle annonce — {ad['source']}",
        subtitle=ad.get("search_name", ""),
        body=f"{ad['title']} — {ad.get('price') or '?'}€",
    )


UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


# ---------- leboncoin ----------
_lbc_session = None

def _lbc_sess():
    """Bootstrap a curl_cffi session with DataDome cookies from homepage."""
    global _lbc_session
    if _lbc_session is not None:
        return _lbc_session
    if not HAS_CFFI:
        return None
    s = cffi_requests.Session(impersonate="chrome124")
    try:
        # Visiting the homepage gives us the datadome cookie
        s.get("https://www.leboncoin.fr/", timeout=20)
        s.get("https://www.leboncoin.fr/recherche?category=55", timeout=20)
    except Exception as e:
        log(f"[!] LBC session bootstrap failed: {e}")
        return None
    _lbc_session = s
    return s


def search_leboncoin(keywords, max_price):
    if not HAS_CFFI:
        log("[!] Leboncoin skipped: install curl_cffi "
            "(pip install curl_cffi) to bypass DataDome")
        return []
    s = _lbc_sess()
    if s is None:
        return []
    results = []
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": "https://www.leboncoin.fr/recherche",
        "Origin":  "https://www.leboncoin.fr",
        "Content-Type": "application/json",
    }
    for kw in keywords:
        payload = {
            "filters": {
                "category": {"id": "55"},
                "keywords": {"text": kw},
                "ranges": {"price": {"max": max_price}},
            },
            "limit": 30, "offset": 0,
            "sort_by": "time", "sort_order": "desc",
        }
        try:
            r = s.post("https://api.leboncoin.fr/finder/search",
                       json=payload, headers=headers, timeout=20)
            if r.status_code == 403:
                # rotate the session once
                global _lbc_session
                _lbc_session = None
                s2 = _lbc_sess()
                if s2 is None:
                    log(f"[!] LBC '{kw}' → 403 (cannot refresh session)")
                    continue
                r = s2.post("https://api.leboncoin.fr/finder/search",
                            json=payload, headers=headers, timeout=20)
            if r.status_code != 200:
                log(f"[!] LBC '{kw}' → {r.status_code}")
                continue
            for ad in r.json().get("ads", []):
                price = 0
                p = ad.get("price")
                if isinstance(p, list) and p: price = p[0]
                elif isinstance(p, (int, float)): price = p
                img_url = ""
                images = ad.get("images") or {}
                if isinstance(images, dict):
                    urls = images.get("urls") or []
                    if isinstance(urls, list) and urls:
                        img_url = urls[0]
                    if not img_url:
                        img_url = images.get("thumb_url") or ""
                results.append({
                    "source": "leboncoin",
                    "id":    f"lbc_{ad.get('list_id')}",
                    "title": ad.get("subject", ""),
                    "body":  ad.get("body", ""),
                    "price": price,
                    "url":   ad.get("url", ""),
                    "location": (ad.get("location") or {}).get("city", ""),
                    "image_url": img_url,
                })
        except Exception as e:
            log(f"[!] LBC error '{kw}': {e}")
    return results


# ---------- ebay (HTML scraping) ----------
def search_ebay(keywords, max_price):
    if BeautifulSoup is None:
        log("[!] eBay skipped (install beautifulsoup4)")
        return []
    results = []
    client = cffi_requests if HAS_CFFI else requests
    for kw in keywords:
        q = quote_plus(kw)
        url = (f"https://www.ebay.fr/sch/i.html?_nkw={q}"
               f"&_udhi={max_price}&_sop=10&LH_BIN=1")
        try:
            kwargs = {"timeout": 25, "headers": {
                "User-Agent": UA,
                "Accept-Language": "fr-FR,fr;q=0.9",
            }}
            if HAS_CFFI:
                kwargs["impersonate"] = "chrome124"
            r = client.get(url, **kwargs)
            if r.status_code != 200:
                log(f"[!] eBay '{kw}' → {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for li in soup.select("li.s-item"):
                a = li.select_one("a.s-item__link")
                t = li.select_one(".s-item__title")
                p = li.select_one(".s-item__price")
                if not (a and t): continue
                href = a.get("href", "").split("?")[0]
                title = t.get_text(" ", strip=True)
                if not href or not title or "shop on ebay" in title.lower():
                    continue
                price = 0
                if p:
                    m = re.search(r"(\d[\d\s.,]*)", p.get_text())
                    if m:
                        try:
                            price = int(float(m.group(1)
                                              .replace(" ", "")
                                              .replace(",", ".")))
                        except ValueError: pass
                if price and price > max_price:
                    continue
                # Pull thumbnail (eBay lazy-loads, so check data-src too)
                img_url = ""
                img = li.select_one("img.s-item__image-img, img")
                if img:
                    img_url = (img.get("src") or img.get("data-src")
                               or img.get("data-defer-load") or "")
                    # eBay sometimes serves a 1x1 placeholder; ignore it
                    if "ir.ebaystatic" in img_url or "s-l1.gif" in img_url:
                        img_url = img.get("data-src") or ""
                results.append({
                    "source": "ebay",
                    "id":    f"ebay_{hashlib.md5(href.encode()).hexdigest()[:12]}",
                    "title": title, "body": "",
                    "price": price, "url": href, "location": "",
                    "image_url": img_url,
                })
        except Exception as e:
            log(f"[!] eBay error '{kw}': {e}")
    return results


# ---------- troc-velo ----------
_tv_session = None

def _tv_sess():
    global _tv_session
    if _tv_session is not None:
        return _tv_session
    if HAS_CFFI:
        s = cffi_requests.Session(impersonate="chrome124")
    else:
        s = requests.Session()
        s.headers.update({"User-Agent": UA})
    try:
        s.get("https://www.troc-velo.com/", timeout=20)
    except Exception as e:
        log(f"[!] Troc-Vélo session bootstrap failed: {e}")
        return None
    _tv_session = s
    return s


def search_trocvelo(keywords, max_price):
    if BeautifulSoup is None:
        log("[!] Troc-Vélo skipped (install beautifulsoup4)")
        return []
    s = _tv_sess()
    if s is None:
        return []
    results = []
    for kw in keywords:
        q = quote_plus(kw)
        url = (f"https://www.troc-velo.com/annonces.html?"
               f"action=search&q={q}&prix_max={max_price}")
        try:
            r = s.get(url, timeout=20,
                      headers={"Accept-Language": "fr-FR,fr;q=0.9"})
            if r.status_code == 403:
                global _tv_session
                _tv_session = None
                s2 = _tv_sess()
                if s2 is None:
                    log(f"[!] Troc-Vélo '{kw}' → 403 (no session)")
                    continue
                r = s2.get(url, timeout=20)
            if r.status_code != 200:
                log(f"[!] Troc-Vélo '{kw}' → {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            # Annonces appear as <article> or <div class="annonce"> blocks.
            # We look at every link pointing to an ad URL.
            for a in soup.select("a[href*='/annonce/']"):
                href = a.get("href", "")
                if not href: continue
                if href.startswith("/"):
                    href = "https://www.troc-velo.com" + href
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 8:
                    continue
                # Try to pull the price from a nearby element
                price = 0
                parent = a.find_parent()
                if parent:
                    m = re.search(r"(\d[\d\s]{1,6})\s*€", parent.get_text(" "))
                    if m:
                        try: price = int(m.group(1).replace(" ", ""))
                        except ValueError: pass
                if price and price > max_price:
                    continue
                ad_id = hashlib.md5(href.encode()).hexdigest()[:12]
                results.append({
                    "source": "trocvelo",
                    "id":    f"tv_{ad_id}",
                    "title": title, "body": "",
                    "price": price, "url": href, "location": "",
                })
        except Exception as e:
            log(f"[!] Troc-Vélo error '{kw}': {e}")
    # dedupe
    seen_ids = set(); uniq = []
    for r in results:
        if r["id"] in seen_ids: continue
        seen_ids.add(r["id"]); uniq.append(r)
    return uniq


# ---------- vinted ----------
_vinted_session = None

def _vinted_sess():
    """Vinted's API needs a valid session cookie from the homepage."""
    global _vinted_session
    if _vinted_session is not None:
        return _vinted_session
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    try:
        s.get("https://www.vinted.fr/", timeout=15)
    except Exception as e:
        log(f"[!] Vinted session bootstrap failed: {e}")
        return None
    _vinted_session = s
    return s

def search_vinted(keywords, max_price):
    s = _vinted_sess()
    if s is None: return []
    results = []
    for kw in keywords:
        url = "https://www.vinted.fr/api/v2/catalog/items"
        params = {
            "search_text": kw,
            "price_to":    max_price,
            "currency":    "EUR",
            "order":       "newest_first",
            "per_page":    20,
        }
        try:
            r = s.get(url, params=params, timeout=15,
                      headers={"Referer": "https://www.vinted.fr/"})
            if r.status_code == 401 or r.status_code == 403:
                # refresh session once
                global _vinted_session
                _vinted_session = None
                s2 = _vinted_sess()
                if s2 is None: continue
                r = s2.get(url, params=params, timeout=15,
                           headers={"Referer": "https://www.vinted.fr/"})
            if r.status_code != 200:
                log(f"[!] Vinted '{kw}' → {r.status_code}")
                continue
            for item in r.json().get("items", []):
                price_obj = item.get("price") or item.get("total_item_price") or {}
                if isinstance(price_obj, dict):
                    try: price = float(price_obj.get("amount", 0))
                    except (TypeError, ValueError): price = 0
                else:
                    try: price = float(price_obj)
                    except (TypeError, ValueError): price = 0
                # Photo: Vinted returns photo as object, sometimes a list of photos
                img_url = ""
                photo = item.get("photo")
                if isinstance(photo, dict):
                    img_url = photo.get("url") or photo.get("full_size_url") or ""
                elif isinstance(item.get("photos"), list) and item["photos"]:
                    img_url = (item["photos"][0].get("url")
                               or item["photos"][0].get("full_size_url") or "")
                results.append({
                    "source": "vinted",
                    "id":    f"vin_{item.get('id')}",
                    "title": item.get("title", ""),
                    "body":  item.get("description", "") or "",
                    "price": int(price) if price else 0,
                    "url":   item.get("url", ""),
                    "location": "",
                    "image_url": img_url,
                })
        except Exception as e:
            log(f"[!] Vinted error '{kw}': {e}")
    return results


# ---------- leboncoin via Gmail (free DataDome bypass) ----------
def search_leboncoin_email():
    """
    Connects to Gmail via IMAP, finds Leboncoin alert emails received
    in the last GMAIL_LOOKBACK_HOURS, parses out new ad listings,
    marks the emails as read so they're not re-processed.

    Requires: GMAIL_USER + GMAIL_APP_PASSWORD env vars.
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log("[!] lbc_email skipped: GMAIL_USER/GMAIL_APP_PASSWORD not set")
        return []
    if BeautifulSoup is None:
        log("[!] lbc_email skipped: install beautifulsoup4")
        return []

    results = []
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        imap.select("INBOX")

        # Search for LBC emails received in the lookback window.
        # Using SINCE (not UNSEEN) so a fresh deployment can backfill.
        since = (datetime.now() - timedelta(hours=GMAIL_LOOKBACK_HOURS)
                ).strftime("%d-%b-%Y")
        criterion = f'(SINCE "{since}" FROM "{GMAIL_LBC_FROM}")'
        status, data = imap.search(None, criterion)
        if status != "OK":
            log(f"[!] Gmail search failed: {status}")
            imap.logout()
            return []

        msg_ids = data[0].split()
        log(f"[*] lbc_email: {len(msg_ids)} LBC email(s) in last "
            f"{GMAIL_LOOKBACK_HOURS}h")

        for mid in msg_ids:
            status, msg_data = imap.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            # Extract HTML body
            html = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        try:
                            charset = part.get_content_charset() or "utf-8"
                            html = part.get_payload(decode=True).decode(
                                charset, errors="replace")
                            break
                        except Exception:
                            continue
            elif msg.get_content_type() == "text/html":
                try:
                    charset = msg.get_content_charset() or "utf-8"
                    html = msg.get_payload(decode=True).decode(
                        charset, errors="replace")
                except Exception:
                    pass

            if not html:
                continue

            # Extract ads from the HTML
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Real ad URLs always have the path /ad/<category>/<id>
                # or end with /<numeric_id>.htm
                if "leboncoin.fr" not in href:
                    continue
                if not (re.search(r"/\d{8,}", href) or "/ad/" in href):
                    continue

                # Filter out unsubscribe / preferences / nav links by text
                txt = a.get_text(" ", strip=True)
                if not txt or len(txt) < 5:
                    continue
                low = txt.lower()
                if any(x in low for x in [
                    "désabonner", "désinscri", "désinscription",
                    "préférences", "unsubscribe", "voir toutes",
                    "modifier ma recherche", "gérer mes alertes",
                ]):
                    continue

                # Try to extract price from the surrounding container
                price = 0
                container = a.find_parent(["td", "tr", "div"]) or a.find_parent()
                if container:
                    m = re.search(r"(\d[\d\s]{1,6})\s*€",
                                  container.get_text(" "))
                    if m:
                        try:
                            price = int(m.group(1).replace(" ", ""))
                        except ValueError:
                            pass

                # Find a thumbnail: prefer <img> nested inside the <a>,
                # otherwise look at the same container.
                img_url = ""
                inner_img = a.find("img")
                if inner_img and inner_img.get("src"):
                    img_url = inner_img["src"]
                elif container:
                    other_img = container.find("img")
                    if other_img and other_img.get("src"):
                        cand = other_img["src"]
                        # Skip tiny tracking pixels & email-template chrome
                        if not any(x in cand.lower() for x in [
                            "pixel", "tracking", "logo", "icon", "spacer",
                        ]):
                            img_url = cand

                clean_url = href.split("?")[0]
                ad_id = (f"lbcmail_"
                         f"{hashlib.md5(clean_url.encode()).hexdigest()[:12]}")

                results.append({
                    "source": "leboncoin (email)",
                    "id":    ad_id,
                    "title": txt,
                    "body":  "",
                    "price": price,
                    "url":   clean_url,
                    "location": "",
                    "image_url": img_url,
                })

            # Mark email as read so we don't reprocess on next tick
            try:
                imap.store(mid, "+FLAGS", "\\Seen")
            except Exception:
                pass

        imap.logout()
    except Exception as e:
        log(f"[!] lbc_email error: {e}")

    # Dedupe within this batch
    seen_local = set()
    uniq = []
    for r in results:
        if r["id"] in seen_local:
            continue
        seen_local.add(r["id"])
        uniq.append(r)
    return uniq


# ---------- filter ----------
def matches(ad, search):
    """
    Multi-stage filter:
     1) all `require_all` tokens must appear in title+body (strict)
     2) if any accessory term appears AND no bike indicator, reject
     3) soft size filter: skip only if another size is explicitly mentioned
    """
    text = f"{ad['title']} {ad.get('body','')}".lower()

    # (1a) strict model match — all of these must be present
    for token in search.get("require_all", []):
        if token.lower() not in text:
            return False

    # (1b) at least one of these must be present (e.g. "di2" or "axs")
    any_tokens = search.get("require_any", [])
    if any_tokens:
        if not any(tok.lower() in text for tok in any_tokens):
            return False

    # (2) accessory filter
    has_accessory = any(term in text for term in ACCESSORY_TERMS)
    has_bike_hint = any(ind in text for ind in BIKE_INDICATORS)
    if has_accessory and not has_bike_hint:
        return False

    # (3) size filter
    size = search["size"].lower()
    ours = [rf"\btaille\s*{size}\b",
            rf"\bcadre\s*{size}\b",
            rf"\b{size}\s*\(\d{{2,3}}\)",
            rf"\bgr\.?\s*{size}\b",
            rf"\bgröße\s*{size}\b",
            rf"\brahmen\s*{size}\b",
            rf"\bsize\s*{size}\b"]
    others = [s for s in ["xs", "s", "l", "xl", "xxl"] if s != size]
    others_pat = ([rf"\btaille\s*{s}\b" for s in others] +
                  [rf"\bcadre\s*{s}\b"  for s in others] +
                  [rf"\bgr\.?\s*{s}\b"  for s in others] +
                  [rf"\bgröße\s*{s}\b"  for s in others])
    if any(re.search(p, text) for p in ours):
        return True
    if any(re.search(p, text) for p in others_pat):
        return False
    return True


# ---------- main loop ----------
SEARCH_FUNCS = {
    "leboncoin": search_leboncoin,
    "ebay":      search_ebay,
    "trocvelo":  search_trocvelo,
    "vinted":    search_vinted,
}

def _annotate_deal(ad, prices):
    """Tag an ad with the median price + deal_pct for its search bucket."""
    if not ad.get("price"):
        return
    bucket = ad.get("search_name") or "default"
    median = get_median_price(prices, bucket)
    if median:
        ad["median_price"] = median
        ad["deal_pct"] = round((median - ad["price"]) / median * 100)


def check_once(seen, first_run, prices):
    """
    Returns (new_ads, run_stats) where run_stats is:
        {sources_ok: [...], sources_failed: [...], total_found: int}
    Mutates `seen` (adds new ids) and `prices` (records observations).
    """
    new_ads = []
    run_stats = {"sources_ok": [], "sources_failed": [], "total_found": 0}

    # --- per-search keyword sources (vinted, ebay, lbc, troc) ---
    keyword_sources = [s for s in ENABLED_SOURCES if s != "lbc_email"]
    for search in SEARCHES:
        found = []
        for src in keyword_sources:
            fn = SEARCH_FUNCS.get(src)
            if not fn: continue
            try:
                results = fn(search["keywords"], search["max_price"])
                found += results
                if src not in run_stats["sources_ok"]:
                    run_stats["sources_ok"].append(src)
            except Exception as e:
                log(f"[!] {src} threw: {e}")
                err = f"{src}: {type(e).__name__}"
                if err not in run_stats["sources_failed"]:
                    run_stats["sources_failed"].append(err)
        run_stats["total_found"] += len(found)

        for ad in found:
            if ad["id"] in seen:
                continue
            seen[ad["id"]] = now_iso()
            if first_run:
                # Still record price so we build history during the seed run
                if matches(ad, search) and ad.get("price"):
                    record_price(prices, search["name"], ad["price"])
                continue
            if not matches(ad, search):
                continue
            ad["search_name"] = search["name"]
            # Score against historical median BEFORE recording this point
            _annotate_deal(ad, prices)
            record_price(prices, search["name"], ad["price"])
            new_ads.append(ad)

    # --- lbc_email source: queried once (emails contain pre-filtered ads) ---
    if "lbc_email" in ENABLED_SOURCES:
        try:
            email_ads = search_leboncoin_email()
            run_stats["sources_ok"].append("lbc_email")
            run_stats["total_found"] += len(email_ads)
            for ad in email_ads:
                if ad["id"] in seen:
                    continue
                seen[ad["id"]] = now_iso()
                if first_run:
                    continue
                # No matches() filter — the LBC alert criteria already
                # filtered server-side. We trust whatever LBC sent us.
                ad["search_name"] = "Leboncoin alert"
                # Email ads aren't tied to a specific SEARCHES bucket so
                # we use a shared "lbc_email" price history bucket.
                _annotate_deal(ad, prices)
                if ad.get("price"):
                    record_price(prices, "Leboncoin alert", ad["price"])
                new_ads.append(ad)
        except Exception as e:
            log(f"[!] lbc_email threw: {e}")
            run_stats["sources_failed"].append(f"lbc_email: {type(e).__name__}")

    return new_ads, run_stats


def format_ad(ad):
    price = f"{ad['price']}€" if ad.get("price") else "prix ?"
    loc   = f" — {ad['location']}" if ad.get("location") else ""

    # Deal scoring banner
    banner = ""
    median = ad.get("median_price")
    deal_pct = ad.get("deal_pct")
    if median and deal_pct is not None:
        if deal_pct >= DEAL_THRESHOLD_PCT:
            banner = (f"🔥 <b>BONNE AFFAIRE — {deal_pct}% sous la médiane</b>"
                      f" (médiane {int(median)}€)\n")
        elif deal_pct <= -DEAL_THRESHOLD_PCT:
            banner = (f"💸 Au-dessus du marché ({-deal_pct}% > médiane "
                      f"{int(median)}€)\n")
        else:
            banner = f"📊 Prix conforme au marché (médiane {int(median)}€)\n"

    return (banner +
            f"🚴 <b>{ad['search_name']}</b>\n"
            f"{ad['title']}\n"
            f"{price}{loc} · <i>{ad['source']}</i>\n"
            f"{ad['url']}")


# ---------- weekly heartbeat ----------
def _build_heartbeat_message(stats, seen, prices):
    """Compose the weekly summary text (HTML)."""
    runs = stats.get("runs", [])
    if not runs:
        return ("🩺 <b>Bike-alert weekly heartbeat</b>\n"
                "Aucun run enregistré cette semaine.")

    total_runs    = len(runs)
    total_new     = sum(r.get("new_ads", 0)   for r in runs)
    total_found   = sum(r.get("total_found", 0) for r in runs)
    failed_runs   = [r for r in runs if r.get("sources_failed")]
    err_summary = {}
    for r in failed_runs:
        for e in r.get("sources_failed", []):
            err_summary[e] = err_summary.get(e, 0) + 1

    sources_seen = set()
    for r in runs:
        for s in r.get("sources_ok", []):
            sources_seen.add(s)

    # Median per search (skip those with too few samples)
    median_lines = []
    for search in SEARCHES:
        m = get_median_price(prices, search["name"])
        n = len(prices.get(search["name"], []))
        if m:
            median_lines.append(f"  • {search['name']}: <b>{int(m)}€</b> "
                                f"<i>(n={n})</i>")
    if not median_lines:
        median_lines = ["  <i>Pas encore assez d'historique de prix.</i>"]

    err_lines = ([f"  • {e}: {n}×" for e, n in err_summary.items()]
                 if err_summary else ["  ✅ Aucune erreur"])

    return (
        f"🩺 <b>Bike-alert — récap hebdo</b>\n"
        f"\n"
        f"<b>Activité (7j)</b>\n"
        f"  • {total_runs} runs · {total_found} annonces vues · "
        f"{total_new} nouvelles\n"
        f"  • Sources actives: {', '.join(sorted(sources_seen)) or '—'}\n"
        f"  • Annonces en mémoire: {len(seen)}\n"
        f"\n"
        f"<b>Erreurs</b>\n"
        + "\n".join(err_lines) + "\n"
        f"\n"
        f"<b>Médianes prix</b>\n"
        + "\n".join(median_lines)
    )


def maybe_send_heartbeat(stats, seen, prices):
    """
    Send a weekly summary on Sundays (8-12h UTC), at most once per 6 days.
    Modifies stats['last_heartbeat'] on send.
    """
    now = datetime.now()
    if now.weekday() != 6:  # Sunday = 6
        return
    if not (8 <= now.hour < 12):
        return
    last = _parse_iso(stats.get("last_heartbeat"))
    if last and (now - last).days < 6:
        return
    msg = _build_heartbeat_message(stats, seen, prices)
    if send_telegram(msg):
        stats["last_heartbeat"] = now_iso()
        log("[*] Weekly heartbeat sent.")


def run_tick(seen):
    """Execute one check cycle. Returns (new_ads, first_run_flag)."""
    first_run = len(seen) == 0
    prices = load_prices()
    stats  = load_stats()

    new_ads, run_stats = check_once(seen, first_run, prices)

    # Persist state
    save_seen(seen)
    save_prices(prices)

    # Record this tick in the rolling history
    stats.setdefault("runs", []).append({
        "ts":             now_iso(),
        "new_ads":        len(new_ads),
        "total_found":    run_stats["total_found"],
        "sources_ok":     run_stats["sources_ok"],
        "sources_failed": run_stats["sources_failed"],
        "total_seen":     len(seen),
    })

    if first_run:
        log(f"First run — seeded {len(seen)} existing ads silently.")
    else:
        for ad in new_ads:
            tag = ""
            if ad.get("deal_pct") is not None and ad["deal_pct"] >= DEAL_THRESHOLD_PCT:
                tag = f" 🔥-{ad['deal_pct']}%"
            log(f"[+] NEW: {ad['source']} | {ad['title']}{tag}")
            notify(ad)
        log(f"Tick: {len(new_ads)} new · {len(seen)} total seen · "
            f"{run_stats['total_found']} found · "
            f"failures: {run_stats['sources_failed'] or 'none'}")

    # Heartbeat decision (no-op outside the Sunday window)
    maybe_send_heartbeat(stats, seen, prices)

    save_stats(stats)
    return new_ads, first_run


def main_loop():
    log(f"Bike alert started (loop mode). Sources: {', '.join(ENABLED_SOURCES)}. "
        f"Poll every {POLL_INTERVAL}s.")
    seen = load_seen()
    while True:
        try:
            run_tick(seen)
        except KeyboardInterrupt:
            log("Stopped by user.")
            break
        except Exception as e:
            log(f"[!] Loop error: {e}")
        time.sleep(POLL_INTERVAL)


def main_once():
    log(f"Bike alert (one-shot). Sources: {', '.join(ENABLED_SOURCES)}.")
    seen = load_seen()
    run_tick(seen)


def main_heartbeat():
    """Force-send the heartbeat now (manual trigger / debugging)."""
    log("Forcing heartbeat send.")
    seen   = load_seen()
    prices = load_prices()
    stats  = load_stats()
    msg = _build_heartbeat_message(stats, seen, prices)
    if send_telegram(msg):
        stats["last_heartbeat"] = now_iso()
        save_stats(stats)
        log("[*] Heartbeat forced & sent.")
    else:
        log("[!] Heartbeat send failed.")


if __name__ == "__main__":
    if "--heartbeat" in sys.argv:
        main_heartbeat()
    elif "--once" in sys.argv:
        main_once()
    else:
        main_loop()
