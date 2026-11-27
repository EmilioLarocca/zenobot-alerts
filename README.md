# zenobot_alerts

A 24/7 alert bot for Solana tokens and Meteora DLMM positions. You control it from **Telegram** and from a **web dashboard**. Both read and write the same settings, so a change in one shows up in the other within a few seconds.

| Module | What it does |
|---|---|
| 📡 **A: Global Volume Scanner** | Every 10 seconds it checks Jupiter's top-trending list (exactly like your previous bot) plus the top-traded list for your timeframe, with a built-in check that no token above your threshold is missed. Alerts when volume (5m / 1h / 24h), market cap range and total fees (SOL, checked for each token) match your filters. Has a cooldown per token and a blacklist. **⭐ Set as default** saves your settings; **↩ Restore default** puts them back after experimenting. |
| 🎯 **B: Token Alerts** | Watches specific tokens for a Volume, Price (USD or SOL) or Market-cap threshold. Sends **"Special Alert Hit!"** when it is reached. |
| 🚪 **C: Exit Alerts** | Watches RSI and/or Bollinger Bands on a **live** (still-open) candle every 10 seconds: 1m / 5m / 15m / 1h / 4h. The candle history comes from Jupiter's chart data in USD (the same candles as the chart on jup.ag, all pools combined), with GeckoTerminal as backup. The live candle is built from Jupiter prices. Alerts instantly, then reminds you every 5 minutes while the condition holds. Turns itself off after the duration you choose. **⭐ Set as default** makes new exit alerts (dashboard and Telegram) start with your settings. Every alert message has a **❌ Cancel this alert** button. In Telegram the list shows each alert by ticker with its condition (no numbers). |

Every time you switch something **ON or OFF**, from Telegram or the dashboard, the bot sends you a Telegram message confirming it. That covers the scanner, a module, or a single alert.

**Hosting:** a **Hetzner CX23** server (2 vCPU, 4 GB RAM, 40 GB disk) at about **€7.31/month** including the IPv4 address and Italian VAT. All data APIs are free.

---

## 🚀 Deployment on Hetzner (no coding needed, about 30 minutes)

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
2. Click **+** (top right) → **New repository**. Name it `zenobot_alerts`, choose **Public**, then click **Create repository**.
   - Public is fine: the code contains no secrets. Your token and password are stored only on your server.
3. On the next page, click **"uploading an existing file"**.
4. Unzip `zenobot_alerts.zip` on your computer and open the unzipped folder. Select **everything inside it** (the `app` and `tests` folders and all the files) and drag it into the GitHub page.
5. Wait until every file is listed, then click **Commit changes**.
6. Check that `install.sh`, `docker-compose.yml`, `Caddyfile`, `Dockerfile` and the `app` folder are all on the repository's main page.

### Step 4: Buy the Hetzner server
1. Sign up at **hetzner.com/cloud** and verify your account. If asked, pay the €20 prepayment through PayPal or verify with your ID.
2. In the **Cloud Console**, create a project, then click **Add Server**:

| Setting | Choose |
|---|---|
| Location | **Nuremberg** or **Falkenstein** |
| Image | **Ubuntu 24.04** (or 26.04). Pick the plain OS image, *not* the Docker app |
| Type | **CX23** |
| Networking | **Public IPv4** ON, **IPv6** ON |
| SSH keys | leave deselected. You'll get a root password by email |
| Volumes, Firewalls, Backups, Placement groups, Labels, Cloud config | leave all deselected |
| Name | `zenobot` |

3. Click **Create & Buy now**. Within a minute Hetzner emails you the server's **IP address** and **root password**.

**Paying:** Hetzner doesn't charge PayPal automatically. Each month you receive an invoice, and you pay it yourself at **accounts.hetzner.com** → Invoices → PayPal.

### Step 5: Connect to the server
- **Easiest:** in the Hetzner Cloud Console, open your server and click **Console** (the `>_` icon). A terminal opens in your browser. Log in as `root` with the emailed password.
- **Or from your computer:** open **Terminal** (Mac) or **PowerShell** (Windows) and type `ssh root@YOUR.SERVER.IP`. Answer `yes`, then paste the password. Nothing appears while you paste the password; that's normal.
- The first time, the server may ask you to choose a new password. Type the old one once, then the new one twice.

