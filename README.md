# zenobot_alerts

A 24/7 alert bot for Solana tokens and Meteora DLMM positions. You control it from **Telegram** and from a **web dashboard**. Both read and write the same settings, so a change in one shows up in the other within a few seconds.

| Module | What it does |
|---|---|
| 📡 **A: Global Volume Scanner** | Scans trending and new Solana tokens every minute. Alerts when volume (1m / 5m / 1h / 24h), market cap range and total fees match your filters. Has a cooldown per token and a blacklist. |
| 🎯 **B: Token Alerts** | Watches specific tokens for a Volume, Price (USD or SOL) or Market-cap threshold. Sends **"Special Alert Hit!"** when it is reached. |
| 🚪 **C: Exit Alerts** | Watches RSI and/or Bollinger Bands on a **live** (still-open) candle: 1m / 5m / 15m / 1h / 4h. Turns itself off after the duration you choose. |

**Cost:** Railway Hobby plan, **$5/month**. That includes $5 of usage, which this bot normally stays under. All data APIs used are free.

---

## 🚀 Deployment (no coding needed, about 20 minutes)

### Step 1: Create the Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`.
2. **Name:** `zenobot_alerts`
3. **Username:** must end in `bot`, for example `zenobot_alerts_bot`. If it's taken, try `zenobot_alerts_xyz_bot`.
4. BotFather replies with a **token** like `7123456789:AAH...`. Copy it somewhere safe. Never share it.
5. Open your new bot and press **Start**. A bot cannot message you until you've done this.

### Step 2: Get your Chat ID
1. In Telegram, open **@userinfobot** and press **Start**.
2. It replies with your **Id**, a number like `123456789`. Copy it.

### Step 3: Put the code on GitHub
1. Create a free account at **github.com** if you don't have one.
2. Click **+** (top right) → **New repository** → name it `zenobot_alerts` → choose **Private** → **Create repository**.
3. On the next page, click **"uploading an existing file"**.
4. Unzip `zenobot_alerts.zip` on your computer. Open the unzipped folder, select **everything inside it** (the `app` and `tests` folders and all files) and drag it into the GitHub page.
   - Check that `Dockerfile`, `main.py`, `requirements.txt`, `railway.json` and the `app` folder are all in the list.
5. Click **Commit changes**.

### Step 4: Deploy on Railway
1. Go to **railway.com** → **Login with GitHub**.
2. Upgrade to the **Hobby plan** ($5/month): click your avatar → **Account / Plans**. The free trial also works for testing.
3. Click **New Project** → **Deploy from GitHub repo** → allow access if asked → pick **zenobot_alerts**.
4. Railway starts building. The first build takes about 3–5 minutes. It may fail once because the settings in the next step are missing. That's normal.

### Step 5: Add your settings (Variables)
1. Click the service box → **Variables** tab → **Raw Editor**, and paste the following with your own values:
   ```
   TELEGRAM_BOT_TOKEN=paste-your-BotFather-token
   CHAT_ID=paste-your-id-number
   DASHBOARD_PASSWORD=choose-a-long-password
   PORT=8080
   ```
2. Optional: add `JUPITER_API_KEY=...`. It's a free key from **portal.jup.ag** and doubles the Jupiter request speed. The bot works without it.
3. Click **Update Variables**, then **Deploy** if Railway asks.

### Step 6: Add a Volume (keeps your settings between deploys) ⚠️ important
1. In the project canvas, **right-click the service** → **Attach Volume**. You can also press `Ctrl/⌘ + K` and type "volume".
2. Set the **Mount path** to `/data` and click **Add**.

If you skip this step, your alerts and settings are erased every time Railway redeploys.

### Step 7: Get the dashboard address
1. Service → **Settings** → **Networking** → **Generate Domain**. When asked for a port, enter **8080**.
2. You get an address like `https://zenobot-alerts-production.up.railway.app`. Open it:
   - **Username:** `admin`
   - **Password:** your `DASHBOARD_PASSWORD`
3. The bot sends you **"✅ zenobot_alerts is online"** on Telegram after each deploy. Send `/dashboard` at any time to get the link again.

**Done.** Railway redeploys automatically every time you change a file on GitHub.

---

## 📱 Using it

**Telegram:** send `/settings` or `/start`.
- Tap a module to see its status. **Configure** opens its settings.
- Some buttons ask you to type a value, for example *"Send the minimum volume…"*. You can use shortcuts: `50k`, `1.5m`, `12h`, `3d`. Send `/cancel` to stop.
- **New alerts:** tap ➕ and send the contract address. Adjust the settings on the card, then tap **▶️ Activate**.
- `/status` shows whether Jupiter, GeckoTerminal and Meteora are responding.

**Web dashboard:** the same settings arranged in tabs. It refreshes every 3 seconds. The pills at the top show data-source health.

### How alerts behave
- **Scanner:** a token alerts at most once per *cooldown* period. Blacklisted tokens, SOL, USDC and USDT are always ignored. At most 8 alerts are sent per scan, so a market-wide pump can't flood you.
- **Token and Exit alerts fire once per crossing.** After firing, an alert "re-arms" once the condition is no longer true. You get one message per move, not one every 30 seconds.
- **Exit alerts and false signals:**
  - A signal must be seen on **2 checks in a row**, at least 20 seconds apart.
  - The candle data must be fresh, and there must be enough history for your indicator lengths.
  - The live price must agree with the candles.
