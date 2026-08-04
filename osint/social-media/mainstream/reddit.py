"""
Reddit checker.
Edit USERNAME / POST / SUBREDDIT / QUERY below, then run: python reddit.py

Reddit's ".json" suffix is public and needs no auth - it works on user
profiles, submissions, subreddits, comment listings and search. This is
the same mechanism every third-party Reddit client has used for years.

NOTE: this sandbox's outbound IP is currently blocked/interstitial'd by
Reddit on both www.reddit.com (403) and old.reddit.com (serves a generic
"Welcome to Reddit" HTML page instead of JSON, even with a plain
requests.get and no special headers) - almost certainly a network-level
anti-bot flag on this environment rather than anything wrong with these
URLs. The endpoints below are implemented against Reddit's long-documented,
stable JSON API shape; verify from a normal residential connection.

Reddit doesn't publicly expose post view counts or the individual
identities of upvoters, so get_views()/get_likers() return None - only
the aggregate score is available via get_post_data().
"""
import json
import sys

import requests

USERNAME = "target_username"
POST = "https://www.reddit.com/r/test/comments/0000000/target_post/"
SUBREDDIT = "target_subreddit"
QUERY = "target search query"


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
    profile_url = f"https://www.reddit.com/user/{username}/"
    resp = _get(f"https://www.reddit.com/user/{username}/about.json")
    result = {"platform": "Reddit", "username": username, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result

    if resp.status_code == 200:
        try:
            user = resp.json().get("data", {})
        except ValueError:
            user = {}
        result.update({
            "exists": True,
            "total_karma": user.get("total_karma"),
            "created_utc": user.get("created_utc"),
            "is_mod": user.get("is_mod"),
        })
    elif resp.status_code == 404:
        result["exists"] = False
    elif resp.status_code == 403:
        result["error"] = "403 - suspended/private account, or this network's IP is being rate-limited by Reddit"
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


def get_post_data(post):
    json_url = post.rstrip("/") + "/.json"
    resp = _get(json_url)
    result = {"platform": "Reddit", "post": post, "found": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}" + (" - possibly rate-limited" if resp.status_code == 403 else "")
        return result

    try:
        listing = resp.json()[0]["data"]["children"][0]["data"]
    except (ValueError, KeyError, IndexError):
        result["error"] = "unexpected response body"
        return result

    result.update({
        "found": True,
        "title": listing.get("title"),
        "author": listing.get("author"),
        "score": listing.get("score"),
        "upvote_ratio": listing.get("upvote_ratio"),
        "num_comments": listing.get("num_comments"),
        "created_utc": listing.get("created_utc"),
        "subreddit": listing.get("subreddit"),
    })
    return result


def get_views(post):
    # Reddit doesn't publicly expose post view counts.
    return None


def get_likers(post):
    # Reddit never exposes the identities behind a post's upvotes - only the aggregate score.
    return None


def get_post_comments(post, limit=10):
    """Top-level comments on a submission."""
    json_url = post.rstrip("/") + "/.json"
    resp = _get(f"{json_url}?limit={limit}")
    if resp is None or resp.status_code != 200:
        return None
    try:
        children = resp.json()[1]["data"]["children"]
    except (ValueError, KeyError, IndexError):
        return None
    comments = []
    for child in children:
        data = child.get("data", {})
        if child.get("kind") != "t1":
            continue
        comments.append({
            "author": data.get("author"),
            "body": data.get("body"),
            "score": data.get("score"),
            "created_utc": data.get("created_utc"),
        })
    return comments


def get_subreddit_data(subreddit):
    url = f"https://www.reddit.com/r/{subreddit}/"
    resp = _get(f"https://www.reddit.com/r/{subreddit}/about.json")
    result = {"platform": "Reddit", "subreddit": subreddit, "url": url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    data = resp.json().get("data", {})
    if not data:
        result["exists"] = False
        return result

    result.update({
        "exists": True,
        "title": data.get("title"),
        "public_description": data.get("public_description"),
        "subscribers": data.get("subscribers"),
        "created_utc": data.get("created_utc"),
        "over18": data.get("over18"),
    })
    return result


def get_subreddit_posts(subreddit, sort="new", limit=10):
    """sort: 'new', 'hot', 'top', or 'rising'."""
    resp = _get(f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}")
    if resp is None or resp.status_code != 200:
        return None
    children = resp.json().get("data", {}).get("children", [])
    return [
        {
            "title": c["data"].get("title"),
            "author": c["data"].get("author"),
            "score": c["data"].get("score"),
            "num_comments": c["data"].get("num_comments"),
            "created_utc": c["data"].get("created_utc"),
            "permalink": "https://www.reddit.com" + c["data"].get("permalink", ""),
        }
        for c in children
    ]


def get_user_posts(username, limit=10):
    resp = _get(f"https://www.reddit.com/user/{username}/submitted.json?limit={limit}")
    if resp is None or resp.status_code != 200:
        return None
    children = resp.json().get("data", {}).get("children", [])
    return [
        {
            "title": c["data"].get("title"),
            "subreddit": c["data"].get("subreddit"),
            "score": c["data"].get("score"),
            "num_comments": c["data"].get("num_comments"),
            "created_utc": c["data"].get("created_utc"),
            "permalink": "https://www.reddit.com" + c["data"].get("permalink", ""),
        }
        for c in children
    ]


def get_user_comments(username, limit=10):
    resp = _get(f"https://www.reddit.com/user/{username}/comments.json?limit={limit}")
    if resp is None or resp.status_code != 200:
        return None
    children = resp.json().get("data", {}).get("children", [])
    return [
        {
            "body": c["data"].get("body"),
            "subreddit": c["data"].get("subreddit"),
            "score": c["data"].get("score"),
            "created_utc": c["data"].get("created_utc"),
            "permalink": "https://www.reddit.com" + c["data"].get("permalink", ""),
        }
        for c in children
    ]


def search_reddit(query, limit=10):
    resp = _get(f"https://www.reddit.com/search.json?q={query}&limit={limit}")
    if resp is None or resp.status_code != 200:
        return None
    children = resp.json().get("data", {}).get("children", [])
    return [
        {
            "title": c["data"].get("title"),
            "subreddit": c["data"].get("subreddit"),
            "author": c["data"].get("author"),
            "score": c["data"].get("score"),
            "permalink": "https://www.reddit.com" + c["data"].get("permalink", ""),
        }
        for c in children
    ]


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("post_comments", get_post_comments(POST))
    _show("subreddit", get_subreddit_data(SUBREDDIT))
    _show("subreddit_posts", get_subreddit_posts(SUBREDDIT))
    _show("user_posts", get_user_posts(USERNAME))
    _show("user_comments", get_user_comments(USERNAME))
    _show("search", search_reddit(QUERY))