### Step 6: Run the installer
Replace `YOURNAME` with your GitHub username and paste:
```
curl -fsSL https://raw.githubusercontent.com/YOURNAME/zenobot_alerts/main/install.sh | sudo bash
```
It asks for:
1. your repository URL, for example `https://github.com/YOURNAME/zenobot_alerts`
2. your Telegram token (hidden while you type)
3. your Chat ID
4. a dashboard password (press Enter to have one generated)
5. your **GMGN API key** (recommended, see below; hidden while you type)
6. a Jupiter key (optional, press Enter to skip)
7. the dashboard address: press **Enter** to accept the suggestion

The suggested address looks like `https://203-0-113-7.sslip.io`. It's a free address made from your server's IP, with a real HTTPS certificate.

The first build takes about 5 minutes. Then you get **"✅ zenobot_alerts is online"** on Telegram.

### GMGN API key (recommended)
The GMGN key is used for one thing only: a **backup** for each token's total fees, used when Metlex doesn't answer.

Volume, prices and candles come from Jupiter and GeckoTerminal. A read-only key is enough, with trading disabled.
1. Create the key at **gmgn.ai/ai**.
2. If your key has an **IP whitelist**, add your server's IP (run `zenobot ip` on the server to see it).
3. To add or change the key later, run `zenobot edit` and fill in `GMGN_API_KEY=`.
4. Check it works with `zenobot test`.

Without a key, or when GMGN doesn't answer, everything still works:
- the scanner works the same (it finds tokens with Jupiter)
- fees still come from **Metlex**
- if Metlex doesn't answer either, the alert shows "Total fees: n/a" and is still sent

### Step 7: Open the dashboard
Open the address the installer printed. Log in with username **`admin`** and your password. Send `/dashboard` to the bot at any time to get the link again, and run `zenobot password` on the server to see the password.

**Done.** The bot runs 24/7 and restarts by itself after crashes or server reboots.

---

## 🔄 Updating the bot (new files from Claude, or your own edits)

### A. Put the new files on GitHub
**Option 1: upload a new zip.**
1. Unzip the new `zenobot_alerts.zip` on your computer.
2. Open your repository on github.com → **Add file** → **Upload files**.
3. Open the unzipped folder, select **everything inside it** and drag it onto the page. Files with the same name are **replaced** with the new version. Folders like `app` are matched too.
4. Wait for the upload list to finish, then click **Commit changes**.

**Option 2: change a single file.**
1. Open the file on github.com and click the **pencil icon** (Edit).
2. Make your change and click **Commit changes**.

To delete a file you no longer need, open it → **⋯** menu → **Delete file** → Commit. Uploading never deletes old files by itself.

### B. Apply the update on the server
Connect to the server (Step 5) and run:
```
zenobot update
```
It downloads the new files from GitHub, rebuilds and restarts the bot. Your alerts and settings are kept. It takes 1–5 minutes.

### Server commands

| Command | What it does |
|---|---|
| `zenobot update` | get the latest files from GitHub and restart |
| `zenobot logs` | live logs (Ctrl+C to exit) |
| `zenobot edit` | change token, chat id or password (save with Ctrl+O, Enter, Ctrl+X) |
| `zenobot password` | show the dashboard password |
| `zenobot test` | check the data sources (Jupiter, Jupiter charts, GeckoTerminal, Metlex, GMGN) |
| `zenobot scan` | run one scan without sending alerts and explain why tokens pass or are skipped |
| `zenobot ip` | show the server's IP (for the GMGN whitelist) |
| `zenobot status` | check it's running |
| `zenobot restart` | restart the bot |

---

## 📱 Using it

**Telegram:** send `/settings` or `/start`.
- Tap a module to see its status. **Configure** opens its settings.
- Some buttons ask you to type a value, for example *"Send the minimum volume…"*. You can use shortcuts: `50k`, `1.5m`, `12h`, `3d`. Send `/cancel` to stop.
- **New alerts:** tap ➕ and send the contract address. Adjust the settings on the card, then tap **▶️ Activate**.
- `/status` shows whether Jupiter, Jupiter charts, GeckoTerminal, Meteora and Metlex are responding.

**Web dashboard:** the same settings arranged in tabs. It refreshes every 3 seconds. The pills at the top show data-source health.

