# Setup Leboncoin email parsing

This pipeline lets GitHub Actions catch new Leboncoin ads even though
LBC blocks scraping from cloud IPs. The trick: LBC's own native alert
emails do the searching for us, and we just parse the inbox.

## Step 1 — Create alerts on Leboncoin (~5 min)

For each bike you want to track, do this on https://www.leboncoin.fr:

1. Type the search in the search bar (e.g. `cube attain c:62 race`)
2. Apply filters in the left sidebar:
   - Categorie: Vélos
   - Prix max: your budget
   - Localisation: France entière (or your region)
3. Click **"Créer une alerte"** at the top of the results
4. Choose the **email** delivery option
5. Repeat for the other 6 bikes:
   - canyon roadlite cf 7
   - canyon ultimate cf sl di2
   - canyon endurace cf di2
   - van rysel rcr pro
   - specialized tarmac sl7 di2
   - trek domane sl di2

You can also create broader alerts like just `cube attain` and let the
script's filter handle it.

## Step 2 — Generate a Gmail App Password (~2 min)

1. Open https://myaccount.google.com/security and make sure
   **2-Step Verification** is ON. If not, enable it first
   (5 min, requires your phone).
2. Go to https://myaccount.google.com/apppasswords
3. Name the app "bike-alert" → Generate
4. Copy the 16-character password (looks like `abcd efgh ijkl mnop`)

## Step 3 — Add secrets to GitHub

On your repo: Settings → Secrets and variables → Actions → "New repository secret"

Add:
- `GMAIL_USER` = your full Gmail address (e.g. `lopeshugo1310@gmail.com`)
- `GMAIL_APP_PASSWORD` = the App Password from step 2

## Step 4 — Push and trigger

```bash
cd ~/PycharmProjects/PythonProject/bike_alert
git add .
git commit -m "add Gmail-based Leboncoin alert parsing"
git pull --rebase
git push
```

Then on github.com → Actions tab → "bike-alert" → "Run workflow".

## How it works

Every 5 minutes, the GitHub Action:
1. Queries Vinted (catalog API, no anti-bot)
2. Queries eBay (HTML scrape, no anti-bot)
3. Connects to your Gmail via IMAP, finds emails received in the last
   24h whose sender contains `leboncoin.fr`, parses them for ad URLs
4. Marks those emails as "read" so they're not reprocessed
5. Compares against `seen_ads.json`, sends Telegram for genuinely new ones

LBC alert emails typically arrive within 5-30 minutes of an ad being
posted, depending on LBC's batching. The script sees the email at most
5 minutes after it lands in your inbox.

## Tuning

- `GMAIL_LBC_FROM` (default `leboncoin.fr`): override if alerts come from
  a different sender domain in your locale.
- `GMAIL_LOOKBACK_HOURS` (default `24`): how far back to scan on each run.
  Lower = faster but riskier if a run is missed; higher = safer.

## Troubleshooting

- **"lbc_email skipped: GMAIL_USER/GMAIL_APP_PASSWORD not set"**:
  GitHub secrets aren't being read. Double-check the names.
- **"lbc_email error: ... AUTHENTICATIONFAILED"**:
  Your App Password is wrong. Regenerate one and update the secret.
- **"0 LBC email(s) in last 24h"** but you DID get an email:
  Check the email's actual `From:` header — if LBC uses a different
  sender domain in France (e.g. `leboncoin-mail.com`), set
  `GMAIL_LBC_FROM` accordingly.
- **Same ad pinged twice**: shouldn't happen because we dedupe by URL,
  but if it does, delete `seen_ads.json` from the repo and let the
  next run re-seed.
