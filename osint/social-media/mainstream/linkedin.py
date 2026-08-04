"""
LinkedIn checker.
Edit USERNAME / POST below, then run: python linkedin.py

LinkedIn aggressively blocks bot-like traffic with its own "999" status
code, but oddly only on URLs that end in a trailing slash - fetching
"/in/<name>" (no trailing slash) consistently gets through, while
"/in/<name>/" gets blocked, for reasons that are LinkedIn's own bug/quirk,
not a documented API. When it works, LinkedIn server-renders a very rich
schema.org JSON-LD block into the page (for Google's benefit) containing
name, headline, current roles, employers, education, follower count, and
even recent posts with real like/comment counts - all of that is public
information LinkedIn is choosing to show search engines, we're just
reading the same HTML a crawler would.

A 999/blocked response does NOT reliably mean "doesn't exist" - it can
also happen to real, existing profiles that aren't cached the way
high-traffic ones are - so exists=None (not False) on a block.

LinkedIn never exposes post view counts or a public list of who liked a
post (only the aggregate count), so get_views()/get_likers() return None.
"""
import html
import json
import re
import sys
import requests

USERNAME = "target_username"
POST = "https://www.linkedin.com/posts/target_username_target-slug-activity-0000000000000000000-abcd"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _get(url, headers=None, timeout=10):
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    try:
        return requests.get(url, headers=merged, timeout=timeout)
    except requests.RequestException as exc:
        print(f"[!] Request failed for {url}: {exc}", file=sys.stderr)
        return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def _ld_json_graph(text):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return []
    return data.get("@graph", [data])


def get_profile_data(username):
    profile_url = f"https://www.linkedin.com/in/{username}"
    result = {"platform": "LinkedIn", "username": username, "url": profile_url, "exists": None}

    resp = _get(profile_url)
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code == 999:
        result["error"] = "blocked by LinkedIn's anti-bot check - not proof the profile doesn't exist"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    person = None
    for item in _ld_json_graph(resp.text):
        if item.get("@type") == "Person" and item.get("url", "").rstrip("/").endswith(username):
            person = item
            break

    if person is None:
        result["exists"] = False
        return result

    follows = person.get("interactionStatistic", {})
    title_match = re.search(r"<title>(.*?) \| LinkedIn</title>", resp.text)
    headline = None
    if title_match:
        headline = html.unescape(re.sub(r"^.*? - ", "", title_match.group(1), count=1)).strip()

    result.update({
        "exists": True,
        "name": person.get("name"),
        "headline": headline,
        "badge": person.get("disambiguatingDescription"),  # e.g. "Creator, Top Voice" - a LinkedIn tag, not the headline
        "about": person.get("description"),
        "job_titles": person.get("jobTitle"),
        "current_employers": [org.get("name") for org in (person.get("worksFor") or [])],
        "education": [edu.get("name") for edu in (person.get("alumniOf") or [])],
        "location": (person.get("address") or {}).get("addressLocality"),
        "follower_count": follows.get("userInteractionCount") if follows.get("interactionType", "").endswith("FollowAction") else None,
        "photo_url": (person.get("image") or {}).get("contentUrl"),
    })
    return result


def get_post_data(post):
    result = {"platform": "LinkedIn", "post": post, "found": None}
    resp = _get(post)
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code == 999:
        result["error"] = "blocked by LinkedIn's anti-bot check"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    graph = _ld_json_graph(resp.text)
    posting = next((i for i in graph if i.get("@type") in ("SocialMediaPosting", "DiscussionForumPosting", "Article")), None)
    if posting is None:
        result["found"] = False
        return result

    stats = posting.get("interactionStatistic")
    stats = stats if isinstance(stats, list) else ([stats] if stats else [])
    likes = next((s.get("userInteractionCount") for s in stats if "LikeAction" in s.get("interactionType", "")), None)

    result.update({
        "found": True,
        "author": (posting.get("author") or {}).get("name"),
        "text": posting.get("text") or posting.get("headline"),
        "published": posting.get("datePublished"),
        "like_count": likes,
        "comment_count": posting.get("commentCount"),
    })
    return result


def get_views(post):
    # LinkedIn doesn't publicly expose post view counts.
    return None


def get_likers(post):
    # LinkedIn never exposes the identities behind a post's likes - only the aggregate count.
    return None


def get_comments(post, count=10):
    """A preview of top comments (LinkedIn only embeds a subset, not the full thread)."""
    resp = _get(post)
    if resp is None or resp.status_code != 200:
        return None

    graph = _ld_json_graph(resp.text)
    posting = next((i for i in graph if i.get("@type") in ("SocialMediaPosting", "DiscussionForumPosting", "Article")), None)
    if posting is None:
        return None

    comments = []
    for c in (posting.get("comment") or [])[:count]:
        like_stat = c.get("interactionStatistic") or {}
        comments.append({
            "author": (c.get("author") or {}).get("name"),
            "text": c.get("text"),
            "published": c.get("datePublished"),
            "like_count": like_stat.get("userInteractionCount"),
        })
    return comments


def get_recent_posts(username, count=10):
    """Recent posts/articles surfaced on a profile page, with real like/comment counts."""
    resp = _get(f"https://www.linkedin.com/in/{username}")
    if resp is None or resp.status_code != 200:
        return None

    posts = []
    for item in _ld_json_graph(resp.text):
        if item.get("@type") not in ("SocialMediaPosting", "DiscussionForumPosting", "Article"):
            continue
        stats = item.get("interactionStatistic")
        stats = stats if isinstance(stats, list) else ([stats] if stats else [])
        likes = next((s.get("userInteractionCount") for s in stats if "LikeAction" in s.get("interactionType", "")), None)
        posts.append({
            "url": item.get("mainEntityOfPage") or item.get("url"),
            "text": (item.get("text") or item.get("headline") or "")[:200],
            "published": item.get("datePublished"),
            "like_count": likes,
            "comment_count": item.get("commentCount"),
        })
        if len(posts) >= count:
            break
    return posts


if __name__ == "__main__":
    _show("profile", get_profile_data(USERNAME))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("comments", get_comments(POST))
    _show("recent_posts", get_recent_posts(USERNAME))
