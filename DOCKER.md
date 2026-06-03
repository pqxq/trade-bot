# Docker Setup Guide

Complete step-by-step instructions to run the Binance Futures Testnet bot on any server (Oracle Cloud, VPS, local).

## Requirements

- Docker >= 24 and Docker Compose v2 (`docker compose` not `docker-compose`)
- Binance Futures Testnet API keys from [testnet.binancefuture.com](https://testnet.binancefuture.com)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)

---

## Step 1 — Clone & configure

```bash
git clone https://github.com/pqxq/trade-bot.git
cd trade-bot
cp .env.example .env
nano .env          # fill in all values (see below)
```

Minimum required values in `.env`:

```env
API_ID=12345678
API_HASH=your_telegram_api_hash
CHANNEL_NAME=intelligent_trading_signals

BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_TESTNET=true
TRADING_SYMBOL=BTC/USDT
LEVERAGE=10
```

---

## Step 2 — Create Telegram session (ONCE only)

The bot uses Telethon which requires a one-time interactive login to create a session file.
This must be done **before** starting the container, because Docker has no terminal for interactive input.

```bash
mkdir -p data logs
pip install telethon python-dotenv   # on your local machine or server directly
python3 - <<'EOF'
import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient
load_dotenv()
client = TelegramClient('data/telegram', int(os.environ['API_ID']), os.environ['API_HASH'])
client.start()          # prompts phone number + code
print('Session saved!')
client.disconnect()
EOF
```

After this you will have `data/telegram.session`. **Never delete this file.**

---

## Step 3 — Build and start

```bash
docker compose build
docker compose up -d
```

The container will:
1. Refuse to start if `BINANCE_TESTNET=false` (safety guard)
2. Connect to Binance Futures Testnet via CCXT
3. Connect to Telegram and listen to `@intelligent_trading_signals`
4. Open the FastAPI dashboard on port 8000

---

## Step 4 — Verify everything works

```bash
# Check container is running
docker compose ps

# Watch live logs
docker compose logs -f --tail=50

# Open dashboard
curl http://localhost:8000/
# or in browser: http://<your-server-ip>:8000
```

Expected log output when healthy:

```
INFO | Binance Futures Testnet Trading Platform
INFO | Initializing exchange connection…
INFO | Leverage set to 10 for BTC/USDT
INFO | Exchange ready — symbol=BTC/USDT leverage=10
INFO | Telegram listener connected to @intelligent_trading_signals
```

---

## Useful commands

| Command | Description |
|---|---|
| `docker compose up -d` | Start in background |
| `docker compose logs -f` | Follow live logs |
| `docker compose restart trader` | Restart without rebuild |
| `docker compose down` | Stop and remove container |
| `docker compose build --no-cache` | Rebuild after code changes |
| `docker compose exec trader bash` | Shell inside container |

---

## Updating after code changes

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

---

## Oracle Cloud specific

Open port 8000 in Oracle Cloud security list:

```bash
# On the server — open firewall
sudo firewall-cmd --zone=public --add-port=8000/tcp --permanent
sudo firewall-cmd --reload
```

Then add an **Ingress Rule** in Oracle Cloud Console:
- Source CIDR: `0.0.0.0/0`
- IP Protocol: TCP
- Destination Port Range: `8000`

Dashboard will be available at `http://<oracle-public-ip>:8000`.

---

## Data persistence

| Path (host) | Path (container) | Contents |
|---|---|---|
| `./data/` | `/app/data/` | SQLite DB + Telegram session |
| `./logs/` | `/app/logs/` | Loguru rotating log files |

Both are bind-mounted so data survives container restarts and rebuilds.

---

## Troubleshooting

**`BINANCE_TESTNET is set to false`** — change `BINANCE_TESTNET=true` in `.env`.

**`AUTH_REQUIRED`** in bot status — the session file is missing or corrupted. Redo Step 2.

**`ccxt.errors.AuthenticationError`** — wrong testnet keys. Make sure you created keys at `testnet.binancefuture.com`, not the real Binance.

**Container exits immediately** — run `docker compose logs trader` to see the Python traceback.

**Port 8000 unreachable on Oracle Cloud** — you need both the OS firewall rule (`firewall-cmd`) AND the VCN Ingress Rule in the Oracle console.