- **RSI + BB together:** "ALL must hit" (default) needs both indicators. "ANY can hit" needs only one. If both indicators are OFF, the alert won't switch on and you get a warning.
- **Bollinger option:** fill **either** "Min above Middle Band %" **or** "Min above Upper Band %". `0` means touching or above the band; `2` means at least 2% above it.
- **Exit alert durations:** when the time runs out, you get a "monitoring ended" message. Changing the duration of a running alert restarts the countdown from now.

---

## 🔌 Data sources and their limits (good to know)

| Need | Source | Notes |
|---|---|---|
| Live price, 24h change | Jupiter Price API V3 | Batches of 50 tokens per request. Falls back to GeckoTerminal if Jupiter fails. |
| Market cap, 5m/1h/24h volume, logo, trending lists | Jupiter Tokens API V2 | Batches of up to 100 tokens per request. |
| Candles for RSI/BB, 1-minute volume | GeckoTerminal | Free, but the no-key limit is only about **5–10 calls/min**. The bot caches data and limits itself to 8/min. |
| Lifetime DLMM pool fees, backup candles | Meteora DLMM API | 30 requests/sec, no key needed. Backup candles only exist for 5m/1h/4h. |

- **Rate limiting:** each API has a token bucket, and each module has its own budget of **15 calls/min** to Jupiter and GeckoTerminal (`MODULE_RPM`). After an HTTP 429 the bot pauses that API automatically.
- **Caching:** prices are cached for about 20 seconds. Candles are cached for 30–120 seconds depending on the timeframe. Trending lists are cached for 45 seconds to 5 minutes.
- **"Total fees":** GMGN has no public API. The bot shows Jupiter's `fees` field (USD) and also the lifetime fees of the token's **Meteora DLMM pools**. In the scanner you can choose which one the "Min fees" filter uses.
- **1m volume** isn't published by Jupiter. For the 1m timeframe the scanner first filters by 5m volume, then checks real 1m candles for the top 3 candidates per scan.
- **How many exit alerts can run?** With the free GeckoTerminal limit, about **5–8 active exit alerts** stay fully fresh. More still work, but each one updates less often.
- **Meteora backup candles:** if GeckoTerminal is down, the bot uses Meteora candles, rescaled to the live USD price. The alert message says so when this happens.

---

## 🛠 Troubleshooting

| Problem | Fix |
|---|---|
| No "online" message on Telegram | Check `TELEGRAM_BOT_TOKEN` and `CHAT_ID` in Variables. Make sure you pressed **Start** in your bot. Check Railway → **Deployments** → **View logs**. |
| Bot replies "⛔ This bot is private" | `CHAT_ID` doesn't match your Telegram id. Ask @userinfobot again. |
| Settings disappear after an update | You need the Volume at `/data` (Step 6). |
| Dashboard keeps asking for the password | Username `admin`, password = `DASHBOARD_PASSWORD`. Changing the variable needs a redeploy. |
| Exit alert shows ⚠️ "only N candles of history" | The token is too new for your RSI/BB lengths. Use a smaller timeframe or shorter lengths, or wait. |
| Many "429 rate limited" in `/status` | Normal from time to time. Lower `GECKO_RPM` to 5 or reduce the number of exit alerts. |

---

## 🗂 Project structure

```
zenobot_alerts/
├── main.py                  starts the Telegram bot, web dashboard and 3 alert loops together
├── requirements.txt         exact library versions
├── Dockerfile               how Railway builds the app (Python 3.12)
├── railway.json             Railway settings: health check, auto-restart, 1 instance
├── .env.example             list of the settings (environment variables)
├── app/
│   ├── config.py            reads the environment variables
│   ├── db.py                SQLite database + DEFAULT settings (edit defaults here)
│   ├── services.py          validation rules shared by Telegram and the dashboard
│   ├── ratelimit.py         token-bucket rate limiter
│   ├── datasources.py       Jupiter / GeckoTerminal / Meteora clients, caching, fallbacks
│   ├── indicators.py        RSI + Bollinger (pandas-ta) on live candles
│   ├── formatting.py        alert message templates (edit wording here)
│   ├── notifier.py          sends messages to Telegram safely
│   ├── telegram_ui.py       Telegram menus, buttons and step-by-step questions
│   ├── context.py           shared objects
│   ├── modules/
│   │   ├── scanner.py       Module A
│   │   ├── token_alerts.py  Module B
│   │   └── exit_alerts.py   Module C
│   └── web/
│       ├── server.py        dashboard API + password protection
│       └── static/index.html the dashboard page
└── tests/test_offline.py    automatic tests with fake market data
```

### Easy tweaks
To change any of these, edit the file on GitHub (pencil icon → Commit). Railway redeploys by itself.

- **Scan frequency or max alerts per scan:** the top of `app/modules/scanner.py`, `INTERVAL_S` and `MAX_ALERTS_PER_SCAN`.
- **Exit-alert confirmation strictness:** the top of `app/modules/exit_alerts.py`, `CONFIRM_CHECKS` and `MAX_PRICE_GAP`.
- **Default scanner values:** `DEFAULT_SCANNER` in `app/db.py`.
- **Alert message wording:** `app/formatting.py`.

### Optional variables
`JUPITER_API_KEY`, `GECKO_RPM` (default 8), `JUPITER_RPM` (24 without a key, 50 with one), `MODULE_RPM` (15), `METEORA_RPS` (5), `DATA_DIR`.

### Alternative hosting: any VPS with Docker
```
docker build -t zenobot .
docker run -d --restart always -p 80:8080 -v zenobot-data:/data \
  -e TELEGRAM_BOT_TOKEN=... -e CHAT_ID=... -e DASHBOARD_PASSWORD=... zenobot
```

*Not financial advice. Alerts depend on third-party data that can be delayed or wrong. Always double-check before acting.*
