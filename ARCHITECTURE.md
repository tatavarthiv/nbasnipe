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
- **Rate limit**: 1 second between requests (generous throttle to avoid 429s)
- **Pagination**: `get_nba_events()` paginates with cursor, limit=200 per page
- **Endpoints used**:
  - `GET /events?series_ticker=KXNBAGAME` — List all NBA game events (paginated)
  - `GET /markets?event_ticker=KXNBAGAME-...` — Get bid/ask for a specific game
- **Key functions**:
  - `get_nba_events()` — Paginated fetch of all NBA events. Loops until no cursor returned.
  - `get_event_markets(event_ticker)` — Fetches both sides of a market. Calculates probability from midpoint of bid/ask (in cents → 0-1). Falls back to `last_price` if no bid/ask.
  - `get_game_odds(ticker)` — Calls `get_event_markets()`, sorts by probability to determine favorite/underdog. Returns None if != 2 markets. Detects finished games (probability > 0.99). **Used during slate scans only.**
  - `get_team_odds(ticker, team)` — Calls `get_event_markets()`, looks up odds for a **specific team** by name. Returns None if team not found or != 2 markets. **Used during live polling and pregame refresh** to avoid the favorite-flip bug.
  - `probability_to_american(prob)` — Converts 0-1 probability to American odds integer.
  - `parse_event_ticker(ticker)` — Extracts away_team, home_team, game_date from ticker string like `KXNBAGAME-26FEB24BOSPHX`.

### 2. NBA API (`scores.py`)
- **What**: Live NBA scores and game status
- **Library**: `nba_api` (wraps NBA.com endpoints)
- **Auth**: None
- **Endpoints used**:
  - `scoreboard.ScoreBoard()` — Today's games, scores, periods, game clocks
- **Key functions**:
  - `get_todays_games()` — Returns all today's games with status, scores, period, formatted clock, start_time. Game status mapping: `gameStatus 1 → "scheduled"`, `2 → "live"`, `3 → "final"`.
  - `find_game_by_teams(home, away)` — Normalizes team abbreviations via `TEAM_ABBREV_MAP`, then calls `get_todays_games()` internally and searches for match. **Note: this triggers a fresh NBA API call each time.**
  - `format_clock(clock)` — Parses ISO 8601 duration `"PT04M32.00S"` → `"4:32"`. Returns empty string for empty input, passes through non-ISO strings.
  - `period_to_string(period)` — `0 → "Pre-game"`, `1-4 → "Q1"-"Q4"`, `5+ → "OT1", "OT2"`, etc.

### 3. Telegram Bot API (`notifier.py`)
- **What**: Sends notifications to a Telegram channel
- **Auth**: Bot token + channel ID (env vars)
- **Library**: `python-telegram-bot` (async, wrapped with `asyncio.run()`)
- **Behavior when unconfigured**: Prints message to console instead of sending

---

## Database Schema (SQLite - `snipe.db`)

### `monitored_games`
Stores games we're actively watching.

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| ticker | TEXT UNIQUE | Kalshi event ticker (e.g., `KXNBAGAME-26FEB24BOSPHX`) |
| game_date | TEXT | `YYYY-MM-DD` |
| home_team | TEXT | 3-letter code (e.g., `PHX`) |
| away_team | TEXT | 3-letter code (e.g., `BOS`) |
| favorite_team | TEXT | Team that was the favorite at scan time |
| pregame_odds | REAL | Favorite's probability at scan time, overwritten by pregame refresh (0-1) |
| start_time | TEXT | ISO 8601 UTC from NBA API (may be NULL) |
| last_notified_odds | REAL | Probability when last notification was sent (NULL if never notified) |
| last_notified_quarter | INTEGER | Quarter/period when last notification was sent (NULL if never, resets baseline each quarter) |
| last_notification_time | TEXT | ISO timestamp of last notification |
| status | TEXT | `active` or `complete` |
| created_at | TEXT | Auto-set on insert |

