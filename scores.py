"""Live NBA scores wrapper using nba_api."""
from typing import Optional
from nba_api.live.nba.endpoints import scoreboard, boxscore


# Team abbreviation mappings (NBA.com uses different codes than Kalshi)
TEAM_ABBREV_MAP = {
    # Kalshi -> NBA.com
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA",
    "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
    "DET": "DET", "GSW": "GSW", "HOU": "HOU", "IND": "IND",
    "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
    "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
    "POR": "POR", "SAC": "SAC", "SAS": "SAS", "TOR": "TOR",
    "UTA": "UTA", "WAS": "WAS",
    # Common variations
    "NOR": "NOP",  # New Orleans
    "PHO": "PHX",  # Phoenix
    "SAN": "SAS",  # San Antonio
    "UTH": "UTA",  # Utah
}


def get_todays_games() -> list[dict]:
    """Fetch all NBA games scheduled for today.

    Returns list of games:
    {
        "game_id": "0022400123",
        "home_team": "CLE",
        "away_team": "NYK",
        "home_score": 0,
        "away_score": 0,
        "status": "scheduled" | "live" | "final",
        "period": 0,
        "clock": "",
        "start_time": "2024-02-26T19:00:00-05:00"
    }
    """
    try:
        sb = scoreboard.ScoreBoard()
        games_data = sb.get_dict()
    except Exception as e:
        print(f"Error fetching scoreboard: {e}")
        return []

    games = []
    for game in games_data.get("scoreboard", {}).get("games", []):
        home_team = game.get("homeTeam", {}).get("teamTricode", "")
        away_team = game.get("awayTeam", {}).get("teamTricode", "")

        # Determine game status
        game_status = game.get("gameStatus", 1)
        if game_status == 1:
            status = "scheduled"
        elif game_status == 2:
            status = "live"
        else:
            status = "final"

        games.append({
            "game_id": game.get("gameId", ""),
            "home_team": home_team,
            "away_team": away_team,
            "home_score": game.get("homeTeam", {}).get("score", 0),
            "away_score": game.get("awayTeam", {}).get("score", 0),
            "status": status,
            "period": game.get("period", 0),
            "clock": game.get("gameClock", ""),
            "start_time": game.get("gameTimeUTC", ""),
        })

    return games


def get_live_score(game_id: str) -> Optional[dict]:
    """Fetch live score for a specific game.

    Returns:
    {
        "home_team": "CLE",
        "away_team": "NYK",
        "home_score": 55,
        "away_score": 48,
        "period": 2,
        "clock": "4:32",
        "status": "live"
    }
    """
    try:
        box = boxscore.BoxScore(game_id=game_id)
        data = box.get_dict()
    except Exception as e:
        print(f"Error fetching boxscore for {game_id}: {e}")
        return None

    game = data.get("game", {})
    if not game:
        return None

    home = game.get("homeTeam", {})
    away = game.get("awayTeam", {})

    game_status = game.get("gameStatus", 1)
    if game_status == 1:
        status = "scheduled"
    elif game_status == 2:
        status = "live"
    else:
        status = "final"

    return {
        "home_team": home.get("teamTricode", ""),
        "away_team": away.get("teamTricode", ""),
        "home_score": home.get("score", 0),
        "away_score": away.get("score", 0),
        "period": game.get("period", 0),
        "clock": format_clock(game.get("gameClock", "")),
        "status": status,
    }


def format_clock(clock: str) -> str:
    """Format game clock for display.

    Input: "PT04M32.00S" (ISO 8601 duration)
    Output: "4:32"
    """
    if not clock or clock == "":
        return ""

    # Handle ISO 8601 duration format
    if clock.startswith("PT"):
        try:
            clock = clock[2:]  # Remove PT prefix
            minutes = 0
            seconds = 0

            if "M" in clock:
                parts = clock.split("M")
                minutes = int(parts[0])
                clock = parts[1] if len(parts) > 1 else ""

            if "S" in clock:
                seconds_str = clock.replace("S", "")
                if "." in seconds_str:
                    seconds_str = seconds_str.split(".")[0]
                seconds = int(seconds_str) if seconds_str else 0

            return f"{minutes}:{seconds:02d}"
        except (ValueError, IndexError):
            return clock

    return clock


def period_to_string(period: int) -> str:
    """Convert period number to display string."""
    if period <= 0:
        return "Pre-game"
    elif period == 1:
        return "Q1"
    elif period == 2:
        return "Q2"
    elif period == 3:
        return "Q3"
    elif period == 4:
        return "Q4"
    else:
        return f"OT{period - 4}"


def find_game_by_teams(home_team: str, away_team: str) -> Optional[dict]:
    """Find a game by team abbreviations.

    Handles abbreviation differences between Kalshi and NBA.com
    """
    # Normalize team codes
    home_norm = TEAM_ABBREV_MAP.get(home_team.upper(), home_team.upper())
    away_norm = TEAM_ABBREV_MAP.get(away_team.upper(), away_team.upper())

    games = get_todays_games()
    for game in games:
        if (game["home_team"].upper() == home_norm and
            game["away_team"].upper() == away_norm):
            return game

    return None
