"""Kalshi API client for fetching NBA odds."""
import requests
import time
from typing import Optional
from datetime import datetime
import config

# Rate limiting - generous to avoid 429s, speed doesn't matter for scans
_last_request_time = 0
_min_request_interval = 1.0  # 1 second between requests


def _rate_limit():
    """Ensure we don't exceed rate limits."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _min_request_interval:
        time.sleep(_min_request_interval - elapsed)
    _last_request_time = time.time()


def probability_to_american(prob: float) -> int:
    """Convert Kalshi probability (0-1) to American odds.

    Examples:
        0.75 -> -300
        0.60 -> -150
        0.40 -> +150
    """
    if prob >= 0.5:
        return int(-100 * prob / (1 - prob))
    else:
        return int(100 * (1 - prob) / prob)


def american_to_probability(odds: int) -> float:
    """Convert American odds to probability.

    Examples:
        -300 -> 0.75
        -150 -> 0.60
        +150 -> 0.40
    """
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    else:
        return 100 / (odds + 100)


def get_nba_events() -> list[dict]:
    """Fetch all NBA game events from Kalshi.

    Returns list of events:
    {
        "event_ticker": "KXNBAGAME-26FEB24BOSPHX",
        "title": "Boston at Phoenix",
        "sub_title": "BOS at PHX (Feb 24)",
        "away_team": "BOS",
        "home_team": "PHX",
        "game_date": "2026-02-24"
    }
    """
    url = f"{config.KALSHI_API_BASE}/events"
    cursor = None
    events = []

    while True:
        _rate_limit()
        params = {
            "series_ticker": config.KALSHI_NBA_SERIES,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"Error fetching Kalshi events: {e}")
            break

        for event in data.get("events", []):
            ticker = event.get("event_ticker", "")
            parsed = parse_event_ticker(ticker)
            if not parsed:
                continue

            events.append({
                "event_ticker": ticker,
                "title": event.get("title", ""),
                "sub_title": event.get("sub_title", ""),
                "away_team": parsed["away_team"],
                "home_team": parsed["home_team"],
                "game_date": parsed["game_date"],
            })

        cursor = data.get("cursor")
        if not cursor:
            break

    return events


def parse_event_ticker(ticker: str) -> Optional[dict]:
    """Parse KXNBAGAME event ticker to extract teams and date.

    Example: KXNBAGAME-26FEB24BOSPHX
    Format: KXNBAGAME-{YY}{MON}{DD}{AWAY}{HOME}
    """
    prefix = f"{config.KALSHI_NBA_SERIES}-"
    if not ticker.startswith(prefix):
        return None

    suffix = ticker[len(prefix):]
    if len(suffix) < 11:  # YY(2) + MON(3) + DD(2) + AWAY(3) + HOME(3) = 13, but sometimes teams are 2-3 chars
        return None

    try:
        year = suffix[0:2]
        month = suffix[2:5]
        day = suffix[5:7]
        teams = suffix[7:]

        # Teams are 3 chars each
        away_team = teams[0:3]
        home_team = teams[3:6]

        # Convert month abbreviation to number
        months = {
            "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
            "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
            "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12"
        }
        month_num = months.get(month.upper(), "01")

        game_date = f"20{year}-{month_num}-{day}"

        return {
            "home_team": home_team,
            "away_team": away_team,
            "game_date": game_date,
        }
    except (IndexError, ValueError):
        return None


def get_event_markets(event_ticker: str) -> list[dict]:
    """Fetch markets for a specific NBA game event.

    Returns list of markets (typically 2, one per team):
    {
        "ticker": "KXNBAGAME-26FEB24BOSPHX-BOS",
        "team": "BOS",
        "yes_bid": 71,  # cents (probability * 100)
        "yes_ask": 74,
        "last_price": 72,
        "probability": 0.72  # midpoint
    }
    """
    _rate_limit()
    url = f"{config.KALSHI_API_BASE}/markets"
    params = {"event_ticker": event_ticker}

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"Error fetching markets for {event_ticker}: {e}")
        return []

    markets = []
    for market in data.get("markets", []):
        ticker = market.get("ticker", "")
        # Extract team from ticker (last 3 chars after the dash)
        team = ticker.split("-")[-1] if "-" in ticker else ""

        yes_bid = market.get("yes_bid", 0) or 0
        yes_ask = market.get("yes_ask", 0) or 0

        # Calculate probability from midpoint of bid/ask (in cents)
        if yes_bid and yes_ask:
            probability = (yes_bid + yes_ask) / 200  # Convert cents to 0-1
        else:
            probability = (market.get("last_price", 50) or 50) / 100

        markets.append({
            "ticker": ticker,
            "team": team,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "last_price": market.get("last_price", 0) or 0,
            "probability": probability,
        })

    return markets


def get_game_odds(event_ticker: str) -> Optional[dict]:
    """Get current odds for an NBA game.

    Returns:
    {
        "event_ticker": "KXNBAGAME-26FEB24BOSPHX",
        "favorite": "BOS",
        "underdog": "PHX",
        "favorite_prob": 0.72,
        "underdog_prob": 0.28,
        "favorite_american": -257,
        "underdog_american": +257,
        "is_finished": False
    }
    """
    markets = get_event_markets(event_ticker)
    if len(markets) != 2:
        return None

    # Sort by probability to find favorite
    markets.sort(key=lambda m: m["probability"], reverse=True)
    favorite = markets[0]
    underdog = markets[1]

    # Check if game is finished (one side has > 99% probability)
    is_finished = favorite["probability"] > 0.99

    return {
        "event_ticker": event_ticker,
        "favorite": favorite["team"],
        "underdog": underdog["team"],
        "favorite_prob": favorite["probability"],
        "underdog_prob": underdog["probability"],
        "favorite_american": probability_to_american(favorite["probability"]),
        "underdog_american": probability_to_american(underdog["probability"]),
        "is_finished": is_finished,
    }


def get_team_odds(event_ticker: str, team: str) -> Optional[dict]:
    """Get current odds for a specific team in a game.

    Unlike get_game_odds() which re-determines the favorite each call,
    this returns odds for the requested team regardless of who currently leads.

    Returns:
    {
        "team": "DEN",
        "probability": 0.38,
        "american": +163,
        "opponent": "UTA",
        "opponent_probability": 0.62,
        "opponent_american": -163,
    }
    """
    markets = get_event_markets(event_ticker)
    if len(markets) != 2:
        return None

    team_market = None
    opponent_market = None
    for m in markets:
        if m["team"] == team:
            team_market = m
        else:
            opponent_market = m

    if not team_market or not opponent_market:
        return None

    return {
        "team": team,
        "probability": team_market["probability"],
        "american": probability_to_american(team_market["probability"]),
        "opponent": opponent_market["team"],
        "opponent_probability": opponent_market["probability"],
        "opponent_american": probability_to_american(opponent_market["probability"]),
    }


def get_todays_heavy_favorites(date: Optional[str] = None) -> list[dict]:
    """Get NBA games where the favorite is >= PREGAME_THRESHOLD.

    Args:
        date: Optional date string (YYYY-MM-DD). Defaults to today.

    Returns list of games with heavy favorites.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    events = get_nba_events()
    heavy_favorites = []

    for event in events:
        if event["game_date"] != date:
            continue

        odds = get_game_odds(event["event_ticker"])
        if not odds:
            continue

        if odds["favorite_prob"] >= config.PREGAME_THRESHOLD:
            heavy_favorites.append({
                **event,
                **odds,
            })

    return heavy_favorites


if __name__ == "__main__":
    # Test the API
    print("Fetching NBA events...")
    events = get_nba_events()
    print(f"Found {len(events)} events")

    for event in events[:5]:
        print(f"\n{event['event_ticker']}: {event['title']}")
        odds = get_game_odds(event["event_ticker"])
        if odds:
            print(f"  Favorite: {odds['favorite']} ({odds['favorite_american']})")
            print(f"  Underdog: {odds['underdog']} ({odds['underdog_american']})")
