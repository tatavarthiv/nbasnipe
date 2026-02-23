"""Main entry point - runs the scanner and poller."""
import time
import schedule
from datetime import datetime, timedelta
import scanner
import poller
import notifier
import state
import config


def run_scanner():
    """Run the daily scanner for today and tomorrow."""
    print(f"\n[{datetime.now()}] Running daily scanner...")
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        scanner.scan_games_for_date(today)
        scanner.scan_games_for_date(tomorrow)
    except Exception as e:
        print(f"Scanner error: {e}")


def run_poller():
    """Run the poller if there are active games."""
    if not poller.has_active_games():
        return

    try:
        poller.poll_active_games()
    except Exception as e:
        print(f"Poller error: {e}")


def cleanup():
    """Clean up old games."""
    print(f"\n[{datetime.now()}] Cleaning up old games...")
    try:
        state.cleanup_old_games(days_old=7)
    except Exception as e:
        print(f"Cleanup error: {e}")


def main():
    """Main loop with scheduled tasks."""
    print("=" * 50)
    print("NBA Odds Sniper Starting...")
    print("=" * 50)
    print(f"Pregame threshold: {config.PREGAME_THRESHOLD} (probability)")
    print(f"Entry threshold: {config.ENTRY_THRESHOLD} (probability)")
    print(f"Poll interval: {config.POLL_INTERVAL_SECONDS} seconds")
    print("=" * 50)

    # Initialize database
    state.init_db()

    # Send startup notification
    notifier.send_startup_message()

    # Run initial scan
    run_scanner()

    # Print monitoring status
    print("\n" + scanner.get_monitored_summary())

    # Schedule daily scan at 10 AM
    schedule.every().day.at("10:00").do(run_scanner)

    # Schedule daily cleanup at 3 AM
    schedule.every().day.at("03:00").do(cleanup)

    print(f"\nStarting poll loop (every {config.POLL_INTERVAL_SECONDS}s)...")
    print("Press Ctrl+C to stop\n")

    # Main loop
    try:
        while True:
            # Run scheduled tasks
            schedule.run_pending()

            # Poll active games
            run_poller()

            # Sleep until next poll
            time.sleep(config.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
