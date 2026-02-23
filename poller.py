"""Live odds poller - monitors active games for betting opportunities."""
from datetime import datetime
import kalshi
import scores
import state
import notifier
import config


def poll_active_games():
    """Check all active monitored games for betting opportunities.

    For each game:
    1. Get current odds from Kalshi
    2. Get live score from nba_api
    3. Check if odds hit threshold
    4. Send notification if first time or improvement
    """
    active_games = state.get_active_games()

    if not active_games:
        return

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Polling {len(active_games)} active games...")

    for game in active_games:
        try:
            process_game(game)
        except Exception as e:
            print(f"  Error processing {game['ticker']}: {e}")


def process_game(game: dict):
    """Process a single monitored game."""
    ticker = game["ticker"]
    favorite_team = game["favorite_team"]

    # Get current odds from Kalshi
    odds = kalshi.get_game_odds(ticker)
    if not odds:
        print(f"  {ticker}: No odds available")
        return

    current_prob = odds["favorite_prob"]
    current_american = odds["favorite_american"]

    # Get live score
    live_game = scores.find_game_by_teams(
        home_team=game["home_team"],
        away_team=game["away_team"]
    )

    if not live_game:
        print(f"  {ticker}: {favorite_team} {current_american} (pregame)")
        check_and_notify_pregame(game, current_prob, current_american)
        return

    # Check game status
    if live_game["status"] == "final":
        print(f"  {ticker}: Game finished, marking complete")
        state.mark_game_complete(ticker)
        return

    if live_game["status"] == "scheduled":
        print(f"  {ticker}: {favorite_team} {current_american} (not started)")
        return

    # Game is live
    home_score = live_game["home_score"]
    away_score = live_game["away_score"]
    period = live_game["period"]
    clock = live_game["clock"]

    # Determine favorite's score
    if favorite_team == game["home_team"]:
        favorite_score = home_score
        underdog_score = away_score
        underdog_team = game["away_team"]
    else:
        favorite_score = away_score
        underdog_score = home_score
        underdog_team = game["home_team"]

    print(f"  {ticker}: {favorite_team} {current_american} ({favorite_score}-{underdog_score} Q{period})")

    # Check if we should notify
    if state.should_notify(ticker, current_prob):
        print(f"    -> SENDING NOTIFICATION!")

        pregame_prob = game["pregame_odds"]
        pregame_american = kalshi.probability_to_american(pregame_prob)

        notifier.send_notification(
            favorite_team=favorite_team,
            underdog_team=underdog_team,
            favorite_score=favorite_score,
            underdog_score=underdog_score,
            period=period,
            clock=clock,
            current_odds=current_american,
            pregame_odds=pregame_american
        )

        state.update_last_notification(ticker, current_prob)
        state.log_notification(
            ticker=ticker,
            odds=current_prob,
            home_score=home_score,
            away_score=away_score,
            period=period,
            clock=clock
        )


def check_and_notify_pregame(game: dict, current_prob: float, current_american: int):
    """Check if pregame odds have dropped enough to notify."""
    ticker = game["ticker"]

    # Only notify if odds have dropped below entry threshold
    if state.should_notify(ticker, current_prob):
        print(f"    -> Pregame odds dropped! SENDING NOTIFICATION!")

        pregame_prob = game["pregame_odds"]
        pregame_american = kalshi.probability_to_american(pregame_prob)

        notifier.send_notification(
            favorite_team=game["favorite_team"],
            underdog_team=game["away_team"] if game["favorite_team"] == game["home_team"] else game["home_team"],
            favorite_score=0,
            underdog_score=0,
            period=0,
            clock="Pregame",
            current_odds=current_american,
            pregame_odds=pregame_american
        )

        state.update_last_notification(ticker, current_prob)


def has_active_games() -> bool:
    """Check if there are any active games to monitor."""
    return len(state.get_active_games()) > 0


if __name__ == "__main__":
    poll_active_games()
