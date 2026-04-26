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
import hashlib
import requests
from datetime import datetime
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

SEARCHES = [
    {
        "name": "Cube Attain C:62 Race (M)",
        "keywords": ["cube attain c:62 race", "cube attain c62 race",
                     "cube attain c 62"],
        "require_all": ["cube", "attain"],          # title must contain both
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

ENABLED_SOURCES = ["ebay", "vinted"]
# Note: leboncoin and trocvelo are disabled because their DataDome
# anti-bot blocks GitHub Actions IPs. Use Leboncoin's native email
# alerts instead (free, instant). Re-add them here if you ever run
# this on a residential IP (your own machine, home server, etc).
POLL_INTERVAL   = 300  # seconds between full scans

STATE_FILE = Path(__file__).parent / "seen_ads.json"
LOG_FILE   = Path(__file__).parent / "bike_alert.log"
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
def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    STATE_FILE.write_text(json.dumps(list(seen)))


# ---------- telegram ----------
def send_telegram(text):
    if "PUT_TOKEN" in TELEGRAM_TOKEN:
        log("[!] TELEGRAM_TOKEN not configured — printing instead")
        print(text)
        return
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
    except Exception as e:
        log(f"[!] Telegram error: {e}")


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
                results.append({
                    "source": "leboncoin",
                    "id":    f"lbc_{ad.get('list_id')}",
                    "title": ad.get("subject", ""),
                    "body":  ad.get("body", ""),
                    "price": price,
                    "url":   ad.get("url", ""),
                    "location": (ad.get("location") or {}).get("city", ""),
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
                results.append({
                    "source": "ebay",
                    "id":    f"ebay_{hashlib.md5(href.encode()).hexdigest()[:12]}",
                    "title": title, "body": "",
                    "price": price, "url": href, "location": "",
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
                results.append({
                    "source": "vinted",
                    "id":    f"vin_{item.get('id')}",
                    "title": item.get("title", ""),
                    "body":  item.get("description", "") or "",
                    "price": int(price) if price else 0,
                    "url":   item.get("url", ""),
                    "location": "",
                })
        except Exception as e:
            log(f"[!] Vinted error '{kw}': {e}")
    return results


# ---------- filter ----------
def matches(ad, search):
    """
    Multi-stage filter:
     1) all `require_all` tokens must appear in title+body (strict)
     2) if any accessory term appears AND no bike indicator, reject
     3) soft size filter: skip only if another size is explicitly mentioned
    """
    text = f"{ad['title']} {ad.get('body','')}".lower()

    # (1) strict model match
    for token in search.get("require_all", []):
        if token.lower() not in text:
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

def check_once(seen, first_run):
    new_ads = []
    for search in SEARCHES:
        found = []
        for src in ENABLED_SOURCES:
            fn = SEARCH_FUNCS.get(src)
            if not fn: continue
            try:
                found += fn(search["keywords"], search["max_price"])
            except Exception as e:
                log(f"[!] {src} threw: {e}")
        for ad in found:
            if ad["id"] in seen: continue
            seen.add(ad["id"])
            if first_run: continue
            if not matches(ad, search): continue
            ad["search_name"] = search["name"]
            new_ads.append(ad)
    return new_ads


def format_ad(ad):
    price = f"{ad['price']}€" if ad.get("price") else "prix ?"
    loc   = f" — {ad['location']}" if ad.get("location") else ""
    return (f"🚴 <b>{ad['search_name']}</b>\n"
            f"{ad['title']}\n"
            f"{price}{loc} · <i>{ad['source']}</i>\n"
            f"{ad['url']}")


def run_tick(seen):
    """Execute one check cycle. Returns (new_ads, first_run_flag)."""
    first_run = len(seen) == 0
    new_ads = check_once(seen, first_run)
    save_seen(seen)
    if first_run:
        log(f"First run — seeded {len(seen)} existing ads silently.")
    else:
        for ad in new_ads:
            log(f"[+] NEW: {ad['source']} | {ad['title']}")
            send_telegram(format_ad(ad))
        log(f"Tick: {len(new_ads)} new · {len(seen)} total seen.")
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


if __name__ == "__main__":
    if "--once" in sys.argv:
        main_once()
    else:
        main_loop()
