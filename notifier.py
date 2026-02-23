"""Telegram notification service."""
import asyncio
from telegram import Bot
from telegram.error import TelegramError
import config
import scores


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

Current odds: {current_odds}
Pregame odds: {pregame_odds}"""

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


def send_startup_message():
    """Send a message when the bot starts."""
    message = "NBA Odds Sniper is now active and monitoring games."
    asyncio.run(send_telegram_message(message))


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
