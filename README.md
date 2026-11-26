# zenobot_alerts

A 24/7 alert bot for Solana tokens and Meteora DLMM positions. You control it from **Telegram** and from a **web dashboard**. Both read and write the same settings, so a change in one shows up in the other within a few seconds.

| Module | What it does |
|---|---|
| 📡 **A: Global Volume Scanner** | Scans trending and new Solana tokens every minute. Alerts when volume (1m / 5m / 1h / 24h), market cap range and total fees match your filters. Has a cooldown per token and a blacklist. |
| 🎯 **B: Token Alerts** | Watches specific tokens for a Volume, Price (USD or SOL) or Market-cap threshold. Sends **"Special Alert Hit!"** when it is reached. |
| 🚪 **C: Exit Alerts** | Watches RSI and/or Bollinger Bands on a **live** (still-open) candle: 1m / 5m / 15m / 1h / 4h. Turns itself off after the duration you choose. |

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
5. a Jupiter key (optional, press Enter to skip)
6. the dashboard address: press **Enter** to accept the suggestion

The suggested address looks like `https://203-0-113-7.sslip.io`. It's a free address made from your server's IP, with a real HTTPS certificate.

The first build takes about 5 minutes. Then you get **"✅ zenobot_alerts is online"** on Telegram.

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
| `zenobot status` | check it's running |
| `zenobot restart` | restart the bot |

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
| No "online" message on Telegram | Make sure you pressed **Start** in your bot. Run `zenobot logs` to see the error. Run `zenobot edit` to fix the token or Chat ID. |
| Bot replies "⛔ This bot is private" | `CHAT_ID` doesn't match your Telegram id. Ask @userinfobot again, then run `zenobot edit`. |
| Dashboard doesn't open | Wait 2 minutes after installing; the HTTPS certificate takes a moment. Use exactly the `https://…sslip.io` address. Check `zenobot status`. |
| Dashboard keeps asking for the password | Username `admin`. Run `zenobot password` to see the password. |
| `zenobot update` says "not possible to fast-forward" | Run `cd /opt/zenobot_alerts && git fetch && git reset --hard origin/main`, then `zenobot update`. This keeps your settings. |
| Installer says "Could not download the repository" | The repository is private or the address is wrong. Make it public, or see "Private repository" above. |
| Exit alert shows ⚠️ "only N candles of history" | The token is too new for your RSI/BB lengths. Use a smaller timeframe or shorter lengths, or wait. |
| Many "429 rate limited" in `/status` | Normal from time to time. Lower `GECKO_RPM` to 5 or reduce the number of exit alerts. |

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
- **Exit-alert confirmation strictness:** the top of `app/modules/exit_alerts.py`, `CONFIRM_CHECKS` and `MAX_PRICE_GAP`.
- **Default scanner values:** `DEFAULT_SCANNER` in `app/db.py`.
- **Alert message wording:** `app/formatting.py`.

### Optional variables
Add these with `zenobot edit`, one per line like `GECKO_RPM=5`: `JUPITER_API_KEY`, `GECKO_RPM` (default 8), `JUPITER_RPM` (24 without a key, 50 with one), `MODULE_RPM` (15), `METEORA_RPS` (5).


*Not financial advice. Alerts depend on third-party data that can be delayed or wrong. Always double-check before acting.*
