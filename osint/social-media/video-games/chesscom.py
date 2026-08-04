"""
Chess.com checker.
Edit USERNAME / POST below, then run: python chesscom.py

get_profile_data() uses Chess.com's public player API - no key needed.
Chess.com doesn't have "posts" in the social-media sense, so "post" here
maps to that same player's public monthly game archive
(https://api.chess.com/pub/player/<username>/games/<YYYY>/<MM>), and
get_post_data() returns the most recent game in it. Chess.com games have
no view count and no "likers" concept, so those two return None.

Functions: get_profile_data, get_post_data, get_views, get_likers,
get_stats (ratings/records per game mode), get_current_games (in-progress
daily games), get_club_data (a chess.com club's public info).
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
POST = "https://api.chess.com/pub/player/target_username/games/2024/05"
CLUB = "target_club_slug"


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url, headers=None, timeout=10, allow_redirects=True):
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    try:
        return requests.get(url, headers=merged, timeout=timeout, allow_redirects=allow_redirects)
    except requests.RequestException as exc:
        print(f"[!] Request failed for {url}: {exc}", file=sys.stderr)
        return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def get_profile_data(username):
    profile_url = f"https://www.chess.com/member/{username}"
    resp = _get(f"https://api.chess.com/pub/player/{username}")
    result = {"platform": "Chess.com", "username": username, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result

    if resp.status_code == 200:
        data = resp.json()
        result.update({
            "exists": True,
            "title": data.get("title"),
            "followers": data.get("followers"),
            "country": data.get("country"),
            "joined": data.get("joined"),
        })
    elif resp.status_code == 404:
        result["exists"] = False
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


def get_post_data(post):
    result = {"platform": "Chess.com", "post": post, "found": None}
    resp = _get(post)
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    games = resp.json().get("games", [])
    if not games:
        result["found"] = False
        result["error"] = "no games in that archive month"
        return result

    game = games[-1]
    result.update({
        "found": True,
        "white": game.get("white", {}).get("username"),
        "black": game.get("black", {}).get("username"),
        "time_control": game.get("time_control"),
        "time_class": game.get("time_class"),
        "end_time": game.get("end_time"),
        "url": game.get("url"),
        "games_in_month": len(games),
    })
    return result


def get_views(post):
    # Chess.com games have no view count.
    return None


def get_likers(post):
    # Chess.com games have no "likers"/reaction concept.
    return None


def get_stats(username):
    """Ratings/records across every game mode (rapid, blitz, bullet, daily, etc.)."""
    resp = _get(f"https://api.chess.com/pub/player/{username}/stats")
    if resp is None or resp.status_code != 200:
        return None
    return resp.json()


def get_current_games(username):
    """Daily/correspondence games currently in progress (empty list if none)."""
    resp = _get(f"https://api.chess.com/pub/player/{username}/games")
    if resp is None or resp.status_code != 200:
        return None
    return resp.json().get("games", [])


def get_club_data(club_url_id):
    """club_url_id is the slug from a chess.com/club/<slug> URL."""
    resp = _get(f"https://api.chess.com/pub/club/{club_url_id}")
    result = {"platform": "Chess.com", "club": club_url_id, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code == 200:
        data = resp.json()
        result.update({
            "exists": True,
            "name": data.get("name"),
            "members_count": data.get("members_count"),
            "average_daily_rating": data.get("average_daily_rating"),
            "created": data.get("created"),
        })
    elif resp.status_code == 404:
        result["exists"] = False
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("stats", get_stats(USERNAME))
    _show("current_games", get_current_games(USERNAME))
    _show("club", get_club_data(CLUB))
