"""Daily game scanner - identifies heavy favorites to monitor."""
from datetime import datetime
import kalshi
import state
import config


def scan_games_for_date(date: str) -> int:
    """Scan NBA games for a specific date and add heavy favorites to monitoring.

    Args:
        date: Date string (YYYY-MM-DD)

    Returns:
        Number of games added to monitoring
    """
    print(f"Scanning games for {date}...")

    # Get all NBA events
    events = kalshi.get_nba_events()
    todays_events = [e for e in events if e["game_date"] == date]
    print(f"Found {len(todays_events)} NBA games on {date}")

    games_added = 0
    for event in todays_events:
        odds = kalshi.get_game_odds(event["event_ticker"])
        if not odds:
            print(f"  {event['event_ticker']}: No odds available")
            continue

        american = odds["favorite_american"]

        # Skip finished games
        if odds.get("is_finished"):
            print(f"  {event['event_ticker']}: Game already finished, skipping")
            continue

        # Check if it's a heavy favorite
        if odds["favorite_prob"] < config.PREGAME_THRESHOLD:
            print(f"  {event['event_ticker']}: {odds['favorite']} {american} - below threshold, skipping")
            continue

        # Add to monitoring
        added = state.add_monitored_game(
            ticker=event["event_ticker"],
            game_date=event["game_date"],
            home_team=event["home_team"],
            away_team=event["away_team"],
            favorite_team=odds["favorite"],
            pregame_odds=odds["favorite_prob"]
        )

        if added:
            print(f"  {event['event_ticker']}: {odds['favorite']} {american} - ADDED to monitoring")
            games_added += 1
        else:
            print(f"  {event['event_ticker']}: Already monitoring")

    print(f"\nAdded {games_added} new games to monitor")
    return games_added


def scan_todays_games() -> int:
    """Scan today's NBA games."""
    today = datetime.now().strftime("%Y-%m-%d")
    return scan_games_for_date(today)


def scan_tomorrows_games() -> int:
    """Scan tomorrow's NBA games."""
    from datetime import timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return scan_games_for_date(tomorrow)


def get_monitored_summary() -> str:
    """Get summary of currently monitored games."""
    games = state.get_active_games()

    if not games:
        return "No games being monitored."

    lines = [f"Monitoring {len(games)} games:"]
    for game in games:
        american = kalshi.probability_to_american(game["pregame_odds"])
        lines.append(f"  {game['favorite_team']} {american} vs {game['away_team'] if game['favorite_team'] == game['home_team'] else game['home_team']} ({game['game_date']})")

    return "\n".join(lines)


if __name__ == "__main__":
    # Scan for tomorrow since today's games may be done
    from datetime import timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"=== Scanning for {tomorrow} ===\n")
    scan_games_for_date(tomorrow)

    print("\n=== Current Monitoring Status ===")
    print(get_monitored_summary())
