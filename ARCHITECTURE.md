# NBA Odds Sniper - Architecture & Data Flow

## Project Purpose

Monitor NBA moneyline odds on Kalshi. When a heavy pregame favorite (>= -300 / 75% probability) sees their live odds drop significantly during a game (to <= -150 / 60% probability), send a Telegram notification signaling a potential betting entry point.

---

## File Map

| File | Role |
|------|------|
| `main.py` | Entry point, orchestrates scan + polling loop |
| `config.py` | Constants and env vars |
| `kalshi.py` | Kalshi API client (odds data) |
| `scores.py` | NBA live scores via `nba_api` |
| `scanner.py` | Daily game scanner, identifies heavy favorites |
| `poller.py` | Live odds poller, checks for entry signals |
| `notifier.py` | Telegram notification formatting/sending |
| `state.py` | SQLite state management (monitored games, notification log) |

---

## Data Sources

### 1. Kalshi API (`kalshi.py`)
- **What**: Prediction market odds for NBA games
- **Auth**: None (public market data)
- **Base URL**: `https://api.elections.kalshi.com/trade-api/v2`
- **Rate limit**: 20 req/sec (enforced with 50ms throttle)
- **Endpoints used**:
  - `GET /events?series_ticker=KXNBAGAME` — List all NBA game events
  - `GET /markets?event_ticker=KXNBAGAME-...` — Get bid/ask for a specific game
- **Key functions**:
  - `get_game_odds(ticker)` — Returns current favorite/underdog (re-sorts each call). Used during **daily scan** only.
  - `get_team_odds(ticker, team)` — Returns odds for a **specific team** regardless of who currently leads. Used during **live polling** to avoid the favorite-flip bug.
- **Data provided**: Bid/ask prices (probability 0-1), which are used to determine favorite/underdog and calculate American odds

### 2. NBA API (`scores.py`)
- **What**: Live NBA scores and game status
- **Library**: `nba_api` (wraps NBA.com endpoints)
- **Auth**: None
- **Endpoints used**:
  - `scoreboard.ScoreBoard()` — Today's games, scores, periods, game clocks
- **Data provided**: Game status (scheduled/live/final), scores, period, clock (formatted from ISO 8601 duration), start times

### 3. Telegram Bot API (`notifier.py`)
- **What**: Sends notifications to a Telegram channel
- **Auth**: Bot token (env var)
- **Library**: `python-telegram-bot`

---

## Database Schema (SQLite - `snipe.db`)

### `monitored_games`
Stores games we're actively watching.

| Column | Type | Purpose |
|--------|------|---------|
| ticker | TEXT UNIQUE | Kalshi event ticker (e.g., `KXNBAGAME-26FEB24BOSPHX`) |
| game_date | TEXT | `YYYY-MM-DD` |
| home_team | TEXT | 3-letter code (e.g., `PHX`) |
| away_team | TEXT | 3-letter code (e.g., `BOS`) |
| favorite_team | TEXT | Team that was the favorite at scan time |
| pregame_odds | REAL | Favorite's probability at scan time (0-1) |
| start_time | TEXT | ISO 8601 from NBA API |
| last_notified_odds | REAL | Probability when last notification was sent |
| last_notification_time | TEXT | ISO timestamp |
| status | TEXT | `active` or `complete` |

### `notifications`
Log of all sent notifications.

| Column | Type | Purpose |
|--------|------|---------|
| ticker | TEXT | Which game |
| odds_at_notification | REAL | Probability when sent |
| home_score, away_score | INTEGER | Score at time of notification |
| period | INTEGER | Quarter (1-4, 5+ for OT) |
| clock | TEXT | Game clock |

---

## Complete Data Flow

### Phase 1: Startup (`main.py:main()`)

```
main()
  → state.init_db()                    # Create tables if needed
  → notifier.send_startup_message()    # "Bot is active" Telegram msg
  → schedule daily scan at 15:00 UTC   # (10 AM ET / 7 AM PT)
  → schedule cleanup at 03:00 UTC      # Delete games > 7 days old
  → enter main loop (Phase 3)
```

No scan runs on startup. The first scan happens at the scheduled 7 AM PST time.

### Phase 2: Daily Scan (`scanner.scan_games_for_date()`)

Runs at 7 AM PST. This is an **overview** — it identifies which games to track and sends a slate notification. The odds captured here are "morning odds" and will be **replaced** with fresh odds right before each game starts (see Phase 3.5).

