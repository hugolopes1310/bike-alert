# Running bike_alert on GitHub Actions (always-on)

The bot runs every 5 minutes on a GitHub-hosted runner, commits its
state (`seen_ads.json`) back to the repo between runs, and pings you
on Telegram when a new matching ad appears.

## Setup (one-time)

### 1. Create a GitHub repo

Recommended: **public** repo — GitHub Actions is free and unlimited
on public repos. On private repos you only get 2000 minutes/month,
which won't cover a 5-minute cadence.

From this folder:

```bash
cd ~/PycharmProjects/PythonProject/bike_alert
git init
git add .
git commit -m "initial bike_alert bot"
# create a repo on github.com first, then:
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/bike-alert.git
git push -u origin main
```

### 2. Add your Telegram credentials as GitHub Secrets

On your repo page on github.com:

1. Settings → Secrets and variables → Actions → "New repository secret"
2. Add `TELEGRAM_TOKEN` with the value from @BotFather
3. Add `TELEGRAM_CHAT_ID` with your chat ID

### 3. Enable Actions write permission

1. Settings → Actions → General → "Workflow permissions"
2. Select **"Read and write permissions"** (so the workflow can commit
   `seen_ads.json` back to the repo)
3. Save

### 4. Trigger the first run

Go to the **Actions** tab on your repo. You'll see the "bike-alert"
workflow listed. Click it → "Run workflow" → main → green button.

The first run will seed `seen_ads.json` silently (no notifications),
and every subsequent run (every 5 minutes) will only ping on genuinely
new ads.

## Notes & caveats

- **Cadence drift**: GitHub's cron is not precise — during high load
  the 5-minute schedule can drift to 10-15 min. That's normal.
- **DataDome on runners**: GitHub runner IPs are sometimes blacklisted
  by Leboncoin / Troc-Vélo. If you see 403s in the Actions logs:
  - Vinted and eBay will still work fine
  - Fall back to Leboncoin's native email alerts for those two
- **Commit noise**: every time state changes, a `chore: update
  seen_ads state` commit appears. The `[skip ci]` tag prevents it
  from triggering the workflow again.

## Monitoring

- **Actions tab**: click any run to see the log. Look for lines like
  `Tick: 0 new · 178 total seen` (healthy) or `LBC '...' → 403`
  (blocked by DataDome).
- **Disable**: Actions tab → bike-alert → "... " → Disable workflow.
- **Tune the cadence**: edit the `cron:` line in
  `.github/workflows/bike_alert.yml`. `"*/15 * * * *"` = every 15 min.

## Quota math (for reference)

- Free private minutes/month: 2000
- 5-min cadence × 1 min/run × 30 days = ~8640 min (× public = free)
- 15-min cadence = ~2880 min (still over 2000 on private)
- 30-min cadence = ~1440 min (fits in 2000 on private)

TL;DR: use a public repo.
