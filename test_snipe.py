"""Comprehensive test suite for NBA Odds Sniper."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

import config
import kalshi
import scores
import state
import notifier
import poller
import scanner


# ============================================================
# 1. Notification Decision Logic (state.should_notify)
# ============================================================

class TestShouldNotify:
    """Tests for the core notification decision logic."""

    def test_above_entry_threshold_returns_false(self, add_game_to_db):
        """Odds above 0.60 should never trigger notification."""
        ticker = add_game_to_db()
        assert state.should_notify(ticker, 0.65, period=1) is False
        assert state.should_notify(ticker, 0.75, period=1) is False
        assert state.should_notify(ticker, 1.0, period=1) is False

    def test_at_entry_threshold_returns_true(self, add_game_to_db):
        """Odds exactly at 0.60 should trigger notification."""
        ticker = add_game_to_db()
        assert state.should_notify(ticker, 0.60, period=1) is True

    def test_below_entry_threshold_returns_true(self, add_game_to_db):
        """Odds below 0.60 should trigger notification."""
        ticker = add_game_to_db()
        assert state.should_notify(ticker, 0.50, period=1) is True
        assert state.should_notify(ticker, 0.30, period=1) is True

    def test_first_notification_always_fires(self, add_game_to_db):
        """First notification (last_notified_odds is None) always fires."""
        ticker = add_game_to_db()
        assert state.should_notify(ticker, 0.55, period=2) is True

    def test_new_quarter_resets_baseline(self, add_game_to_db):
        """Changing quarter should allow notification even with worse odds."""
        ticker = add_game_to_db()

        # First notification in Q1 at 0.40
        state.update_last_notification(ticker, 0.40, period=1)

        # Q2 at 0.55 (worse odds but new quarter) — should fire
        assert state.should_notify(ticker, 0.55, period=2) is True

    def test_same_quarter_requires_improvement(self, add_game_to_db):
        """Same quarter: only notify if odds improved (lower probability)."""
        ticker = add_game_to_db()

        # First notification in Q1 at 0.50
        state.update_last_notification(ticker, 0.50, period=1)

        # Same quarter, same odds — should NOT fire
        assert state.should_notify(ticker, 0.50, period=1) is False

        # Same quarter, worse odds — should NOT fire
        assert state.should_notify(ticker, 0.55, period=1) is False

        # Same quarter, better odds — should fire
        assert state.should_notify(ticker, 0.45, period=1) is True

    def test_ot_periods_get_fresh_reset(self, add_game_to_db):
        """Each OT period should trigger a fresh notification."""
        ticker = add_game_to_db()

        # Notified in Q4 at 0.40
        state.update_last_notification(ticker, 0.40, period=4)

        # OT1 (period=5) at 0.55 — should fire (new period)
        assert state.should_notify(ticker, 0.55, period=5) is True

        # Record OT1 notification
        state.update_last_notification(ticker, 0.55, period=5)

        # OT2 (period=6) at 0.58 — should fire (new period)
        assert state.should_notify(ticker, 0.58, period=6) is True

    def test_pregame_period_zero(self, add_game_to_db):
        """Pregame notifications use period=0."""
        ticker = add_game_to_db()

        # First pregame notification
        assert state.should_notify(ticker, 0.55, period=0) is True

        state.update_last_notification(ticker, 0.55, period=0)

        # Same period=0, worse odds — should NOT fire
        assert state.should_notify(ticker, 0.58, period=0) is False

        # Same period=0, better odds — should fire
        assert state.should_notify(ticker, 0.50, period=0) is True

    def test_nonexistent_ticker_returns_false(self):
        """Unknown ticker should return False."""
        assert state.should_notify("FAKE-TICKER", 0.40, period=1) is False

    def test_full_game_simulation(self, add_game_to_db):
        """Simulate a full game with notifications across quarters."""
        ticker = add_game_to_db()

        # Q1: Odds drop to 0.55 — first notification
        assert state.should_notify(ticker, 0.55, period=1) is True
        state.update_last_notification(ticker, 0.55, period=1)

        # Q1: Odds drop further to 0.40 — improvement, notify again
        assert state.should_notify(ticker, 0.40, period=1) is True
        state.update_last_notification(ticker, 0.40, period=1)

        # Q1: Odds bounce back to 0.50 — worse than 0.40, no notify
        assert state.should_notify(ticker, 0.50, period=1) is False

        # Q2: Odds at 0.58 — new quarter, fires even though worse than Q1's 0.40
        assert state.should_notify(ticker, 0.58, period=2) is True
        state.update_last_notification(ticker, 0.58, period=2)

        # Q3: Odds at 0.65 — above threshold, no notify
        assert state.should_notify(ticker, 0.65, period=3) is False

        # Q4: Odds at 0.50 — new quarter, below threshold, fires
        assert state.should_notify(ticker, 0.50, period=4) is True
        state.update_last_notification(ticker, 0.50, period=4)

        # OT1: Odds at 0.60 — new period, at threshold, fires
        assert state.should_notify(ticker, 0.60, period=5) is True


# ============================================================
# 2. Odds Conversion (kalshi)
# ============================================================

class TestOddsConversion:
    """Tests for probability <-> American odds conversion."""

    def test_probability_to_american_heavy_favorite(self):
        assert kalshi.probability_to_american(0.75) == -300

    def test_probability_to_american_moderate_favorite(self):
        assert kalshi.probability_to_american(0.60) == -150

    def test_probability_to_american_even(self):
        # At exactly 0.50, formula gives -100
        assert kalshi.probability_to_american(0.50) == -100

    def test_probability_to_american_underdog(self):
        assert kalshi.probability_to_american(0.40) == 150

    def test_probability_to_american_heavy_underdog(self):
        assert kalshi.probability_to_american(0.25) == 300

    def test_probability_to_american_extreme_favorite(self):
        result = kalshi.probability_to_american(0.90)
        assert result == -900

    def test_probability_to_american_extreme_underdog(self):
        result = kalshi.probability_to_american(0.10)
        assert result == 900


# ============================================================
# 3. Notification Formatting (notifier)
# ============================================================

class TestNotificationFormatting:
    """Tests for notification message formatting."""

    def test_format_american_positive(self):
        assert notifier.format_american(163) == "+163"

    def test_format_american_negative(self):
        assert notifier.format_american(-300) == "-300"

    def test_format_american_zero(self):
        assert notifier.format_american(0) == "0"

    def test_format_notification_live_losing(self):
        msg = notifier.format_notification(
            favorite_team="DET",
            underdog_team="UTA",
            favorite_score=45,
            underdog_score=55,
            period=2,
            clock="4:32",
            current_odds=-145,
            pregame_odds=-600,
        )
        assert "PISTONS" in msg
        assert "losing to" in msg
        assert "45-55" in msg
        assert "Q2" in msg
        assert "4:32 remaining" in msg
        assert "-145" in msg
        assert "-600" in msg

    def test_format_notification_live_leading(self):
        msg = notifier.format_notification(
            favorite_team="BOS",
            underdog_team="PHX",
            favorite_score=60,
            underdog_score=50,
            period=3,
            clock="8:00",
            current_odds=-150,
            pregame_odds=-400,
        )
        assert "CELTICS" in msg
        assert "leading" in msg

    def test_format_notification_live_tied(self):
        msg = notifier.format_notification(
            favorite_team="LAL",
            underdog_team="GSW",
            favorite_score=50,
            underdog_score=50,
            period=1,
            clock="2:00",
            current_odds=-130,
            pregame_odds=-350,
        )
        assert "LAKERS" in msg
        assert "tied with" in msg

    def test_format_notification_pregame(self):
        msg = notifier.format_notification(
            favorite_team="CLE",
            underdog_team="NYK",
            favorite_score=0,
            underdog_score=0,
            period=0,
            clock="Pregame",
            current_odds=-140,
            pregame_odds=-500,
        )
        assert "CAVALIERS" in msg
        assert "vs" in msg
        assert "Pregame" in msg
        assert "losing" not in msg

    def test_format_notification_positive_odds(self):
        msg = notifier.format_notification(
            favorite_team="MIA",
            underdog_team="BKN",
            favorite_score=40,
            underdog_score=60,
            period=2,
            clock="1:00",
            current_odds=150,
            pregame_odds=-300,
        )
        assert "+150" in msg
        assert "-300" in msg


# ============================================================
# 4. Clock Formatting (scores)
# ============================================================

class TestClockFormatting:
    """Tests for game clock format parsing."""

    def test_standard_clock(self):
        assert scores.format_clock("PT04M32.00S") == "4:32"

    def test_zero_minutes(self):
        assert scores.format_clock("PT00M05.00S") == "0:05"

    def test_full_quarter(self):
        assert scores.format_clock("PT12M00.00S") == "12:00"

    def test_seconds_only(self):
        assert scores.format_clock("PT00M00.50S") == "0:00"

    def test_empty_string(self):
        assert scores.format_clock("") == ""

    def test_none_input(self):
        assert scores.format_clock(None) == ""

    def test_non_iso_passthrough(self):
        assert scores.format_clock("4:32") == "4:32"

    def test_no_minutes(self):
        assert scores.format_clock("PT30.00S") == "0:30"


# ============================================================
# 5. Period Display (scores)
# ============================================================

class TestPeriodToString:
    """Tests for period number to display string conversion."""

    def test_pregame(self):
        assert scores.period_to_string(0) == "Pre-game"

    def test_negative(self):
        assert scores.period_to_string(-1) == "Pre-game"

    def test_quarter_1(self):
        assert scores.period_to_string(1) == "Q1"

    def test_quarter_4(self):
        assert scores.period_to_string(4) == "Q4"

    def test_overtime_1(self):
        assert scores.period_to_string(5) == "OT1"

    def test_overtime_2(self):
        assert scores.period_to_string(6) == "OT2"

    def test_overtime_3(self):
        assert scores.period_to_string(7) == "OT3"


# ============================================================
# 6. Event Ticker Parsing (kalshi)
# ============================================================

class TestParseEventTicker:
    """Tests for Kalshi event ticker parsing."""

    def test_valid_ticker(self):
        result = kalshi.parse_event_ticker("KXNBAGAME-26FEB24BOSPHX")
        assert result is not None
        assert result["away_team"] == "BOS"
        assert result["home_team"] == "PHX"
        assert result["game_date"] == "2026-02-24"

    def test_valid_ticker_march(self):
        result = kalshi.parse_event_ticker("KXNBAGAME-26MAR03LACLAL")
        assert result is not None
        assert result["away_team"] == "LAC"
        assert result["home_team"] == "LAL"
        assert result["game_date"] == "2026-03-03"

    def test_invalid_prefix(self):
        result = kalshi.parse_event_ticker("KXNFLGAME-26FEB24BOSPHX")
        assert result is None

    def test_too_short(self):
        result = kalshi.parse_event_ticker("KXNBAGAME-26FEB")
        assert result is None

    def test_empty_string(self):
        result = kalshi.parse_event_ticker("")
        assert result is None


# ============================================================
# 7. State Management (SQLite)
# ============================================================

class TestStateManagement:
    """Tests for SQLite state operations."""

    def test_add_game_returns_true(self):
        result = state.add_monitored_game(
            ticker="KXNBAGAME-26MAR03BOSPHX",
            game_date="2026-03-03",
            home_team="PHX",
            away_team="BOS",
            favorite_team="BOS",
            pregame_odds=0.80,
            start_time="2026-03-03T02:00:00Z",
        )
        assert result is True

    def test_add_duplicate_returns_false(self):
        state.add_monitored_game(
            ticker="KXNBAGAME-26MAR03BOSPHX",
            game_date="2026-03-03",
            home_team="PHX",
            away_team="BOS",
            favorite_team="BOS",
            pregame_odds=0.80,
        )
        result = state.add_monitored_game(
            ticker="KXNBAGAME-26MAR03BOSPHX",
            game_date="2026-03-03",
            home_team="PHX",
            away_team="BOS",
            favorite_team="BOS",
            pregame_odds=0.80,
        )
        assert result is False

    def test_get_active_games(self, add_game_to_db):
        add_game_to_db()
        games = state.get_active_games()
        assert len(games) == 1
        assert games[0]["ticker"] == "KXNBAGAME-26MAR03BOSPHX"
        assert games[0]["status"] == "active"

    def test_get_active_games_excludes_complete(self, add_game_to_db):
        ticker = add_game_to_db()
        state.mark_game_complete(ticker)
        games = state.get_active_games()
        assert len(games) == 0

    def test_get_active_games_by_date(self, add_game_to_db):
        add_game_to_db(ticker="KXNBAGAME-26MAR03BOSPHX", game_date="2026-03-03")
        add_game_to_db(ticker="KXNBAGAME-26MAR04LACLAL", game_date="2026-03-04")
        games = state.get_active_games(game_date="2026-03-03")
        assert len(games) == 1

    def test_mark_game_complete(self, add_game_to_db):
        ticker = add_game_to_db()
        state.mark_game_complete(ticker)
        game = state.get_game_by_ticker(ticker)
        assert game["status"] == "complete"

    def test_update_pregame_odds(self, add_game_to_db):
        ticker = add_game_to_db(pregame_odds=0.80)
        state.update_pregame_odds(ticker, 0.78)
        game = state.get_game_by_ticker(ticker)
        assert game["pregame_odds"] == 0.78

    def test_update_last_notification(self, add_game_to_db):
        ticker = add_game_to_db()
        state.update_last_notification(ticker, 0.55, period=2)
        game = state.get_game_by_ticker(ticker)
        assert game["last_notified_odds"] == 0.55
        assert game["last_notified_quarter"] == 2
        assert game["last_notification_time"] is not None

    def test_log_notification(self, add_game_to_db):
        ticker = add_game_to_db()
        state.log_notification(
            ticker=ticker,
            odds=0.55,
            home_score=45,
            away_score=50,
            period=2,
            clock="4:32",
        )
        # Verify it was inserted
        conn = state.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE ticker = ?", (ticker,))
        rows = cursor.fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["odds_at_notification"] == 0.55

    def test_get_game_by_ticker(self, add_game_to_db):
        ticker = add_game_to_db()
        game = state.get_game_by_ticker(ticker)
        assert game is not None
        assert game["favorite_team"] == "BOS"

    def test_get_game_by_ticker_not_found(self):
        game = state.get_game_by_ticker("NONEXISTENT")
        assert game is None

    def test_get_earliest_start_time(self, add_game_to_db):
        add_game_to_db(
            ticker="KXNBAGAME-26MAR03GAME1",
            start_time="2026-03-03T23:00:00Z",
        )
        add_game_to_db(
            ticker="KXNBAGAME-26MAR03GAME2",
            start_time="2026-03-03T20:00:00Z",
        )
        earliest = state.get_earliest_start_time()
        assert earliest == "2026-03-03T20:00:00Z"

    def test_get_earliest_start_time_no_games(self):
        earliest = state.get_earliest_start_time()
        assert earliest is None

    def test_cleanup_old_games(self, add_game_to_db):
        add_game_to_db(
            ticker="KXNBAGAME-26FEB20OLD",
            game_date="2026-02-20",
        )
        add_game_to_db(
            ticker="KXNBAGAME-26MAR03NEW",
            game_date="2026-03-03",
        )
        state.cleanup_old_games(days_old=7)
        games = state.get_active_games()
        # Old game should be deleted, new game should remain
        tickers = [g["ticker"] for g in games]
        assert "KXNBAGAME-26FEB20OLD" not in tickers
        assert "KXNBAGAME-26MAR03NEW" in tickers


# ============================================================
# 8. Poller Logic (mocked API calls)
# ============================================================

class TestPollerProcessGame:
    """Tests for poller.process_game with mocked external calls."""

    def _make_game(self, **overrides):
        game = {
            "ticker": "KXNBAGAME-26MAR03BOSPHX",
            "home_team": "PHX",
            "away_team": "BOS",
            "favorite_team": "BOS",
            "pregame_odds": 0.80,
            "start_time": "2026-03-03T02:00:00Z",
            "last_notified_odds": None,
            "last_notified_quarter": None,
        }
        game.update(overrides)
        return game

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_no_odds_available(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        add_game_to_db()
        mock_odds.return_value = None
        poller.process_game(self._make_game())
        mock_notifier.send_notification.assert_not_called()

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_game_final_marks_complete(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        ticker = add_game_to_db()
        mock_odds.return_value = {"probability": 0.55, "american": -122}
        mock_find.return_value = {"status": "final", "home_score": 100, "away_score": 95,
                                  "period": 4, "clock": "0:00"}
        poller.process_game(self._make_game())
        game = state.get_game_by_ticker(ticker)
        assert game["status"] == "complete"

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_game_scheduled_no_action(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        add_game_to_db()
        mock_odds.return_value = {"probability": 0.75, "american": -300}
        mock_find.return_value = {"status": "scheduled"}
        poller.process_game(self._make_game())
        mock_notifier.send_notification.assert_not_called()

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_live_above_threshold_no_notification(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        add_game_to_db()
        mock_odds.return_value = {"probability": 0.70, "american": -233}
        mock_find.return_value = {
            "status": "live", "home_score": 30, "away_score": 35,
            "period": 1, "clock": "5:00",
        }
        poller.process_game(self._make_game())
        mock_notifier.send_notification.assert_not_called()

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_live_below_threshold_sends_notification(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        add_game_to_db()
        mock_odds.return_value = {"probability": 0.55, "american": -122}
        mock_find.return_value = {
            "status": "live", "home_score": 30, "away_score": 45,
            "period": 2, "clock": "3:00",
        }
        poller.process_game(self._make_game())
        mock_notifier.send_notification.assert_called_once()

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_live_new_quarter_sends_notification(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        """New quarter should fire even if odds are worse than previous quarter."""
        ticker = add_game_to_db()
        # Simulate previous Q1 notification at 0.40
        state.update_last_notification(ticker, 0.40, period=1)

        mock_odds.return_value = {"probability": 0.55, "american": -122}
        mock_find.return_value = {
            "status": "live", "home_score": 50, "away_score": 55,
            "period": 2, "clock": "8:00",
        }
        poller.process_game(self._make_game())
        mock_notifier.send_notification.assert_called_once()

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_find_game_returns_none_checks_pregame(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        """When find_game_by_teams returns None, should try pregame notification."""
        add_game_to_db()
        mock_odds.return_value = {"probability": 0.55, "american": -122}
        mock_find.return_value = None

        poller.process_game(self._make_game())
        # Pregame notification should fire since odds are below threshold
        mock_notifier.send_notification.assert_called_once()
        # Verify it used pregame parameters
        call_kwargs = mock_notifier.send_notification.call_args
        assert call_kwargs[1]["clock"] == "Pregame" or call_kwargs.kwargs.get("clock") == "Pregame"

    @patch("poller.notifier")
    @patch("poller.scores.find_game_by_teams")
    @patch("poller.kalshi.get_team_odds")
    def test_favorite_is_home_team(self, mock_odds, mock_find, mock_notifier, add_game_to_db):
        """When favorite is the home team, scores should be assigned correctly."""
        add_game_to_db(
            ticker="KXNBAGAME-26MAR03BOSPHX",
            home_team="PHX",
            away_team="BOS",
            favorite_team="PHX",
        )
        mock_odds.return_value = {"probability": 0.55, "american": -122}
        mock_find.return_value = {
            "status": "live", "home_score": 40, "away_score": 50,
            "period": 1, "clock": "5:00",
        }
        game = self._make_game(favorite_team="PHX")
        poller.process_game(game)
        mock_notifier.send_notification.assert_called_once()
        call_args = mock_notifier.send_notification.call_args
        # Favorite (PHX) is home, so favorite_score=40 (home), underdog_score=50 (away)
        assert call_args.kwargs["favorite_score"] == 40
        assert call_args.kwargs["underdog_score"] == 50


# ============================================================
# 9. Scanner Logic (mocked API calls)
# ============================================================

class TestScanner:
    """Tests for scanner.scan_games_for_date with mocked APIs."""

    @patch("scanner.scores.get_todays_games")
    @patch("scanner.kalshi.get_game_odds")
    @patch("scanner.kalshi.get_nba_events")
    def test_heavy_favorite_gets_tracked(self, mock_events, mock_odds, mock_scores):
        mock_events.return_value = [{
            "event_ticker": "KXNBAGAME-26MAR03BOSPHX",
            "game_date": "2026-03-03",
            "home_team": "PHX",
            "away_team": "BOS",
        }]
        mock_odds.return_value = {
            "favorite": "BOS",
            "underdog": "PHX",
            "favorite_prob": 0.80,
            "underdog_prob": 0.20,
            "favorite_american": -400,
            "underdog_american": 400,
            "is_finished": False,
        }
        mock_scores.return_value = [{
            "home_team": "PHX",
            "away_team": "BOS",
            "start_time": "2026-03-03T02:00:00Z",
        }]

        tracked, untracked = scanner.scan_games_for_date("2026-03-03")
        assert len(tracked) == 1
        assert len(untracked) == 0
        assert tracked[0]["favorite"] == "BOS"

    @patch("scanner.scores.get_todays_games")
    @patch("scanner.kalshi.get_game_odds")
    @patch("scanner.kalshi.get_nba_events")
    def test_light_favorite_not_tracked(self, mock_events, mock_odds, mock_scores):
        mock_events.return_value = [{
            "event_ticker": "KXNBAGAME-26MAR03BOSPHX",
            "game_date": "2026-03-03",
            "home_team": "PHX",
            "away_team": "BOS",
        }]
        mock_odds.return_value = {
            "favorite": "BOS",
            "underdog": "PHX",
            "favorite_prob": 0.60,
            "underdog_prob": 0.40,
            "favorite_american": -150,
            "underdog_american": 150,
            "is_finished": False,
        }
        mock_scores.return_value = []

        tracked, untracked = scanner.scan_games_for_date("2026-03-03")
        assert len(tracked) == 0
        assert len(untracked) == 1

    @patch("scanner.scores.get_todays_games")
    @patch("scanner.kalshi.get_game_odds")
    @patch("scanner.kalshi.get_nba_events")
    def test_finished_game_skipped(self, mock_events, mock_odds, mock_scores):
        mock_events.return_value = [{
            "event_ticker": "KXNBAGAME-26MAR03BOSPHX",
            "game_date": "2026-03-03",
            "home_team": "PHX",
            "away_team": "BOS",
        }]
        mock_odds.return_value = {
            "favorite": "BOS",
            "underdog": "PHX",
            "favorite_prob": 0.99,
            "underdog_prob": 0.01,
            "favorite_american": -9900,
            "underdog_american": 9900,
            "is_finished": True,
        }
        mock_scores.return_value = []

        tracked, untracked = scanner.scan_games_for_date("2026-03-03")
        assert len(tracked) == 0
        assert len(untracked) == 0

    @patch("scanner.scores.get_todays_games")
    @patch("scanner.kalshi.get_game_odds")
    @patch("scanner.kalshi.get_nba_events")
    def test_no_odds_skipped(self, mock_events, mock_odds, mock_scores):
        mock_events.return_value = [{
            "event_ticker": "KXNBAGAME-26MAR03BOSPHX",
            "game_date": "2026-03-03",
            "home_team": "PHX",
            "away_team": "BOS",
        }]
        mock_odds.return_value = None
        mock_scores.return_value = []

        tracked, untracked = scanner.scan_games_for_date("2026-03-03")
        assert len(tracked) == 0
        assert len(untracked) == 0

    @patch("scanner.scores.get_todays_games")
    @patch("scanner.kalshi.get_game_odds")
    @patch("scanner.kalshi.get_nba_events")
    def test_duplicate_game_not_readded(self, mock_events, mock_odds, mock_scores, add_game_to_db):
        """Running scan twice shouldn't create duplicate DB entries."""
        add_game_to_db()  # Pre-add the game

        mock_events.return_value = [{
            "event_ticker": "KXNBAGAME-26MAR03BOSPHX",
            "game_date": "2026-03-03",
            "home_team": "PHX",
            "away_team": "BOS",
        }]
        mock_odds.return_value = {
            "favorite": "BOS",
            "underdog": "PHX",
            "favorite_prob": 0.80,
            "underdog_prob": 0.20,
            "favorite_american": -400,
            "underdog_american": 400,
            "is_finished": False,
        }
        mock_scores.return_value = []

        tracked, _ = scanner.scan_games_for_date("2026-03-03")
        # Should still be tracked (shows in results) but not duplicated in DB
        assert len(tracked) == 1
        games = state.get_active_games()
        assert len(games) == 1


