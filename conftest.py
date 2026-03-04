"""Shared test fixtures."""
import os
import sqlite3
import tempfile
import pytest


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    """Use a temporary database for each test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.DB_PATH", path)

    # Re-initialize tables in the temp DB
    import state
    state.init_db()

    yield path

    os.unlink(path)


@pytest.fixture
def sample_game():
    """A standard monitored game dict as returned by state.get_active_games()."""
    return {
        "id": 1,
        "ticker": "KXNBAGAME-26MAR03BOSPHX",
        "game_date": "2026-03-03",
        "home_team": "PHX",
        "away_team": "BOS",
        "favorite_team": "BOS",
        "pregame_odds": 0.80,
        "start_time": "2026-03-03T02:00:00Z",
        "last_notified_odds": None,
        "last_notified_quarter": None,
        "last_notification_time": None,
        "status": "active",
        "created_at": "2026-03-03T00:00:00",
    }


@pytest.fixture
def add_game_to_db():
    """Helper to add a game to the test database."""
    import state

    def _add(
        ticker="KXNBAGAME-26MAR03BOSPHX",
        game_date="2026-03-03",
        home_team="PHX",
        away_team="BOS",
        favorite_team="BOS",
        pregame_odds=0.80,
        start_time="2026-03-03T02:00:00Z",
    ):
        state.add_monitored_game(
            ticker=ticker,
            game_date=game_date,
            home_team=home_team,
            away_team=away_team,
            favorite_team=favorite_team,
            pregame_odds=pregame_odds,
            start_time=start_time,
        )
        return ticker

    return _add
