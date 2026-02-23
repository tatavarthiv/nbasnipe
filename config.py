import os
from dotenv import load_dotenv

load_dotenv()

# Thresholds (in Kalshi probability format 0-1, which is cents/100)
# Kalshi prices: 75 cents = 0.75 = 75% probability = -300 American odds
# Kalshi prices: 60 cents = 0.60 = 60% probability = -150 American odds
PREGAME_THRESHOLD = 0.75  # Monitor games where favorite is >= this (heavy favorite, -300+)
ENTRY_THRESHOLD = 0.60    # Notify when odds drop to <= this (entry point, -150 or better)

# Polling
POLL_INTERVAL_SECONDS = 1

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

# Database
DB_PATH = os.environ.get("DB_PATH", "snipe.db")

# Kalshi API
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_NBA_SERIES = "KXNBAGAME"
