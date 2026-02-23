# NBA Odds Sniper

Monitor NBA moneyline odds on Kalshi and get notified when heavy pregame favorites drop to favorable live odds.

## How It Works

1. **Daily scan** identifies games where a team is a heavy favorite (>= -300)
2. **Live polling** every 5 seconds checks if odds drop to entry threshold (<= -150)
3. **Telegram notification** sent when odds hit threshold, and again only if they improve

## Quick Start

### 1. Create Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow prompts
3. Save the bot token

### 2. Create Telegram Channel

1. Create a new channel in Telegram
2. Add your bot as an admin
3. Get channel ID:
   - Public channel: use `@channelname`
   - Private: add [@userinfobot](https://t.me/userinfobot) to get numeric ID

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your bot token and channel ID
```

### 4. Install & Run

```bash
pip install -r requirements.txt
python main.py
```

## Example Output

```
=== Scanning for 2026-02-23 ===
Scanning games for 2026-02-23...
Found 3 NBA games on 2026-02-23
  KXNBAGAME-26FEB23UTAHOU: HOU -700 - ADDED to monitoring
  KXNBAGAME-26FEB23SACMEM: MEM -177 - below threshold, skipping

Monitoring 1 games:
  HOU -700 vs UTA (2026-02-23)
```

## Notification Format

```
ROCKETS currently losing to Jazz 45-55
Q2 | 4:32 remaining

Current odds: -145
Pregame odds: -700
```

## Configuration

Edit `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `PREGAME_THRESHOLD` | 0.75 | Monitor if favorite >= 75% (-300) |
| `ENTRY_THRESHOLD` | 0.60 | Notify when odds drop to 60% (-150) |
| `POLL_INTERVAL_SECONDS` | 5 | How often to check live games |

## Deploy to Railway

1. Push to GitHub
2. Create project on [railway.app](https://railway.app)
3. Connect repo
4. Add environment variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`
5. Deploy

## Data Sources

- **Kalshi API** - Free, no auth needed for market data
- **nba_api** - Free NBA.com wrapper for live scores
