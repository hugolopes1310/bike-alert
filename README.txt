BIKE ALERT — Telegram bot that pings you on new bike listings
================================================================

Watches Leboncoin, eBay, Troc-Vélo and Vinted for specific bikes
and sends a Telegram message as soon as a new matching ad appears.


SETUP
-----

1) Create a Telegram bot
   - Open Telegram, start a chat with @BotFather
   - Send /newbot, follow the prompts
   - Copy the token BotFather gives you (looks like 123456:ABC...)

2) Get your chat_id
   - Send any message to your new bot
   - Open https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates in a browser
   - Look for "chat":{"id":123456789,...} — that number is your chat_id

3) Configure
   cp .env.example .env
   # edit .env and paste your token + chat_id

4) Install + run
   pip3 install -r requirements.txt
   python3 bike_alert.py


HOW IT BEHAVES
--------------

- Polls every 5 minutes (change POLL_INTERVAL in bike_alert.py)
- First run: scans current ads silently and saves them as "seen" so you
  don't get spammed with historical listings
- Subsequent runs: only pings you when something genuinely new shows up
- State is stored in seen_ads.json (delete it to reset)
- Logs go to bike_alert.log


TUNING
------

Edit SEARCHES at the top of bike_alert.py to add/remove bikes
or change max prices. The "size" field does a soft filter — an ad
without an explicit size mention still gets through.

To disable a source, remove it from ENABLED_SOURCES.


RUNNING IT CONTINUOUSLY
-----------------------

macOS (launchd): create ~/Library/LaunchAgents/com.user.bikealert.plist
Linux (systemd): create a user service
Cheap VPS or Raspberry Pi: screen / tmux / nohup also work

Or just run it in a terminal tab when you're actively shopping.


TROUBLESHOOTING
---------------

- "LBC ... → 403": Leboncoin's anti-bot (DataDome) is blocking your IP.
  Try running from a different network, or fall back to Leboncoin's
  built-in saved-search email alerts and parse them via IMAP/Gmail API.

- "Vinted ... → 401/403": Session cookie expired; the script retries
  once automatically. If it persists, Vinted may have rate-limited you.

- No Troc-Vélo results: make sure beautifulsoup4 is installed.

- Telegram not sending: double-check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
  in your .env, and make sure you sent at least one message TO your bot
  first (otherwise Telegram refuses to let the bot message you).