### How alerts behave
- **Scanner:** a token alerts at most once per *cooldown* period. Blacklisted tokens, SOL, USDC and USDT are always ignored. At most 8 alerts are sent per scan, so a market-wide pump can't flood you.
- **Token alerts fire once per crossing.** After firing, an alert "re-arms" once the condition is no longer true.
- **Exit alerts are real-time.** They're checked every 10 seconds on the live candle, and alert as soon as the condition is met. The candle history is downloaded from Jupiter's chart data when the alert starts and refreshed every 2 minutes (every 5 minutes if it had to use the GeckoTerminal backup). Between refreshes, each 10-second Jupiter price updates the running candle and starts new candles on time. While it stays met, you get a **reminder every 5 minutes** (`REMINDER_MINUTES` in `app/modules/exit_alerts.py`). There are no extra confirmation filters. The only limit is mathematical: a brand-new token needs at least as many candles as the indicator length (for example 15 candles for RSI 14).
- **RSI + BB together:** "ALL must hit" (default) needs both indicators. "ANY can hit" needs only one. If both indicators are OFF, the alert won't switch on and you get a warning.
- **Bollinger option:** fill **either** "Min above Middle Band %" **or** "Min above Upper Band %". `0` means touching or above the band; `2` means at least 2% above it.
- **Exit alert durations:** when the time runs out, you get a "monitoring ended" message. Changing the duration of a running alert restarts the countdown from now.

---

## 🔌 Data sources and their limits (good to know)

| Need | Source | Notes |
|---|---|---|
| Finding tokens + volume, market cap, % change | Jupiter Tokens V2, every 10 s: **top trending** (read through Metlex, the same URL as your previous bot) and **top traded** (read directly from Jupiter), 100 tokens each | If one route fails, the list is read through the other. Splitting the two lists keeps each route at 6 calls/min, far below the limits (Jupiter free key: 60/min). Every scan checks that the list is complete (see "Coverage" below). |
| 5m/1h/24h volume, price change, market cap | Jupiter Tokens API V2 | The same source for every token, so all alerts are comparable. |
| **Total fees** (SOL) | **Metlex** `total_fee`, the same as your previous bot, with **GMGN** as backup | Looked up for each token that passed the other filters, up to 20 per scan. Cached for 3 minutes. |
| Live price, 24h change | Jupiter Price API V3 | One batched request every 10 seconds for all exit alerts. Falls back to GeckoTerminal. |
| Candle history for RSI/BB | **Jupiter chart data** (USD, all pools combined, same as jup.ag), then GeckoTerminal, then Meteora DLMM | Refreshed every 2 minutes per alert (15 exit alerts ≈ 7 calls/min). The Jupiter chart address is internal to jup.ag, not part of Jupiter's public API, so it can change without notice: if it fails, the bot switches that alert to GeckoTerminal automatically. Its health shows as "Jupiter charts" on the dashboard and in `/status`. The live candle is built from Jupiter prices. |

- **Rate limiting:** each API has its own token bucket, and each alert module has a budget of 15 calls/min to Jupiter and GeckoTerminal. GMGN is only called for fee backups (`GMGN_RPS`, default 2/s). After an HTTP 429 the bot pauses that API automatically.
- **Why no GeckoTerminal API key?** It isn't needed: building the live candle from Jupiter prices keeps GeckoTerminal usage low.
- **Total fees** come from Metlex's token info, field `total_fee`, exactly as in your previous bot, with GMGN as backup. It works for **any** token. Set the scanner minimum **in SOL**. Alerts show SOL plus the approximate dollar value.
- **Coverage:** Jupiter's "top traded" list is the 100 tokens with the most volume in your timeframe, measured across all Solana DEXes that Jupiter routes through (Meteora, Raydium, Orca, Pump.fun AMM and more). If the 100th token is below your threshold, then **every** token above your threshold is in the list, so nothing is missed. The bot checks this on every scan, and `zenobot scan` prints it. With a $70K/5m threshold the list practically always reaches below it; in an extremely hot market where 100+ tokens are above $70K at once, the log shows a warning.
- **1m volume** was removed from the scanner, which now offers only 5m / 1h / 24h.


---

## 🛠 Troubleshooting

