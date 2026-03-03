"""Telegram notification service."""
import asyncio
from telegram import Bot
from telegram.error import TelegramError
import config
import scores


def format_american(odds: int) -> str:
    """Format American odds with +/- prefix."""
    if odds > 0:
        return f"+{odds}"
    return str(odds)


# Team full names for nicer notifications
TEAM_NAMES = {
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets",
    "CHI": "Bulls", "CLE": "Cavaliers", "DAL": "Mavericks", "DEN": "Nuggets",
    "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets", "IND": "Pacers",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies", "MIA": "Heat",
    "MIL": "Bucks", "MIN": "Timberwolves", "NOP": "Pelicans", "NYK": "Knicks",
    "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "POR": "Trail Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors",
    "UTA": "Jazz", "WAS": "Wizards",
}


def format_notification(
    favorite_team: str,
    underdog_team: str,
    favorite_score: int,
    underdog_score: int,
    period: int,
    clock: str,
    current_odds: int,
    pregame_odds: int
) -> str:
    """Format the notification message.

    Example:
    PISTONS currently losing to JAZZ 45-55
    Q2 | 4:32 remaining

    Current odds: -145
    Pregame odds: -600
    """
    favorite_name = TEAM_NAMES.get(favorite_team, favorite_team)
    underdog_name = TEAM_NAMES.get(underdog_team, underdog_team)

    # Determine game state description
    if period == 0:
        status_line = f"{favorite_name.upper()} vs {underdog_name}"
        time_line = clock if clock else "Pregame"
    else:
        if favorite_score > underdog_score:
            status = "leading"
        elif favorite_score < underdog_score:
            status = "losing to"
        else:
            status = "tied with"

        status_line = f"{favorite_name.upper()} currently {status} {underdog_name} {favorite_score}-{underdog_score}"
        period_str = scores.period_to_string(period)
        time_line = f"{period_str} | {clock} remaining" if clock else period_str

    message = f"""{status_line}
{time_line}

Current odds: {format_american(current_odds)}
Pregame odds: {format_american(pregame_odds)}"""

    return message


async def send_telegram_message(message: str) -> bool:
    """Send a message to the configured Telegram channel."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHANNEL_ID:
        print("Telegram not configured, would send:")
        print("-" * 40)
        print(message)
        print("-" * 40)
        return False

    try:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=config.TELEGRAM_CHANNEL_ID,
            text=message,
            parse_mode=None  # Plain text
        )
        return True
    except TelegramError as e:
        print(f"Error sending Telegram message: {e}")
        return False


def send_notification(
    favorite_team: str,
    underdog_team: str,
    favorite_score: int,
    underdog_score: int,
    period: int,
    clock: str,
    current_odds: int,
    pregame_odds: int
) -> bool:
    """Send a betting opportunity notification."""
    message = format_notification(
        favorite_team=favorite_team,
        underdog_team=underdog_team,
        favorite_score=favorite_score,
        underdog_score=underdog_score,
        period=period,
        clock=clock,
        current_odds=current_odds,
        pregame_odds=pregame_odds
    )

    # Run async send in sync context
    return asyncio.run(send_telegram_message(message))


def send_game_starting(favorite_team: str, underdog_team: str, odds: int):
    """Send notification that a game is about to start and we're tracking it."""
    fav_name = TEAM_NAMES.get(favorite_team, favorite_team)
    und_name = TEAM_NAMES.get(underdog_team, underdog_team)

    message = f"""Now tracking: {fav_name} {format_american(odds)} vs {und_name}
Game starting soon"""

    asyncio.run(send_telegram_message(message))


def send_startup_message():
    """Send a message when the bot starts."""
    message = "Bot redeployed and now monitoring games."
    asyncio.run(send_telegram_message(message))


def send_slate_notification(date: str, tracked: list, untracked: list):
    """Send slate notification with all games for the day."""
    from datetime import datetime

    # Format date nicely
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        date_str = dt.strftime("%b %d")
    except:
        date_str = date

    lines = [f"Today's Slate ({date_str})"]
    lines.append("")

    if tracked:
        lines.append("Tracking:")
        for g in tracked:
            fav = g["favorite"]
            fav_name = TEAM_NAMES.get(fav, fav)
            und = g["underdog"]
            und_name = TEAM_NAMES.get(und, und)
            odds = g["favorite_american"]
            start = format_start_time_short(g.get("start_time", ""))
            time_str = f" @ {start}" if start and start != "TBD" else ""
            lines.append(f"  {fav_name} {odds} vs {und_name}{time_str}")
    else:
        lines.append("Tracking: None (no heavy favorites)")

    lines.append("")

    if untracked:
        lines.append("Not tracking:")
        for g in untracked:
            fav = g["favorite"]
            fav_name = TEAM_NAMES.get(fav, fav)
            und = g["underdog"]
            und_name = TEAM_NAMES.get(und, und)
            odds = g["favorite_american"]
            start = format_start_time_short(g.get("start_time", ""))
            time_str = f" @ {start}" if start and start != "TBD" else ""
            lines.append(f"  {fav_name} {odds} vs {und_name}{time_str}")

    message = "\n".join(lines)
    asyncio.run(send_telegram_message(message))


def format_start_time_short(iso_time: str) -> str:
    """Format ISO time to short format like '7:00 PM'."""
    if not iso_time:
        return "TBD"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        et = dt.astimezone(ZoneInfo("America/New_York"))
        return et.strftime("%-I:%M %p")
    except:
        return "TBD"


if __name__ == "__main__":
    # Test notification
    test_msg = format_notification(
        favorite_team="DET",
        underdog_team="UTA",
        favorite_score=45,
        underdog_score=55,
        period=2,
        clock="4:32",
        current_odds=-145,
        pregame_odds=-600
    )
    print(test_msg)
