"""
YouTube checker.
Edit HANDLE / POST / QUERY below, then run: python youtube.py

The channel page and watch page embed their real stats as JSON inside the
server-rendered HTML, so plain requests are enough - no API key needed
for get_profile_data()/get_post_data(). get_channel_videos() and
search_youtube() read the same ytInitialData blob from the videos-tab and
search-results pages. get_comments() replicates the two-step call
youtube.com's own web client makes to its internal ("innertube") API:
one call to find the comment section's continuation token, a second to
fetch the actual comments - using the public web-client API key that
ships in every YouTube page's JS (not a secret, just an app identifier).

YouTube never exposes a public list of who liked a video, so
get_likers() returns None.
"""
import json
import re
import sys

import requests

HANDLE = "target_handle"
POST = "https://www.youtube.com/watch?v=target_video_id"
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


INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # public key baked into every youtube.com page
INNERTUBE_CONTEXT = {"client": {"clientName": "WEB", "clientVersion": "2.20240101.00.00"}}


def get_profile_data(handle):
    profile_url = f"https://www.youtube.com/@{handle}"
    resp = _get(profile_url)
    result = {"platform": "YouTube", "handle": handle, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code == 404:
        result["exists"] = False
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    text = resp.text
    if '"channelId"' not in text and '"externalId"' not in text:
        result["exists"] = False
        result["error"] = "no channel markers in page"
        return result

    result["exists"] = True
    subs = re.search(r'"subscriberCountText".*?"simpleText":"([^"]+)"', text)
    result["subscribers"] = subs.group(1) if subs else None
    return result


def _video_id(post):
    match = re.search(r"v=([\w-]{11})", post)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([\w-]{11})", post)
    return match.group(1) if match else post


def get_post_data(post):
    video_id = _video_id(post)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    resp = _get(watch_url)
    result = {"platform": "YouTube", "post": post, "found": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    text = resp.text
    title = re.search(r'"title":"([^"]+)","', text)
    views = re.search(r'"viewCount":"(\d+)"', text)
    likes = re.search(r'"likeCountIfIndifferentNumber":"(\d+)"', text)
    channel = re.search(r'"author":"([^"]+)"', text)

    if not title and not views:
        result["found"] = False
        return result

    result.update({
        "found": True,
        "title": title.group(1) if title else None,
        "channel": channel.group(1) if channel else None,
        "views": int(views.group(1)) if views else None,
        "likes": int(likes.group(1)) if likes else None,
    })
    return result


def get_views(post):
    data = get_post_data(post)
    return data.get("views") if data.get("found") else None


def get_likers(post):
    # YouTube has never exposed a public, unauthenticated list of who liked a video.
    return None


def _extract_json_blob(text, marker):
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("{", idx)
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return data


def _walk_collect(obj, key, out):
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            _walk_collect(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_collect(v, key, out)


def get_channel_videos(handle, count=10):
    """Recent videos on a channel's /videos tab (abbreviated views/upload-time text, like the site shows)."""
    resp = _get(f"https://www.youtube.com/@{handle}/videos")
    if resp is None or resp.status_code != 200:
        return None

    data = _extract_json_blob(resp.text, "var ytInitialData = ")
    if data is None:
        return None

    rich_items = []
    _walk_collect(data, "richItemRenderer", rich_items)

    videos = []
    for item in rich_items[:count]:
        lockup = item.get("content", {}).get("lockupViewModel")
        if not lockup:
            continue
        video_id = lockup.get("contentId")
        meta_vm = lockup.get("metadata", {}).get("lockupMetadataViewModel", {})
        title = meta_vm.get("title", {}).get("content")
        rows = meta_vm.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        parts = rows[0].get("metadataParts", []) if rows else []
        views_text = parts[0].get("accessibilityLabel") if len(parts) > 0 else None
        published_text = parts[1].get("accessibilityLabel") if len(parts) > 1 else None
        videos.append({
            "video_id": video_id,
            "title": title,
            "views_text": views_text,
            "published_text": published_text,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        })
    return videos


def get_comments(post, count=20):
    """Top-level comments on a video, newest continuation page (~20 per call)."""
    video_id = _video_id(post)

    resp1 = requests.post(
        f"https://www.youtube.com/youtubei/v1/next?key={INNERTUBE_KEY}",
        json={"context": INNERTUBE_CONTEXT, "videoId": video_id},
        headers=DEFAULT_HEADERS, timeout=10,
    )
    if resp1.status_code != 200:
        return None

    token = None

    def find_token(obj):
        nonlocal token
        if isinstance(obj, dict):
            if obj.get("sectionIdentifier") == "comment-item-section":
                candidates = []
                _walk_collect(obj, "continuationEndpoint", candidates)
                for c in candidates:
                    t = c.get("continuationCommand", {}).get("token")
                    if t:
                        token = t
            for v in obj.values():
                find_token(v)
        elif isinstance(obj, list):
            for v in obj:
                find_token(v)

    find_token(resp1.json())
    if token is None:
        return None

    resp2 = requests.post(
        f"https://www.youtube.com/youtubei/v1/next?key={INNERTUBE_KEY}",
        json={"context": INNERTUBE_CONTEXT, "continuation": token},
        headers=DEFAULT_HEADERS, timeout=10,
    )
    if resp2.status_code != 200:
        return None

    payloads = []
    _walk_collect(resp2.json(), "commentEntityPayload", payloads)

    comments = []
    for p in payloads[:count]:
        props = p.get("properties", {})
        author = p.get("author", {})
        toolbar = p.get("toolbar", {})
        comments.append({
            "author": author.get("displayName"),
            "text": props.get("content", {}).get("content"),
            "published_time": props.get("publishedTime"),
            "like_count": toolbar.get("likeCountLiked") or toolbar.get("likeCountNotliked"),
            "reply_count": toolbar.get("replyCount"),
        })
    return comments


def search_youtube(query, count=10):
    """Video search results, same as youtube.com/results?search_query=..."""
    resp = _get(f"https://www.youtube.com/results?search_query={query}")
    if resp is None or resp.status_code != 200:
        return None

    data = _extract_json_blob(resp.text, "var ytInitialData = ")
    if data is None:
        return None

    renderers = []
    _walk_collect(data, "videoRenderer", renderers)

    results = []
    for vr in renderers[:count]:
        results.append({
            "video_id": vr.get("videoId"),
            "title": (vr.get("title", {}).get("runs") or [{}])[0].get("text"),
            "channel": (vr.get("ownerText", {}).get("runs") or [{}])[0].get("text"),
            "views_text": vr.get("viewCountText", {}).get("simpleText"),
            "published_text": vr.get("publishedTimeText", {}).get("simpleText"),
            "length_text": vr.get("lengthText", {}).get("simpleText"),
            "url": f"https://www.youtube.com/watch?v={vr.get('videoId')}" if vr.get("videoId") else None,
        })
    return results


if __name__ == "__main__":
    _show("profile", get_profile_data(HANDLE))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("channel_videos", get_channel_videos(HANDLE))
    _show("comments", get_comments(POST))
    _show("search", search_youtube(QUERY))
