"""
Tumblr checker.
Edit USERNAME / POST below, then run: python tumblr.py

get_profile_data() uses the subdomain 200/404 trick (every blog gets its
own subdomain, unused ones 404 cleanly).

get_post_data()/get_blog_posts()/get_tagged_posts() all use Tumblr's old
v1 "api/read/json" endpoint, which (unlike the v2 REST API) still works
without an API key for public blogs. Tumblr's public data only ever
exposes a combined "notes" count (likes + reblogs together) - there's no
separate view count and no public list of who liked a post, so
get_views()/get_likers() return None.
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
POST = "https://target_username.tumblr.com/post/0000000000000000000/target-slug"


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
    profile_url = f"https://{username}.tumblr.com/"
    resp = _get(profile_url)
    result = {"platform": "Tumblr", "username": username, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
    elif resp.status_code == 200:
        result["exists"] = True
    elif resp.status_code == 404:
        result["exists"] = False
    else:
        result["error"] = f"HTTP {resp.status_code}"
    return result


def _blog_and_id(post):
    match = re.search(r"https?://([\w-]+)\.tumblr\.com/post/(\d+)", post)
    return (match.group(1), match.group(2)) if match else (None, None)


def get_post_data(post):
    result = {"platform": "Tumblr", "post": post, "found": None}
    blog, post_id = _blog_and_id(post)
    if not blog:
        result["error"] = "couldn't parse blog name / post id from URL"
        return result

    resp = _get(f"https://{blog}.tumblr.com/api/read/json?id={post_id}")
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    data = _parse_v1_response(resp.text)
    try:
        post_data = data["posts"][0]
    except (TypeError, KeyError, IndexError):
        result["found"] = False
        result["error"] = "post not found or blog is private"
        return result

    result.update({
        "found": True,
        "type": post_data.get("type"),
        "date": post_data.get("date"),
        "note_count": post_data.get("note-count"),
        "tags": post_data.get("tags"),
        "url": post_data.get("url-with-slug"),
    })
    return result


def get_views(post):
    # Tumblr doesn't expose view counts - only a combined "notes" (likes + reblogs) count,
    # available via get_post_data(post)["note_count"].
    return None


def get_likers(post):
    # Tumblr's public API only returns the aggregate note_count, never the identities behind it.
    return None


def _parse_v1_response(text):
    prefix = "var tumblr_api_read = "
    if text.startswith(prefix):
        text = text[len(prefix):]
    try:
        data, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return None
    return data


def get_blog_posts(username, count=10):
    """Most recent public posts on a blog."""
    resp = _get(f"https://{username}.tumblr.com/api/read/json?num={count}")
    if resp is None or resp.status_code != 200:
        return None
    data = _parse_v1_response(resp.text)
    if data is None:
        return None
    return [
        {
            "id": p.get("id"),
            "type": p.get("type"),
            "date": p.get("date"),
            "note_count": p.get("note-count"),
            "tags": p.get("tags"),
            "url": p.get("url-with-slug"),
        }
        for p in data.get("posts", [])
    ]


def get_tagged_posts(username, tag, scan=50):
    """
    Posts on a blog that carry a given tag. The v1 API's own ?tag= filter
    is silently ignored these days (it just returns unfiltered posts), so
    this fetches the `scan` most recent posts and filters by tag in
    Python - it only searches recent posts, not the blog's full history.
    """
    resp = _get(f"https://{username}.tumblr.com/api/read/json?num={scan}")
    if resp is None or resp.status_code != 200:
        return None
    data = _parse_v1_response(resp.text)
    if data is None:
        return None
    return [
        {
            "id": p.get("id"),
            "type": p.get("type"),
            "date": p.get("date"),
            "note_count": p.get("note-count"),
            "url": p.get("url-with-slug"),
        }
        for p in data.get("posts", [])
        if tag.lower() in [t.lower() for t in (p.get("tags") or [])]
    ]


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("blog_posts", get_blog_posts(USERNAME))
    _show("tagged_posts", get_tagged_posts(USERNAME, "announcement"))
