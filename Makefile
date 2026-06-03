.PHONY: setup session build up down logs restart shell ps

# ── First-time setup ────────────────────────────────────────────────
setup:
	@test -f .env || (cp .env.example .env && echo "\n⚠️  Created .env from .env.example -- fill in your keys first!")
	@mkdir -p data logs

# ── Create Telegram session (run ONCE before docker up) ────────
ession:
	@echo "Generating Telegram session file..."
	docker compose run --rm \
	  -e PYTHONUNBUFFERED=1 \
	  trader \
	  python -c "
import asyncio, os
from dotenv import load_dotenv
from telethon.sync import TelegramClient
load_dotenv()
client = TelegramClient('data/telegram', int(os.environ['API_ID']), os.environ['API_HASH'])
client.start()
print('Session saved to data/telegram.session')
client.disconnect()
"

# ── Docker workflow ──────────────────────────────────────────────
build:
	docker compose build --no-cache

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

restart:
	docker compose restart trader

shell:
	docker compose exec trader bash

ps:
	docker compose ps