| Problem | Fix |
|---|---|
| No "online" message on Telegram | Make sure you pressed **Start** in your bot. Run `zenobot logs` to see the error. Run `zenobot edit` to fix the token or Chat ID. |
| Bot replies "⛔ This bot is private" | `CHAT_ID` doesn't match your Telegram id. Ask @userinfobot again, then run `zenobot edit`. |
| Dashboard doesn't open | Wait 2 minutes after installing; the HTTPS certificate takes a moment. Use exactly the `https://…sslip.io` address. Check `zenobot status`. |
| Dashboard keeps asking for the password | Username `admin`. Run `zenobot password` to see the password. |
| `zenobot update` says "not possible to fast-forward" | Run `cd /opt/zenobot_alerts && git fetch && git reset --hard origin/main`, then `zenobot update`. This keeps your settings. |
| Installer says "Could not download the repository" | The repository is private or the address is wrong. Make it public, or see "Private repository" above. |
| Exit alert shows ⚠️ "only N candles of history" | The token is too new for your RSI/BB lengths. Use a smaller timeframe or shorter lengths, or wait. |
| Many "429 rate limited" in `/status` | Normal from time to time. For GMGN, wait 5 minutes; if it keeps happening, set `GMGN_RPS=1` with `zenobot edit`. |
| `zenobot test` says `AUTH_IP_BLOCKED` | Add the server IP (`zenobot ip`) to your GMGN key's IP whitelist on gmgn.ai. |

---

## ☁️ Other hosting options (not needed if you use Hetzner)

The same files also work on these services. Steps 1–3 above are the same for all of them.

- **Any other Ubuntu VPS** (OVHcloud, IONOS, Contabo, Hostinger…): run the installer from Step 6. It works on Ubuntu 22.04, 24.04 and 26.04 with at least 1 GB of RAM, and adds swap automatically on 1 GB servers.
- **Railway** ($5/month, needs a real credit or debit card):
  1. New Project → Deploy from GitHub repo.
  2. Variables: `TELEGRAM_BOT_TOKEN`, `CHAT_ID`, `DASHBOARD_PASSWORD`, `PORT=8080`.
  3. Attach a Volume at `/data`.
  4. Settings → Networking → Generate Domain (port 8080).
- **Fly.io** (about $4–5/month, prepaid cards can buy credits, minimum $25):
  1. Dashboard → Launch an App from your GitHub repository. `fly.toml` sets everything up.
  2. Add the same values as Secrets, then Deploy.

**Private repository instead of public:**
1. Create a read-only token on GitHub: Settings → Developer settings → Fine-grained tokens → this repo only, "Contents: Read".
2. On the server, instead of Step 6, run:
   ```
   git clone https://x-access-token:YOUR_TOKEN@github.com/YOURNAME/zenobot_alerts.git /opt/zenobot_alerts && bash /opt/zenobot_alerts/install.sh
   ```

---

## 🗂 Project structure

```
zenobot_alerts/
├── main.py                  starts the Telegram bot, web dashboard and 3 alert loops together
├── requirements.txt         exact library versions
├── Dockerfile               how the app is built (Python 3.12)
├── railway.json             (only for Railway hosting)
├── fly.toml                 (only for Fly.io hosting)
├── install.sh               one-command installer for Hetzner / any Ubuntu VPS
├── scripts/zenobot          the "zenobot" server command (update, logs, test…)
├── docker-compose.yml       VPS setup: bot + Caddy (automatic HTTPS)
├── Caddyfile                HTTPS web server config for the dashboard
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
│   ├── diag.py              "zenobot test": checks the GMGN key and data sources
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
To change any of these, edit the file on GitHub (pencil icon → Commit), then run `zenobot update` on the server.

- **Scan frequency or max alerts per scan:** the top of `app/modules/scanner.py`, `INTERVAL_S` and `MAX_ALERTS_PER_SCAN`.
- **Exit-alert check speed and reminder interval:** the top of `app/modules/exit_alerts.py`, `INTERVAL_S` (10 s) and `REMINDER_MINUTES` (5).
- **Default scanner values:** `DEFAULT_SCANNER` in `app/db.py`.
- **Alert message wording:** `app/formatting.py`.

### Optional variables
Add these with `zenobot edit`, one per line like `GMGN_RPS=3`: `GMGN_API_KEY`, `GMGN_RPS` (default 2), `JUPITER_API_KEY`, `GECKO_RPM` (default 8), `JUPITER_RPM` (24 without a key, 50 with one), `MODULE_RPM` (15), `METEORA_RPS` (5).


*Not financial advice. Alerts depend on third-party data that can be delayed or wrong. Always double-check before acting.*
