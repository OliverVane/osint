"""
Xbox Live checker.
Edit GAMERTAG / POST below, then run: python xbox.py

Microsoft's own profile pages are now a client-rendered SPA (identical
HTML for real and fake gamertags without executing JavaScript), so
get_profile_data() uses xboxgamertag.com, a third-party aggregator that
mirrors public Xbox Live data and cleanly 404s on unknown gamertags.

Xbox doesn't have "posts" - the closest analog is a played game, so
get_post_data() treats "post" as a game title and looks it up in that
same gamertag's game history on the aggregator page, returning its
gamerscore progress. There's no view count or "likers" concept for a
played game, so those two return None.
"""
import html
import json
import re
import sys

import requests

GAMERTAG = "target_gamertag"
POST = "target_game_title"


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

GAME_CARD_RE = re.compile(
    r'<h3>([^<]+)</h3>\s*'
    r'<p class="text-sm">([^<]*)</p>\s*'
    r'<p class="text-xs">([^<]*)</p>.*?'
    r'Gamerscore</span>\s*</div>\s*<div class="col-9 font-weight-bold">\s*([\d/ ]+)',
    re.DOTALL,
)


def get_profile_data(gamertag):
    profile_url = f"https://www.xboxgamertag.com/search/{gamertag}"
    resp = _get(profile_url)
    result = {"platform": "Xbox Live", "gamertag": gamertag, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result

    if resp.status_code == 200 and "Games Played" in resp.text:
        result["exists"] = True
    elif resp.status_code == 404 or "Gamertag doesn't exist" in resp.text:
        result["exists"] = False
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


def get_post_data(post):
    result = {"platform": "Xbox Live", "gamertag": GAMERTAG, "post": post, "found": None}
    resp = _get(f"https://www.xboxgamertag.com/search/{GAMERTAG}")
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    for title, last_played, platforms, gamerscore in GAME_CARD_RE.findall(resp.text):
        title = html.unescape(title).strip()
        if title.lower() == post.lower():
            result.update({
                "found": True,
                "title": title,
                "last_played": last_played.strip(),
                "platforms": platforms.strip(),
                "gamerscore": gamerscore.strip(),
            })
            return result

    result["found"] = False
    result["error"] = "game not found in this gamertag's recent history"
    return result


def get_views(post):
    # Played games don't have a view count.
    return None


def get_likers(post):
    # Played games don't have a "likers"/reaction concept.
    return None


def get_games_list(gamertag):
    """Every game in this gamertag's visible play history, with gamerscore progress."""
    resp = _get(f"https://www.xboxgamertag.com/search/{gamertag}")
    if resp is None or resp.status_code != 200:
        return None

    games = []
    for title, last_played, platforms, gamerscore in GAME_CARD_RE.findall(resp.text):
        games.append({
            "title": html.unescape(title).strip(),
            "last_played": last_played.strip(),
            "platforms": platforms.strip(),
            "gamerscore": gamerscore.strip(),
        })
    return games


if __name__ == "__main__":
    _show("profile", get_profile_data(GAMERTAG))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("games_list", get_games_list(GAMERTAG))