```
scan_games_for_date(date)
  → kalshi.get_nba_events()            # GET /events — all NBA events
  → filter to today's date
  → scores.get_todays_games()          # NBA API — get start times
  → FOR EACH today's event:
      → kalshi.get_game_odds(ticker)   # GET /markets — determine favorite
          → kalshi.get_event_markets() # Fetches both sides of the market
          → Sort by probability, determine favorite vs underdog
          → Calculate American odds
      → IF favorite_prob >= 0.75 (PREGAME_THRESHOLD):
          → state.add_monitored_game() # INSERT into SQLite (skips if exists)
      → ELSE: skip (not a heavy enough favorite)
  → notifier.send_slate_notification() # Telegram: "Today's Slate" message
```

**API calls during scan**: 1 Kalshi events call + N Kalshi markets calls (one per today's game) + 1 NBA scoreboard call.

### Phase 3: Main Loop (`main.py:main()`)

```
LOOP forever:
  → schedule.run_pending()             # Check if daily scan is due
  → state.get_active_games()           # Query SQLite for active games
  → IF no active games: sleep 60s, continue
  → check_pregame_refreshes()          # Refresh odds for games within 5 min of start
  → scores.get_todays_games()          # NBA API — check game statuses
  → IF any game is "live":
      → run_polling_loop()             # Enter fast polling (Phase 4)
  → ELSE:
      → get_seconds_until_first_game() # Calculate wait time from DB
      → sleep (up to 5 min)
```

### Phase 3.5: Pregame Odds Refresh (`main.py:check_pregame_refreshes()`)

Runs in the main loop. When a tracked game is within 5 minutes of its start time, this fetches the **latest** Kalshi odds and overwrites the morning scan odds in the DB. This ensures the `pregame_odds` baseline reflects injury news, line movement, etc. that happened throughout the day.

```
check_pregame_refreshes()
  → FOR EACH active game not yet refreshed:
      → Parse start_time, check if within 5 minutes
      → IF yes:
          → kalshi.get_team_odds(ticker, favorite_team)  # Fresh odds
          → state.update_pregame_odds(ticker, new_prob)  # Overwrite DB
          → notifier.send_game_starting()                # "Now tracking" Telegram msg
          → Mark as refreshed (in-memory set)
```

Each game is refreshed exactly once. The in-memory set resets on bot restart.

### Phase 4: Fast Polling Loop (`main.py:run_polling_loop()`)

```
LOOP every 1 second (POLL_INTERVAL_SECONDS):
  → state.get_active_games()           # Query SQLite
  → IF no active games: break
  → poll_live_games()
      → scores.get_todays_games()      # NBA API — 1 call for all games
      → FOR EACH active game in DB:
          → Match to NBA game by "away@home" key
          → IF game is "final": state.mark_game_complete()
          → IF game is "live": poller.process_game()  (Phase 5)
```

### Phase 5: Process Individual Game (`poller.process_game()`)

```
process_game(game)
  → kalshi.get_team_odds(ticker, favorite_team)  # Odds for the STORED favorite
  → scores.find_game_by_teams()                  # NBA API — live score
      → internally calls get_todays_games()
  → IF game is final: mark complete
  → IF game is live:
      → Determine favorite's score vs underdog's score
      → state.should_notify(ticker, current_prob):
          → current_prob <= 0.60 (ENTRY_THRESHOLD)?
          → Never notified before? OR odds improved (lower probability)?
      → IF yes:
          → notifier.send_notification()    # Telegram alert
          → state.update_last_notification()
          → state.log_notification()
```

---

## Resolved Issues

### FIXED: Favorite team flipping mid-game
**Problem**: `get_game_odds()` re-sorts markets each call, so when a -300 favorite drops to +160, the other team becomes the "favorite" in the response. The poller would then check the wrong team's probability.

**Fix**: Added `kalshi.get_team_odds(ticker, team)` which looks up odds for the stored pregame favorite by team name. `poller.process_game()` now uses this instead of `get_game_odds()`.

### FIXED: Clock format not parsed
**Problem**: `get_todays_games()` returned raw ISO 8601 durations like `"PT03M00.00S"`.

**Fix**: `scores.get_todays_games()` now calls `format_clock()` on the `gameClock` value, returning `"3:00"`.

### FIXED: Dead code removed
**Problem**: `poller.poll_active_games()` and `poller.has_active_games()` existed but were never called from `main.py`.

**Fix**: Removed both functions. The main loop uses `main.py:poll_live_games()` which delegates to `poller.process_game()`.

### FIXED: Pregame odds are snapshot-only
**Problem**: Pregame odds were captured once during the 7 AM scan and never updated. Injury news, line movement, etc. throughout the day meant the stored odds could be stale by game time.

**Fix**: Added pregame odds refresh (`check_pregame_refreshes()`). 5 minutes before each game starts, fresh odds are fetched from Kalshi and overwrite the morning scan odds in the DB. A "Now tracking" notification is sent to Telegram.

### FIXED: Daily scan ran on startup
**Problem**: `run_daily_scan()` was called immediately on bot startup, which was unnecessary since the scheduled scan handles it.

**Fix**: Removed the startup scan. The bot now just starts up and waits for the scheduled 7 AM PST scan.

### FIXED: Hardcoded timezone
**Problem**: `timedelta(hours=5)` assumed EST year-round, off by 1 hour during EDT (March-November).

**Fix**: Uses `zoneinfo.ZoneInfo("America/New_York")` which handles EST/EDT automatically.

### FIXED: Notification odds formatting
**Problem**: Positive American odds displayed without `+` prefix (e.g., `163` instead of `+163`).

**Fix**: Added `format_american()` helper in `notifier.py`.

---

## Remaining Issues

### ISSUE 1: Redundant NBA API calls during polling

`poll_live_games()` fetches all NBA games with `scores.get_todays_games()`, but then `poller.process_game()` calls `scores.find_game_by_teams()`, which internally calls `get_todays_games()` **again** for each game. With 3 monitored games, that's 4 NBA API calls per poll cycle instead of 1.

### ISSUE 2: Polling at 1-second intervals is aggressive

`POLL_INTERVAL_SECONDS = 1` means:
- 1 Kalshi API call per monitored game per second
- 1+ NBA API calls per second
- With 3 games, that's ~7 API calls/second sustained for hours

The README says 5 seconds but config says 1 second.

### ISSUE 3: `should_notify` variable naming confusion

`should_notify(ticker, current_odds)` accepts `current_odds` as a parameter name, but it's actually a **probability** (0-1), not American odds. Same for `last_notified_odds` in the DB.

### ISSUE 4: No caching of NBA API responses

`scores.get_todays_games()` hits the NBA API endpoint on every call with no caching. Even a 5-10 second cache would dramatically reduce API calls.

### ISSUE 5: `state.init_db()` runs on module import

`state.py` line 245: `init_db()` is called at module import time. Side effect on import.

### ISSUE 6: Main loop has three nested polling layers

```
main() while loop          → checks every 60s/10s/5min for live games
  → run_polling_loop()     → checks every 1s, calls poll_live_games()
    → poll_live_games()    → for each game, calls process_game()
      → process_game()     → fetches odds + scores again
```

### ISSUE 7: Unused functions remain

- `kalshi.get_todays_heavy_favorites()` — never called
- `scores.get_live_score()` — never called
- `scanner.scan_todays_games()` — never called (main.py calls `scan_and_notify()` directly)

---

## Deployment

- **Platform**: Railway (via `railway.toml`)
- **Builder**: Nixpacks
- **Start command**: `python main.py`
- **Restart policy**: Always (long-running process)
- **Env vars needed**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`

---

## Thresholds Summary

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `PREGAME_THRESHOLD` | 0.75 | Only track favorites at >= 75% (-300 American) |
| `ENTRY_THRESHOLD` | 0.60 | Notify when favorite drops to <= 60% (-150 American) |
| `POLL_INTERVAL_SECONDS` | 1 | Poll every 1 second during live games |

---

## When Are Odds Pulled?

Odds are pulled from Kalshi at three moments:

1. **7 AM scan** (`scanner.py`): Once per game via `get_game_odds()` to determine if it's a heavy favorite. Sets initial `pregame_odds` in the DB. These are "morning preview" odds.
2. **5 min before tipoff** (`main.py:refresh_pregame_odds()`): Once per game via `get_team_odds()`. Overwrites `pregame_odds` in the DB with the latest line. This is the **real baseline** used for entry threshold comparison.
3. **Every poll cycle** (`poller.py`): Once per monitored game per second via `get_team_odds()` to check the stored favorite's current probability against the entry threshold.

**NOT pulled**: During the main loop's "is any game live?" check — that only checks NBA API for game status, not Kalshi.
