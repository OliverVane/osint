"""
Twitter / X checker.
Edit USERNAME / POST below, then run: python twitter_x.py

get_profile_data() reads the static <title>Name (@handle) / X</title> tag
X still server-renders on real profile pages for link-preview crawlers
(the not-found page has no <title> tag in its raw HTML at all).

get_post_data() uses the old syndication widget endpoint
(cdn.syndication.twimg.com/tweet-result) built for embeds. Its "token"
query param just needs to be non-empty - any value works - which is the
only reason this still functions unauthenticated.

X does not expose per-tweet view counts or a public list of likers
through this endpoint, so get_views()/get_likers() return None.
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
POST = "https://x.com/target_username/status/0000000000000000000"


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
    profile_url = f"https://x.com/{username}"
    resp = _get(profile_url)
    result = {"platform": "Twitter/X", "username": username, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    match = re.search(r"<title>(.*?) \(@(.*?)\) / X</title>", resp.text)
    if match:
        result.update({"exists": True, "display_name": match.group(1)})
    elif "<title>" in resp.text:
        result["error"] = "page loaded but title format changed"
    else:
        result["exists"] = False
    return result


def _tweet_id(post):
    match = re.search(r"status/(\d+)", post)
    return match.group(1) if match else post


def get_post_data(post):
    tweet_id = _tweet_id(post)
    result = {"platform": "Twitter/X", "post": post, "found": None}

    resp = _get(f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=os1nt")
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if not data or "id_str" not in data:
        result["found"] = False
        return result

    result.update({
        "found": True,
        "text": data.get("text"),
        "author": data.get("user", {}).get("screen_name"),
        "created_at": data.get("created_at"),
        "favorite_count": data.get("favorite_count"),
        "conversation_count": data.get("conversation_count"),
    })
    return result


def get_views(post):
    # X doesn't expose per-tweet view counts through any public, unauthenticated endpoint.
    return None


def get_likers(post):
    # X doesn't expose a public list of who liked a tweet - only the aggregate favorite_count.
    return None


def get_user_tweets(username):
    # The old syndication timeline endpoint (cdn.syndication.twimg.com/timeline/profile)
    # that used to back embedded Twitter timelines now returns an empty body - X has
    # shut down every unauthenticated way to list a user's tweets. Fetching a user's
    # timeline now requires a logged-in session and X's internal GraphQL API.
    return None


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("user_tweets", get_user_tweets(USERNAME))
