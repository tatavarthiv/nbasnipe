"""Daily game scanner - identifies heavy favorites to monitor."""
from datetime import datetime
import kalshi
import scores
import state
import config
import notifier


def scan_games_for_date(date: str) -> tuple[list, list]:
    """Scan NBA games for a specific date.

    Returns:
        (tracked_games, untracked_games) - lists of game info dicts
    """
    print(f"Scanning games for {date}...")

    # Get all NBA events from Kalshi
    events = kalshi.get_nba_events()
    todays_events = [e for e in events if e["game_date"] == date]
    print(f"Found {len(todays_events)} NBA games on {date}")

    # Get start times from NBA API
    nba_games = scores.get_todays_games()
    start_times = {}
    for g in nba_games:
        key = f"{g['away_team']}@{g['home_team']}"
        start_times[key] = g.get("start_time", "")

    tracked = []
    untracked = []

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

        # Get start time
        key = f"{event['away_team']}@{event['home_team']}"
        start_time = start_times.get(key, "")

        game_info = {
            "ticker": event["event_ticker"],
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "favorite": odds["favorite"],
            "underdog": odds["underdog"],
            "favorite_prob": odds["favorite_prob"],
            "favorite_american": american,
            "start_time": start_time,
            "game_date": date,
        }

        # Check if it's a heavy favorite
        if odds["favorite_prob"] >= config.PREGAME_THRESHOLD:
            # Add to monitoring
            added = state.add_monitored_game(
                ticker=event["event_ticker"],
                game_date=event["game_date"],
                home_team=event["home_team"],
                away_team=event["away_team"],
                favorite_team=odds["favorite"],
                pregame_odds=odds["favorite_prob"],
                start_time=start_time
            )

            if added:
                print(f"  {event['event_ticker']}: {odds['favorite']} {american} - ADDED to monitoring")
            else:
                print(f"  {event['event_ticker']}: Already monitoring")

            tracked.append(game_info)
        else:
            print(f"  {event['event_ticker']}: {odds['favorite']} {american} - below threshold")
            untracked.append(game_info)

    print(f"\nTracking {len(tracked)} games, not tracking {len(untracked)}")
    return tracked, untracked


def scan_and_notify(date: str):
    """Scan games and send slate notification."""
    tracked, untracked = scan_games_for_date(date)
    notifier.send_slate_notification(date, tracked, untracked)


def scan_todays_games():
    """Scan today's NBA games and send notification."""
    today = datetime.now().strftime("%Y-%m-%d")
    scan_and_notify(today)


def get_monitored_summary() -> str:
    """Get summary of currently monitored games."""
    games = state.get_active_games()

    if not games:
        return "No games being monitored."

    lines = [f"Monitoring {len(games)} games:"]
    for game in games:
        american = kalshi.probability_to_american(game["pregame_odds"])
        start = format_start_time(game.get("start_time", ""))
        lines.append(f"  {game['favorite_team']} {american} vs {game['away_team'] if game['favorite_team'] == game['home_team'] else game['home_team']} @ {start}")

    return "\n".join(lines)


def format_start_time(iso_time: str) -> str:
    """Format ISO time to readable format like '7:00 PM ET'."""
    if not iso_time:
        return "TBD"
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        pt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
        return pt.strftime("%-I:%M %p PT")
    except:
        return iso_time


if __name__ == "__main__":
    from datetime import timedelta
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"=== Scanning for {tomorrow} ===\n")
    tracked, untracked = scan_games_for_date(tomorrow)

    print("\n=== Current Monitoring Status ===")
    print(get_monitored_summary())
