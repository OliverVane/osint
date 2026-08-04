"""
Twitch checker.
Edit USERNAME / POST below, then run: python twitch.py

Twitch's own pages are a client-rendered SPA, so every function here
queries the public GQL endpoint instead - the same one twitch.tv's own
frontend calls, using its well-known public Client-Id. "post" is a clip
slug or full clip URL. Clips don't have a "likes" concept on Twitch (only
view count), so get_likers() returns None.

Functions: get_profile_data, get_post_data, get_views, get_likers,
get_followers_count, get_stream_status (live/offline + what's playing),
get_videos (recent VODs).
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
POST = "https://clips.twitch.tv/target_clip_slug"


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


def _post_json(url, json_body, headers=None, timeout=10):
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    try:
        return requests.post(url, json=json_body, headers=merged, timeout=timeout)
    except requests.RequestException as exc:
        print(f"[!] Request failed for {url}: {exc}", file=sys.stderr)
        return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


GQL_URL = "https://gql.twitch.tv/gql"
PUBLIC_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # public id used by twitch.tv itself


def _gql(query, variables):
    return _post_json(GQL_URL, {"query": query, "variables": variables}, headers={"Client-Id": PUBLIC_CLIENT_ID})


def get_profile_data(username):
    profile_url = f"https://www.twitch.tv/{username}"
    result = {"platform": "Twitch", "username": username, "url": profile_url, "exists": None}

    resp = _gql(
        "query($login: String!) { user(login: $login) { id displayName createdAt description } }",
        {"login": username},
    )
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    user = resp.json().get("data", {}).get("user")
    if user:
        result.update({
            "exists": True,
            "display_name": user.get("displayName"),
            "created_at": user.get("createdAt"),
            "description": user.get("description"),
        })
    else:
        result["exists"] = False
    return result


def _clip_slug(post):
    match = re.search(r"clips\.twitch\.tv/([\w-]+)", post)
    if match:
        return match.group(1)
    match = re.search(r"/clip/([\w-]+)", post)
    return match.group(1) if match else post


def get_post_data(post):
    slug = _clip_slug(post)
    result = {"platform": "Twitch", "post": post, "found": None}

    resp = _gql(
        """query($slug: ID!) {
            clip(slug: $slug) {
                title viewCount durationSeconds createdAt
                broadcaster { displayName }
                curator { displayName }
            }
        }""",
        {"slug": slug},
    )
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    clip = resp.json().get("data", {}).get("clip")
    if not clip:
        result["found"] = False
        return result

    result.update({
        "found": True,
        "title": clip.get("title"),
        "views": clip.get("viewCount"),
        "duration_seconds": clip.get("durationSeconds"),
        "created_at": clip.get("createdAt"),
        "broadcaster": (clip.get("broadcaster") or {}).get("displayName"),
        "clipped_by": (clip.get("curator") or {}).get("displayName"),
    })
    return result


def get_views(post):
    data = get_post_data(post)
    return data.get("views") if data.get("found") else None


def get_likers(post):
    # Twitch clips have view counts but no "likes"/likers concept.
    return None


def get_followers_count(username):
    resp = _gql("query($login: String!) { user(login: $login) { followers { totalCount } } }", {"login": username})
    if resp is None or resp.status_code != 200:
        return None
    user = resp.json().get("data", {}).get("user")
    return user.get("followers", {}).get("totalCount") if user else None


def get_stream_status(username):
    """Whether the channel is live right now, and what it's streaming."""
    result = {"platform": "Twitch", "username": username, "live": None}
    resp = _gql(
        "query($login: String!) { user(login: $login) { stream { viewersCount title createdAt game { name } } } }",
        {"login": username},
    )
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    user = resp.json().get("data", {}).get("user")
    stream = user.get("stream") if user else None
    if not user:
        result["live"] = None
        result["error"] = "user not found"
    elif stream:
        result.update({
            "live": True,
            "viewers": stream.get("viewersCount"),
            "title": stream.get("title"),
            "game": (stream.get("game") or {}).get("name"),
            "started_at": stream.get("createdAt"),
        })
    else:
        result["live"] = False
    return result


def get_videos(username, count=10):
    """Most recent VODs for a channel."""
    resp = _gql(
        """query($login: String!, $count: Int!) {
            user(login: $login) {
                videos(first: $count) {
                    edges { node { id title viewCount lengthSeconds publishedAt } }
                }
            }
        }""",
        {"login": username, "count": count},
    )
    if resp is None or resp.status_code != 200:
        return None
    user = resp.json().get("data", {}).get("user")
    if not user:
        return None
    edges = user.get("videos", {}).get("edges", [])
    return [
        {
            "id": e["node"].get("id"),
            "title": e["node"].get("title"),
            "views": e["node"].get("viewCount"),
            "length_seconds": e["node"].get("lengthSeconds"),
            "published_at": e["node"].get("publishedAt"),
        }
        for e in edges
    ]


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("followers_count", get_followers_count(USERNAME))
    _show("stream_status", get_stream_status(USERNAME))
    _show("videos", get_videos(USERNAME))