# ============================================================
# 10. Main Loop Logic (mocked)
# ============================================================

class TestMainLoop:
    """Tests for main.py helper functions."""

    @patch("scores.get_todays_games")
    def test_run_slate_scan_skips_when_live(self, mock_scores):
        """Slate scan should not run when games are already live."""
        mock_scores.return_value = [{"status": "live"}]
        with patch("scanner.scan_and_notify") as mock_scan:
            import main
            main.run_slate_scan()
            mock_scan.assert_not_called()

    @patch("scores.get_todays_games")
    def test_run_slate_scan_runs_when_no_live(self, mock_scores):
        """Slate scan should run when no games are live."""
        mock_scores.return_value = [{"status": "scheduled"}]
        with patch("scanner.scan_and_notify") as mock_scan:
            import main
            main.run_slate_scan()
            mock_scan.assert_called_once()

    @patch("scores.get_todays_games")
    def test_run_slate_scan_runs_on_api_failure(self, mock_scores):
        """Slate scan should run even if NBA API fails."""
        mock_scores.side_effect = Exception("API down")
        with patch("scanner.scan_and_notify") as mock_scan:
            import main
            main.run_slate_scan()
            mock_scan.assert_called_once()

    def test_get_seconds_until_first_game_no_games(self):
        """Should return 0 when no active games."""
        import main
        result = main.get_seconds_until_first_game()
        assert result == 0

    def test_get_seconds_until_first_game_with_game(self, add_game_to_db):
        """Should calculate seconds until game minus 5 minutes."""
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        add_game_to_db(start_time=future)
        import main
        result = main.get_seconds_until_first_game()
        # Should be roughly 2 hours minus 5 minutes = ~6900 seconds
        assert 6500 < result < 7500

    @patch("kalshi.get_team_odds")
    @patch("notifier.send_game_starting")
    def test_pregame_refresh_within_5_min(self, mock_notify, mock_odds, add_game_to_db):
        """Games within 5 minutes of start should get odds refreshed."""
        near_start = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        add_game_to_db(start_time=near_start)

        mock_odds.return_value = {
            "probability": 0.78,
            "american": -354,
        }

        import main
        main._refreshed_games.clear()
        main.check_pregame_refreshes()

        mock_odds.assert_called_once()
        mock_notify.assert_called_once()

    @patch("kalshi.get_team_odds")
    def test_pregame_refresh_skips_already_refreshed(self, mock_odds, add_game_to_db):
        """Already-refreshed games should be skipped."""
        near_start = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        ticker = add_game_to_db(start_time=near_start)

        import main
        main._refreshed_games.clear()
        main._refreshed_games.add(ticker)
        main.check_pregame_refreshes()

        mock_odds.assert_not_called()

    @patch("kalshi.get_team_odds")
    def test_pregame_refresh_skips_far_games(self, mock_odds, add_game_to_db):
        """Games more than 5 minutes away should not be refreshed."""
        far_start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        add_game_to_db(start_time=far_start)

        import main
        main._refreshed_games.clear()
        main.check_pregame_refreshes()

        mock_odds.assert_not_called()