### `notifications`
Log of all sent notifications.

| Column | Type | Purpose |
|--------|------|---------|
| id | INTEGER PK | Auto-increment |
| ticker | TEXT | Which game |
| odds_at_notification | REAL | Probability when sent |
| home_score, away_score | INTEGER | Score at time of notification |
| period | INTEGER | Quarter (1-4, 5+ for OT) |
| clock | TEXT | Game clock |
| sent_at | TEXT | Auto-set on insert |

Note: `init_db()` also runs at module import time (`state.py` line 268), so tables are created as soon as any module imports `state`.

---

## Complete Data Flow

### Phase 1: Startup (`main.py:main()`)

```
main()
  → state.init_db()                    # Create tables if needed (also runs on import)
  → notifier.send_startup_message()    # Telegram: "Bot redeployed and now monitoring games."
  → schedule.every().day.at("17:00")   # 9 AM PST  → run_slate_scan()
  → schedule.every().day.at("19:00")   # 11 AM PST → run_slate_scan()
  → schedule.every().day.at("21:00")   # 1 PM PST  → run_slate_scan()
  → schedule.every().day.at("23:00")   # 3 PM PST  → run_slate_scan()
  → schedule.every().day.at("03:00")   # Cleanup    → state.cleanup_old_games(7)
  → enter main loop (Phase 3)
```

**No scan runs on startup.** The bot waits for the first scheduled scan. The `_refreshed_games` in-memory set is initialized empty (resets on every restart/redeploy).

---

### Phase 2: Slate Scan (`main.py:run_slate_scan()` → `scanner.scan_and_notify()`)

Scheduled every 2 hours (9 AM, 11 AM, 1 PM, 3 PM PST). Each scan re-fetches all Kalshi events and current odds.

```
run_slate_scan()
  → scores.get_todays_games()                    # NBA API call
  → IF any game has status == "live":
      → SKIP entire scan (print log, return)
  → IF NBA API call fails (exception):
      → Proceed with scan anyway (fail-open)
  → scanner.scan_and_notify(today's date)
```

```
scan_and_notify(date)
  → scan_games_for_date(date)                    # Returns (tracked, untracked)
  → notifier.send_slate_notification(date, tracked, untracked)
```

```
scan_games_for_date(date)
  → kalshi.get_nba_events()                      # Paginated — GET /events (may be multiple pages)
      → FOR EACH page:
          → 1 second rate limit wait
          → GET /events?series_ticker=KXNBAGAME&limit=200&cursor=...
          → Parse each event ticker → extract teams + date
          → Continue until no cursor returned
  → Filter to events matching today's date
  → scores.get_todays_games()                    # NBA API — get start times
  → Build start_times lookup: "away@home" → start_time

  → FOR EACH today's event:
      → kalshi.get_game_odds(event_ticker)        # 1 second wait + GET /markets
          → get_event_markets(event_ticker)       # Fetches both market sides
          → IF != 2 markets: return None (skip this game)
          → Sort by probability descending
          → favorite = highest prob, underdog = lowest
          → is_finished = favorite.probability > 0.99
          → Return { favorite, underdog, probs, american odds, is_finished }

      → IF odds is None: skip (log "No odds available")
      → IF is_finished: skip (log "Game already finished")

      → Look up start_time from NBA data by "away@home" key

      → IF favorite_prob >= 0.75 (PREGAME_THRESHOLD):
          → state.add_monitored_game(...)          # INSERT OR IGNORE (unique on ticker)
              → Returns True if new, False if already exists
          → Append to tracked list
      → ELSE:
          → Append to untracked list (below threshold)

  → Return (tracked, untracked)
```

```
send_slate_notification(date, tracked, untracked)
  → Format date as "Mon DD"
  → FOR EACH tracked game:
      → Look up team full name from TEAM_NAMES dict
      → Format odds with +/- prefix via format_american()
      → Format start_time to "H:MM PM" ET via format_start_time_short()
  → FOR EACH untracked game: same formatting
  → Send via Telegram (asyncio.run)
```

