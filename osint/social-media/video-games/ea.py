"""
EA / Origin checker.
Edit USERNAME / POST below, then run: python ea.py

STATUS: EA fully shut down the Origin service, including the old
api1.origin.com lookup API that older OSINT scripts used for this (it now
404s with "Origin has shut down"). There is currently no public,
unauthenticated way to check whether an EA account/persona exists, or to
fetch data about a specific piece of EA content - the EA app, EA Help,
and EA's community forums all require a signed-in session or sit behind a
bot-detection challenge. Every function below still runs and says so
plainly instead of faking a result - update EA_LOOKUP_URL if EA ever
ships a public alternative.
"""
import json
import sys

import requests

USERNAME = "target_username"
POST = "target_post"


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


EA_LOOKUP_URL = "https://api1.origin.com/xsearch/users?userId={username}&searchType=userName"


def get_profile_data(username):
    profile_url = "https://www.ea.com/"
    result = {"platform": "EA/Origin", "username": username, "url": profile_url, "exists": None}

    resp = _get(EA_LOOKUP_URL.format(username=username), headers={"Accept": "application/json"})
    if resp is None:
        result["error"] = "request error"
        return result

    if resp.status_code == 404 and "shut down" in resp.text.lower():
        result["error"] = "Origin API has been shut down - no public username-check method currently exists"
        return result
    if resp.status_code in (401, 403):
        result["error"] = f"HTTP {resp.status_code} - now requires auth"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    info_list = resp.json().get("infoList") or []
    if info_list:
        result.update({"exists": True, "persona": info_list[0].get("userId", username)})
    else:
        result["exists"] = False
    return result


def get_post_data(post):
    return {
        "platform": "EA/Origin", "post": post, "found": None,
        "error": "EA has no public, unauthenticated API for content lookups since Origin's shutdown",
    }


def get_views(post):
    return None


def get_likers(post):
    return None


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
