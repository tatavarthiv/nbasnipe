"""Main entry point - runs the scanner and poller."""
import time
import schedule
from datetime import datetime, timedelta
import scanner
import poller
import notifier
import state
import scores
import config


def run_daily_scan():
    """Run the daily scanner for today and send slate notification."""
    print(f"\n[{datetime.now()}] Running daily scanner...")
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        scanner.scan_and_notify(today)
    except Exception as e:
        print(f"Scanner error: {e}")


def get_seconds_until_first_game() -> int:
    """Calculate seconds until the first tracked game starts."""
    earliest = state.get_earliest_start_time()
    if not earliest:
        return 0

    try:
        game_time = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
        now = datetime.now(game_time.tzinfo)
        delta = game_time - now
        # Start polling 5 minutes before game
        seconds = int(delta.total_seconds()) - 300
        return max(0, seconds)
    except Exception as e:
        print(f"Error parsing start time: {e}")
        return 0


def poll_live_games():
    """Poll only games that are currently live."""
    active_games = state.get_active_games()
    if not active_games:
        return False

    # Get all game statuses from NBA API (1 request)
    nba_games = scores.get_todays_games()
    game_statuses = {}
    for g in nba_games:
        key = f"{g['away_team']}@{g['home_team']}"
        game_statuses[key] = g

    any_live = False
    for game in active_games:
        key = f"{game['away_team']}@{game['home_team']}"
        nba_game = game_statuses.get(key)

        if not nba_game:
            continue

        status = nba_game.get("status", "scheduled")

        if status == "final":
            print(f"  {game['ticker']}: Game finished, marking complete")
            state.mark_game_complete(game["ticker"])
            continue

        if status == "live":
            any_live = True
            poller.process_game(game)

    return any_live


def run_polling_loop():
    """Main polling loop - polls every second when games are live."""
    print(f"\n[{datetime.now()}] Starting polling loop...")

    while True:
        active_games = state.get_active_games()
        if not active_games:
            print("No active games to monitor. Exiting polling loop.")
            break

        # Poll live games
        try:
            any_live = poll_live_games()
            if not any_live:
                # Check if any games haven't started yet
                nba_games = scores.get_todays_games()
                any_scheduled = any(g["status"] == "scheduled" for g in nba_games)
                if not any_scheduled:
                    print("All games finished. Exiting polling loop.")
                    break
        except Exception as e:
            print(f"Polling error: {e}")

        time.sleep(config.POLL_INTERVAL_SECONDS)


def main():
    """Main entry point."""
    print("=" * 50)
    print("NBA Odds Sniper Starting...")
    print("=" * 50)
    print(f"Pregame threshold: {config.PREGAME_THRESHOLD} (probability)")
    print(f"Entry threshold: {config.ENTRY_THRESHOLD} (probability)")
    print(f"Poll interval: {config.POLL_INTERVAL_SECONDS} second(s)")
    print("=" * 50)

    # Initialize database
    state.init_db()

    # Send startup notification
    notifier.send_startup_message()

    # Run initial scan
    run_daily_scan()

    # Print monitoring status
    print("\n" + scanner.get_monitored_summary())

    # Schedule daily scan at 7 AM PST (10 AM ET / 3 PM UTC)
    schedule.every().day.at("15:00").do(run_daily_scan)

    # Schedule daily cleanup at 3 AM
    schedule.every().day.at("03:00").do(lambda: state.cleanup_old_games(days_old=7))

    print("\nWaiting for games to start...")

    try:
        while True:
            # Run scheduled tasks
            schedule.run_pending()

            # Check if we have active games
            active_games = state.get_active_games()
            if not active_games:
                time.sleep(60)  # Check every minute if no games
                continue

            # Check if any game is live
            nba_games = scores.get_todays_games()
            any_live = any(g["status"] == "live" for g in nba_games)

            if any_live:
                # Enter fast polling mode
                run_polling_loop()
            else:
                # Check how long until first game
                seconds = get_seconds_until_first_game()
                if seconds > 60:
                    print(f"Next game in {seconds // 60} minutes. Sleeping...")
                    time.sleep(min(seconds, 300))  # Sleep up to 5 min
                else:
                    time.sleep(10)  # Check every 10 seconds near game time

    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