# ============================================================
# 11. Kalshi API Response Parsing
# ============================================================

class TestKalshiMarketParsing:
    """Tests for Kalshi market response parsing logic."""

    @patch("kalshi.requests.get")
    def test_get_event_markets_bid_ask_midpoint(self, mock_get):
        """Probability should be midpoint of bid/ask."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markets": [
                {"ticker": "KXNBAGAME-26MAR03BOSPHX-BOS",
                 "yes_bid": 70, "yes_ask": 80, "last_price": 75},
                {"ticker": "KXNBAGAME-26MAR03BOSPHX-PHX",
                 "yes_bid": 20, "yes_ask": 30, "last_price": 25},
            ]}
        )
        mock_get.return_value.raise_for_status = lambda: None

        markets = kalshi.get_event_markets("KXNBAGAME-26MAR03BOSPHX")
        assert len(markets) == 2
        # BOS: (70+80)/200 = 0.375 — wait, that's wrong
        # Actually: (70+80)/200 = 0.75
        assert markets[0]["probability"] == 0.75
        assert markets[1]["probability"] == 0.25

    @patch("kalshi.requests.get")
    def test_get_event_markets_fallback_to_last_price(self, mock_get):
        """When no bid/ask, should fall back to last_price."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markets": [
                {"ticker": "KXNBAGAME-26MAR03BOSPHX-BOS",
                 "yes_bid": 0, "yes_ask": 0, "last_price": 72},
            ]}
        )
        mock_get.return_value.raise_for_status = lambda: None

        markets = kalshi.get_event_markets("KXNBAGAME-26MAR03BOSPHX")
        assert len(markets) == 1
        assert markets[0]["probability"] == 0.72

    @patch("kalshi.requests.get")
    def test_get_event_markets_no_data_defaults(self, mock_get):
        """When no bid/ask/last_price, should default to 0.50."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"markets": [
                {"ticker": "KXNBAGAME-26MAR03BOSPHX-BOS",
                 "yes_bid": None, "yes_ask": None, "last_price": None},
            ]}
        )
        mock_get.return_value.raise_for_status = lambda: None

        markets = kalshi.get_event_markets("KXNBAGAME-26MAR03BOSPHX")
        assert markets[0]["probability"] == 0.50

    def test_get_game_odds_needs_exactly_2_markets(self):
        """get_game_odds should return None if != 2 markets."""
        with patch("kalshi.get_event_markets") as mock:
            mock.return_value = [{"team": "BOS", "probability": 0.80}]
            result = kalshi.get_game_odds("KXNBAGAME-26MAR03BOSPHX")
            assert result is None

    def test_get_game_odds_detects_finished(self):
        """Games with >99% probability should be marked finished."""
        with patch("kalshi.get_event_markets") as mock:
            mock.return_value = [
                {"team": "BOS", "probability": 0.995},
                {"team": "PHX", "probability": 0.005},
            ]
            result = kalshi.get_game_odds("KXNBAGAME-26MAR03BOSPHX")
            assert result["is_finished"] is True

    def test_get_team_odds_returns_correct_team(self):
        """get_team_odds should return odds for the requested team."""
        with patch("kalshi.get_event_markets") as mock:
            mock.return_value = [
                {"team": "BOS", "probability": 0.55},
                {"team": "PHX", "probability": 0.45},
            ]
            result = kalshi.get_team_odds("KXNBAGAME-26MAR03BOSPHX", "PHX")
            assert result["team"] == "PHX"
            assert result["probability"] == 0.45
            assert result["opponent"] == "BOS"

    def test_get_team_odds_team_not_found(self):
        """get_team_odds should return None if team not in markets."""
        with patch("kalshi.get_event_markets") as mock:
            mock.return_value = [
                {"team": "BOS", "probability": 0.55},
                {"team": "PHX", "probability": 0.45},
            ]
            result = kalshi.get_team_odds("KXNBAGAME-26MAR03BOSPHX", "LAL")
            assert result is None