**API calls during scan**: 1+ Kalshi events calls (paginated, 1s each) + N Kalshi markets calls (one per today's game, 1s each) + 2 NBA scoreboard calls (one for live check, one for start times).

---

### Phase 3: Main Loop (`main.py:main()`)

```
LOOP forever:
  → schedule.run_pending()                       # Fires scheduled scans/cleanup if due

  → active_games = state.get_active_games()      # SQLite query: WHERE status = 'active'
  → IF no active games:
      → sleep(60)                                # Check every minute
      → continue

  → check_pregame_refreshes()                    # Phase 3.5 — refresh odds near tipoff

  → nba_games = scores.get_todays_games()        # NBA API call
  → any_live = any game with status == "live"

  → IF any_live:
      → run_polling_loop()                       # Enter fast polling (Phase 4)
      → (after polling loop exits, continue main loop)

  → ELSE (no games live yet):
      → seconds = get_seconds_until_first_game()
          → state.get_earliest_start_time()      # SQLite: MIN(start_time) WHERE active
          → IF no start_time found: return 0
          → Parse ISO time, calculate delta from now
          → Subtract 300s (start polling 5 min early)
          → Return max(0, seconds)
      → IF seconds > 60:
          → sleep(min(seconds, 300))             # Sleep up to 5 minutes
      → ELSE:
          → sleep(10)                            # Near game time, check every 10s
```

---

### Phase 3.5: Pregame Odds Refresh (`main.py:check_pregame_refreshes()`)

Runs in both the main loop AND inside the polling loop. Ensures pregame odds are fresh before games start.

```
check_pregame_refreshes()
  → active_games = state.get_active_games()       # SQLite query
  → now = datetime.now(timezone.utc)

  → FOR EACH active game:
      → IF ticker in _refreshed_games: skip       # Already refreshed this session
      → IF start_time is None: skip               # No start time available

      → Parse start_time (ISO 8601 → datetime)
      → minutes_until = (game_time - now) / 60

      → IF minutes_until <= 5:
          → refresh_pregame_odds(game)
```

```
refresh_pregame_odds(game)
  → kalshi.get_team_odds(ticker, favorite_team)   # 1s wait + GET /markets
  → IF no odds returned:
      → Log warning
      → Add ticker to _refreshed_games anyway     # Don't retry
      → return

  → old_prob = game["pregame_odds"]               # From DB (set during scan)
  → new_prob = odds["probability"]                # Fresh from Kalshi
  → state.update_pregame_odds(ticker, new_prob)   # Overwrite DB
  → Add ticker to _refreshed_games                # Mark as done

  → Determine underdog team from home/away vs favorite
  → Log: "Pregame odds refreshed FAV old → new"

  → notifier.send_game_starting(                  # Telegram: "Now tracking: Team ODDS vs Opponent"
      favorite_team, underdog_team, new_american
    )
```

**Each game is refreshed exactly once per bot session.** The `_refreshed_games` set is in-memory only — resets on restart/redeploy.

---

### Phase 4: Fast Polling Loop (`main.py:run_polling_loop()`)

Entered when any NBA game is live. Runs until all games are finished or no active games remain.

```
run_polling_loop()
  LOOP:
    → active_games = state.get_active_games()     # SQLite query
    → IF no active games: break (exit polling)

    → check_pregame_refreshes()                   # In case later games haven't started yet

    → TRY:
        → any_live = poll_live_games()            # Phase 4.5

        → IF not any_live:
            → nba_games = scores.get_todays_games()  # Extra NBA API call
            → any_scheduled = any game with status == "scheduled"
            → IF not any_scheduled:
                → break (all games finished, exit polling)
            → (else: some games haven't started yet, keep looping)

    → CATCH Exception: log and continue

    → sleep(POLL_INTERVAL_SECONDS)                # 1 second
```

---

### Phase 4.5: Poll Live Games (`main.py:poll_live_games()`)

Called once per poll cycle. Checks all active games against NBA scores.

```
poll_live_games()
  → active_games = state.get_active_games()       # SQLite query
  → IF no active games: return False

  → nba_games = scores.get_todays_games()         # NBA API — 1 call for ALL games
  → Build lookup: "away@home" → game data

  → any_live = False
  → FOR EACH active game in DB:
      → key = "away_team@home_team"
      → nba_game = lookup.get(key)
      → IF no match found: skip (continue)

      → status = nba_game["status"]

      → IF status == "final":
          → state.mark_game_complete(ticker)      # status → 'complete'
          → continue

      → IF status == "live":
          → any_live = True
          → poller.process_game(game)             # Phase 5

      → IF status == "scheduled":
          → (no action, game hasn't started)

  → return any_live
```

---

### Phase 5: Process Individual Game (`poller.process_game()`)

Called for each live game every poll cycle. This is where entry signal detection happens.

```
process_game(game)
  → ticker = game["ticker"]
  → favorite_team = game["favorite_team"]         # Stored from scan, never changes

  → odds = kalshi.get_team_odds(ticker, favorite_team)  # 1s wait + GET /markets
      → Looks up odds for the STORED favorite specifically
      → NOT the current market leader (avoids favorite-flip bug)
  → IF no odds: log and return

  → current_prob = odds["probability"]            # 0-1, favorite's current probability
  → current_american = odds["american"]           # e.g., -145 or +163

  → live_game = scores.find_game_by_teams(home, away)
      → Normalizes team codes via TEAM_ABBREV_MAP
      → Calls get_todays_games() AGAIN (redundant NBA API call)
      → Searches for matching game

  → IF live_game is None:
      → Log: "FAV ODDS (pregame)"
      → check_and_notify_pregame(game, current_prob, current_american)
          → state.should_notify(ticker, current_prob, period=0)
          → IF yes: send Telegram notification with score 0-0, clock="Pregame"
          → state.update_last_notification(ticker, current_prob, period=0)
      → return

  → IF live_game["status"] == "final":
      → state.mark_game_complete(ticker)
      → return

  → IF live_game["status"] == "scheduled":
      → Log: "FAV ODDS (not started)"
      → return (no action)

  → (Game is live from here)
  → home_score = live_game["home_score"]
  → away_score = live_game["away_score"]
  → period = live_game["period"]                  # 1-4 for quarters, 5+ for OT
  → clock = live_game["clock"]                    # Formatted "M:SS"

  → Determine favorite's score vs underdog's score:
      → IF favorite_team == home_team: fav_score = home_score
      → ELSE: fav_score = away_score

  → Log: "TICKER: FAV ODDS (fav_score-und_score Qperiod)"

  → IF state.should_notify(ticker, current_prob, period):     # DECISION POINT
      → Log: "SENDING NOTIFICATION!"

      → pregame_prob = game["pregame_odds"]       # From DB (possibly refreshed)
      → pregame_american = probability_to_american(pregame_prob)

      → notifier.send_notification(               # Telegram alert
          favorite_team, underdog_team,
          favorite_score, underdog_score,
          period, clock,
          current_american, pregame_american
        )

      → state.update_last_notification(ticker, current_prob, period)
          → UPDATE last_notified_odds = current_prob
          → UPDATE last_notified_quarter = period
          → UPDATE last_notification_time = now()

      → state.log_notification(                   # INSERT into notifications table
          ticker, current_prob,
          home_score, away_score,
          period, clock
        )
```

---

### Notification Decision Logic (`state.should_notify()`)

This is the core business logic. Four conditions checked in order:

```
should_notify(ticker, current_odds, period)
  → game = get_game_by_ticker(ticker)             # SQLite lookup
  → IF game not found: return False

  → GATE: current_odds > 0.60 (ENTRY_THRESHOLD)?
      → return False                              # Odds not low enough, no notification

  → BRANCH 1: game["last_notified_odds"] is None?
      → return True                               # First notification ever for this game

  → BRANCH 2: game["last_notified_quarter"] is None
               OR period != game["last_notified_quarter"]?
      → return True                               # New quarter/OT = fresh baseline reset

  → BRANCH 3: current_odds < game["last_notified_odds"]?
      → return True                               # Same quarter, odds improved (lower prob = better value)

  → return False                                  # Same quarter, odds same or worse
```

**Quarter reset behavior**: When a new quarter starts (period changes from e.g. 1→2), `last_notified_quarter` won't match, so any odds at/below the -150 entry threshold trigger a notification. This prevents Q1's +100 notification from suppressing Q4's -120 opportunity.

**OT behavior**: Each OT period (5, 6, 7...) triggers a fresh reset since `period` increments.

**Pregame behavior**: Uses `period=0`. First pregame notification always fires (branch 1). Subsequent pregame notifications require odds improvement (branch 3) since period stays at 0.

---

### Notification Message Format (`notifier.format_notification()`)

```
IF period == 0 (pregame):
  "TEAM_NAME vs Opponent"
  "Pregame" or clock text

IF period > 0 (live):
  IF favorite leading:  "TEAM_NAME currently leading Opponent score-score"
  IF favorite losing:   "TEAM_NAME currently losing to Opponent score-score"
  IF tied:              "TEAM_NAME currently tied with Opponent score-score"
  "Q2 | 4:32 remaining"

Current odds: -145
Pregame odds: -600
```

---

## When Are Odds Pulled?

Odds are pulled from Kalshi at three distinct moments:

1. **Every 2 hours** (9 AM - 3 PM PST, `scanner.py`): Once per game via `get_game_odds()` to determine who the favorite is and whether they meet the -300 threshold. Sets initial `pregame_odds` in the DB. Each scan picks up newly listed Kalshi events (paginated) and sends an updated slate with current odds + start times. **1 second between each API call.**

2. **5 min before tipoff** (`main.py:refresh_pregame_odds()`): Once per game via `get_team_odds()` for the stored favorite. Overwrites `pregame_odds` in the DB with the latest line. This becomes the **real baseline** used for entry threshold comparison. Sends "Now tracking" Telegram message.

3. **Every poll cycle** (`poller.py:process_game()`): Once per monitored live game per second via `get_team_odds()` for the stored favorite. Checks current probability against the entry threshold. **1 second rate limit applies**, so with 3 games this is ~3 seconds per full cycle.

**NOT pulled**: During the main loop's "is any game live?" check — that only queries the NBA API for game status, not Kalshi.

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
**Problem**: Pregame odds were captured once during the morning scan and never updated. Injury news, line movement, etc. throughout the day meant the stored odds could be stale by game time.

**Fix**: Added pregame odds refresh (`check_pregame_refreshes()`). 5 minutes before each game starts, fresh odds are fetched from Kalshi and overwrite the scan odds in the DB. A "Now tracking" notification is sent to Telegram.

### FIXED: Daily scan ran on startup
**Problem**: `run_daily_scan()` was called immediately on bot startup, which was unnecessary since the scheduled scan handles it.

**Fix**: Removed the startup scan. The bot now just starts up and waits for the scheduled scans.

### FIXED: Missing games from Kalshi
**Problem**: Kalshi doesn't always list all games early in the morning. A single morning scan would miss games added later.

**Fix**: Slate scans now run every 2 hours (9 AM, 11 AM, 1 PM, 3 PM PST). Each scan picks up newly listed Kalshi events and sends an updated slate. Scans skip once games go live. Added pagination to `get_nba_events()` (limit=200, cursor-based) to ensure all events are captured.

### FIXED: Hardcoded timezone
**Problem**: `timedelta(hours=5)` assumed EST year-round, off by 1 hour during EDT (March-November).

**Fix**: Uses `zoneinfo.ZoneInfo("America/Los_Angeles")` for Pacific time display.

### FIXED: Notification odds formatting
**Problem**: Positive American odds displayed without `+` prefix (e.g., `163` instead of `+163`).

**Fix**: Added `format_american()` helper in `notifier.py`.

### FIXED: No re-notification after quarter changes
**Problem**: `should_notify` used a single `last_notified_odds` high-water mark for the entire game. If odds hit +100 in Q1, a -120 line in Q4 would be suppressed because -120 is "worse" than +100.

**Fix**: Added per-quarter reset. `should_notify` now accepts a `period` parameter and compares against `last_notified_quarter` in the DB. When a new quarter (or OT period) starts, the baseline resets. Within the same quarter, notifications still require improvement.

### FIXED: Games cut off in Kalshi events
**Problem**: Kalshi events endpoint returned only first page of results, causing some games to be missing from scans.

**Fix**: Added cursor-based pagination to `get_nba_events()`. Loops until no cursor returned, fetching 200 events per page.

---

## Remaining Issues

### ISSUE 1: Redundant NBA API calls during polling

`poll_live_games()` fetches all NBA games with `scores.get_todays_games()`, but then `poller.process_game()` calls `scores.find_game_by_teams()`, which internally calls `get_todays_games()` **again** for each game. With 3 monitored games, that's 4 NBA API calls per poll cycle instead of 1.

**Impact**: Unnecessary load on NBA API. Could cause rate limiting or slower polling.

### ISSUE 2: `should_notify` variable naming confusion

`should_notify(ticker, current_odds)` accepts `current_odds` as a parameter name, but it's actually a **probability** (0-1), not American odds. Same for `last_notified_odds` in the DB.

### ISSUE 3: No caching of NBA API responses

`scores.get_todays_games()` hits the NBA API endpoint on every call with no caching. Combined with issue 1, this means multiple identical calls per second.

### ISSUE 4: `state.init_db()` runs on module import

`state.py` line 268: `init_db()` is called at module import time. Side effect on import means any `import state` creates/accesses the database.

### ISSUE 5: Unused functions remain

- `kalshi.get_todays_heavy_favorites()` — never called from any module
- `kalshi.american_to_probability()` — never called from any module
- `scores.get_live_score()` — never called from any module
- `scanner.scan_todays_games()` — never called (main.py calls `scan_and_notify()` directly)
- `scanner.get_monitored_summary()` — never called from any module
- `state.reset_db()` — only used for manual schema migrations, never called in normal flow

### ISSUE 6: Rate limit applies to polling too

The `_min_request_interval = 1.0` second rate limit was set to be generous during scans, but it also affects live polling. With 3 monitored games, each needing a `get_team_odds()` call (which calls `get_event_markets()` which calls `_rate_limit()`), a full poll cycle takes ~3 seconds minimum. The `POLL_INTERVAL_SECONDS = 1` config is effectively overridden by the rate limit.

---

## Deployment

- **Platform**: Railway (via `railway.toml`)
- **Builder**: Nixpacks
- **Start command**: `python main.py`
- **Restart policy**: Always (long-running process)
- **Env vars needed**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`
- **State**: `_refreshed_games` in-memory set resets on each deploy/restart

---

## Thresholds Summary

| Threshold | Value | Meaning |
|-----------|-------|---------|
| `PREGAME_THRESHOLD` | 0.75 | Only track favorites at >= 75% (-300 American) |
| `ENTRY_THRESHOLD` | 0.60 | Notify when favorite drops to <= 60% (-150 American) |
| `POLL_INTERVAL_SECONDS` | 1 | Poll every 1 second during live games (effective ~3s with rate limit) |
| `_min_request_interval` | 1.0 | Seconds between Kalshi API requests |
