"""
Snapchat checker.
Edit USERNAME below, then run: python snapchat.py

get_profile_data() uses the public "add" page, which cleanly 200s for a
real username and 404s for an unknown one.

Snapchat has no public, unauthenticated way to fetch data about a
specific Snap/Story/Spotlight post - that all requires the app's private
API and a logged-in session. get_post_data()/get_views()/get_likers()
are kept for interface consistency with the other scripts, but they
always report found=None with that explanation rather than fake data.
"""
import json
import sys

import requests

USERNAME = "target_username"
POST = "https://www.snapchat.com/add/target_username"  # not used for real post lookups - see note above


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
    profile_url = f"https://www.snapchat.com/add/{username}"
    resp = _get(profile_url)
    result = {"platform": "Snapchat", "username": username, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
    elif resp.status_code == 200:
        result["exists"] = True
    elif resp.status_code == 404:
        result["exists"] = False
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


def get_post_data(post):
    return {
        "platform": "Snapchat", "post": post, "found": None,
        "error": "Snapchat has no public API for individual posts - Stories/Spotlight require a logged-in session",
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
