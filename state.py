"""SQLite state management for tracking games and notifications."""
import sqlite3
from datetime import datetime
from typing import Optional
import config


def get_connection() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            game_date TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            favorite_team TEXT NOT NULL,
            pregame_odds REAL NOT NULL,
            start_time TEXT,
            last_notified_odds REAL,
            last_notified_quarter INTEGER,
            last_notification_time TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            odds_at_notification REAL NOT NULL,
            home_score INTEGER,
            away_score INTEGER,
            period INTEGER,
            clock TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_games_date
        ON monitored_games(game_date)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_games_status
        ON monitored_games(status)
    """)

    conn.commit()
    conn.close()


def add_monitored_game(
    ticker: str,
    game_date: str,
    home_team: str,
    away_team: str,
    favorite_team: str,
    pregame_odds: float,
    start_time: str = None
) -> bool:
    """Add a game to monitor. Returns True if added, False if exists."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO monitored_games
            (ticker, game_date, home_team, away_team, favorite_team, pregame_odds, start_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (ticker, game_date, home_team, away_team, favorite_team, pregame_odds, start_time))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_active_games(game_date: Optional[str] = None) -> list[dict]:
    """Get all active monitored games, optionally filtered by date."""
    conn = get_connection()
    cursor = conn.cursor()

    if game_date:
        cursor.execute("""
            SELECT * FROM monitored_games
            WHERE status = 'active' AND game_date = ?
        """, (game_date,))
    else:
        cursor.execute("""
            SELECT * FROM monitored_games WHERE status = 'active'
        """)

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def get_game_by_ticker(ticker: str) -> Optional[dict]:
    """Get a specific game by ticker."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT * FROM monitored_games WHERE ticker = ?
    """, (ticker,))

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else None


def update_pregame_odds(ticker: str, odds: float):
    """Update the pregame odds for a game (used for pre-game refresh)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE monitored_games
        SET pregame_odds = ?
        WHERE ticker = ?
    """, (odds, ticker))

    conn.commit()
    conn.close()


def update_last_notification(ticker: str, odds: float, period: int = 0):
    """Update the last notified odds and quarter for a game."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE monitored_games
        SET last_notified_odds = ?,
            last_notified_quarter = ?,
            last_notification_time = ?
        WHERE ticker = ?
    """, (odds, period, datetime.now().isoformat(), ticker))

    conn.commit()
    conn.close()


def mark_game_complete(ticker: str):
    """Mark a game as complete (no longer monitoring)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE monitored_games SET status = 'complete' WHERE ticker = ?
    """, (ticker,))

    conn.commit()
    conn.close()


def log_notification(
    ticker: str,
    odds: float,
    home_score: int,
    away_score: int,
    period: int,
    clock: str
):
    """Log a sent notification."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO notifications
        (ticker, odds_at_notification, home_score, away_score, period, clock)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (ticker, odds, home_score, away_score, period, clock))

    conn.commit()
    conn.close()


def should_notify(ticker: str, current_odds: float, period: int = 0) -> bool:
    """Check if we should send a notification for this game.

    Returns True if:
    - Odds are at or below ENTRY_THRESHOLD, AND:
    - Never notified before, OR
    - New quarter started (resets baseline each quarter/OT), OR
    - Same quarter but odds have improved (lower probability = better value)
    """
    game = get_game_by_ticker(ticker)
    if not game:
        return False

    # Check if odds are at or below entry threshold
    if current_odds > config.ENTRY_THRESHOLD:
        return False

    # First notification ever
    if game["last_notified_odds"] is None:
        return True

    # New quarter/OT period = fresh notification opportunity
    if game["last_notified_quarter"] is None or period != game["last_notified_quarter"]:
        return True

    # Same quarter: re-notify only if odds improved (lower probability = better value)
    return current_odds < game["last_notified_odds"]


def cleanup_old_games(days_old: int = 7):
    """Remove games older than specified days."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM monitored_games
        WHERE date(game_date) < date('now', ?)
    """, (f"-{days_old} days",))

    conn.commit()
    conn.close()


def get_earliest_start_time() -> Optional[str]:
    """Get the earliest start time among active games."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT start_time FROM monitored_games
        WHERE status = 'active' AND start_time IS NOT NULL
        ORDER BY start_time ASC
        LIMIT 1
    """)

    row = cursor.fetchone()
    conn.close()

    return row["start_time"] if row else None


def reset_db():
    """Drop and recreate tables. Use for schema changes."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS monitored_games")
    cursor.execute("DROP TABLE IF EXISTS notifications")
    conn.commit()
    conn.close()
    init_db()


# Initialize database on import
init_db()
