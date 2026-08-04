"""
Instagram checker.
Edit USERNAME / POST below, then run: python instagram.py

get_profile_data() uses the same public web_profile_info endpoint
instagram.com's own frontend calls to render a profile - no login needed
for public accounts (though Instagram rate-limits it aggressively).

get_post_data() / get_views() / get_likers(): Instagram no longer serves
any post content (caption, stats, media) to logged-out requests - profile
and post pages are fully client-rendered now and carry no post data in
the raw HTML. These are implemented (they do make the request) but will
reliably return found=None with an explanation rather than real data.
Treat them as a stub until/unless Instagram exposes a public path again.
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
POST = "https://www.instagram.com/p/target_shortcode/"

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
    profile_url = f"https://www.instagram.com/{username}/"
    api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {"x-ig-app-id": "936619743392459", "Accept": "*/*"}  # public app id instagram.com itself uses

    resp = _get(api_url, headers=headers)
    if resp is None:
        return {"platform": "Instagram", "username": username, "url": profile_url, "exists": None, "error": "request error"}

    if resp.status_code == 200:
        try:
            user = resp.json().get("data", {}).get("user")
        except ValueError:
            user = None
        if user:
            return {
                "platform": "Instagram", "username": username, "url": profile_url, "exists": True,
                "full_name": user.get("full_name", ""),
                "followers": user.get("edge_followed_by", {}).get("count"),
                "following": user.get("edge_follow", {}).get("count"),
                "is_private": user.get("is_private"),
                "post_count": user.get("edge_owner_to_timeline_media", {}).get("count"),
            }
        return {"platform": "Instagram", "username": username, "url": profile_url, "exists": False}
    if resp.status_code == 404:
        return {"platform": "Instagram", "username": username, "url": profile_url, "exists": False}
    if resp.status_code == 429:
        return {"platform": "Instagram", "username": username, "url": profile_url, "exists": None, "error": "rate limited (429) - wait and retry"}
    return {"platform": "Instagram", "username": username, "url": profile_url, "exists": None, "error": f"HTTP {resp.status_code}"}


def get_post_data(post):
    resp = _get(post)
    result = {"platform": "Instagram", "post": post, "found": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    text = resp.text
    title = re.search(r'property="og:title" content="([^"]*)"', text)
    desc = re.search(r'property="og:description" content="([^"]*)"', text)
    if title or desc:
        result.update({"found": True, "title": title.group(1) if title else None, "description": desc.group(1) if desc else None})
    else:
        result["error"] = (
            "Instagram doesn't serve post data (caption/stats) to logged-out requests anymore - "
            "this needs an authenticated session to fetch reliably"
        )
    return result


def get_views(post):
    resp = _get(post)
    if resp is None or resp.status_code != 200:
        return None
    match = re.search(r'"video_view_count":(\d+)', resp.text)
    return int(match.group(1)) if match else None


def get_likers(post):
    # Instagram has never exposed a public, unauthenticated list of who liked a post.
    return None


def get_hashtag_data(hashtag):
    # The hashtag web_info API (mirrors the profile endpoint's shape) returns a
    # generic HTML shell instead of JSON for logged-out requests - Instagram
    # has locked this down the same way it has post pages and profile pages
    # under heavy rate limiting. No reliable unauthenticated signal here.
    resp = _get(
        f"https://www.instagram.com/api/v1/tags/web_info/?tag_name={hashtag}",
        headers={"x-ig-app-id": "936619743392459", "Accept": "*/*"},
    )
    result = {"platform": "Instagram", "hashtag": hashtag, "found": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            result["error"] = "Instagram returned an HTML shell instead of JSON (rate-limited or blocked)"
            return result
        story = data.get("data", {}).get("top", {})
        result.update({"found": True, "media_count": story.get("count")})
        return result
    result["error"] = f"HTTP {resp.status_code}"
    return result


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("hashtag", get_hashtag_data("cats"))
