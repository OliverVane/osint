"""
Steam checker.
Edit USERNAME / STEAMID64 / POST below, then run: python steam.py

get_profile_data(), get_groups(), and get_friends_list() all accept
EITHER a vanity name (the custom "steamcommunity.com/id/<name>" a user
can set) OR a numeric SteamID64 (routes through
"steamcommunity.com/profiles/<id>" instead) - not every account has a
vanity name, but every account has a SteamID64. Both URL forms return the
identical XML shape, so one code path handles either.

get_profile_data() uses the public ?xml=1 profile view (no Web API key
needed). get_post_data() treats "post" as a Steam Workshop item and uses
the genuinely public ISteamRemoteStorage/GetPublishedFileDetails API,
which also happens to expose a real "views" count. Steam never exposes
the identities of who favorited/upvoted an item, so get_likers() is None.

get_groups() reads the <groups> block already embedded in the main
profile XML. get_friends_list() and get_group_data() use their own public
XML endpoints (steamIDs only - Steam has never included persona names in
either). get_games_list() is a stub: Steam has locked the owned-games view
behind a login wall for every profile tested, with no public path left.
"""
import json
import re
import sys

import requests

USERNAME = "target_username"
STEAMID64 = "00000000000000000"
POST = "https://steamcommunity.com/sharedfiles/filedetails/?id=0000000000"
GROUP = "target_group_slug"


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


def _profile_base(user):
    """
    user can be a vanity name (steamcommunity.com/id/<name>) or a numeric
    SteamID64 (steamcommunity.com/profiles/<id>) - both routes return the
    exact same XML shape, so every function below accepts either.
    """
    user = str(user).strip()
    if user.isdigit():
        return f"https://steamcommunity.com/profiles/{user}/"
    return f"https://steamcommunity.com/id/{user}/"


def get_profile_data(user):
    profile_url = _profile_base(user)
    resp = _get(f"{profile_url}?xml=1")
    result = {"platform": "Steam", "user": user, "url": profile_url, "exists": None}
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    text = resp.text
    if "<error>" in text:
        result["exists"] = False
        return result

    result["exists"] = True
    persona = re.search(r"<steamID><!\[CDATA\[(.*?)\]\]></steamID>", text)
    steamid64 = re.search(r"<steamID64>(\d+)</steamID64>", text)
    state = re.search(r"<onlineState>(\w+)</onlineState>", text)
    result["persona"] = persona.group(1) if persona else None
    result["steamid64"] = steamid64.group(1) if steamid64 else None
    result["online_state"] = state.group(1) if state else None
    return result


def _workshop_id(post):
    match = re.search(r"id=(\d+)", post)
    return match.group(1) if match else post


def get_post_data(post):
    item_id = _workshop_id(post)
    result = {"platform": "Steam", "post": post, "found": None}
    try:
        resp = requests.post(
            "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
            data={"itemcount": 1, "publishedfileids[0]": item_id},
            timeout=10,
        )
    except requests.RequestException as exc:
        result["error"] = f"request error: {exc}"
        return result

    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    details = resp.json().get("response", {}).get("publishedfiledetails", [{}])[0]
    if details.get("result") != 1:
        result["found"] = False
        return result

    result.update({
        "found": True,
        "title": details.get("title"),
        "description": details.get("description"),
        "views": details.get("views"),
        "favorited": details.get("favorited"),
        "subscriptions": details.get("subscriptions"),
        "time_created": details.get("time_created"),
        "creator_steamid64": details.get("creator"),
    })
    return result


def get_views(post):
    data = get_post_data(post)
    return data.get("views") if data.get("found") else None


def get_likers(post):
    # Steam doesn't expose the identities behind an item's favorites/votes, only aggregate counts.
    return None


def get_groups(user):
    """Steam groups this profile belongs to - embedded directly in the main profile XML. Accepts a vanity name or a SteamID64."""
    resp = _get(f"{_profile_base(user)}?xml=1")
    if resp is None or resp.status_code != 200 or "<error>" in resp.text:
        return None

    groups = []
    for block in re.findall(r"<group [^>]*>.*?</group>", resp.text, re.DOTALL):
        group_id = re.search(r"<groupID64>(\d+)</groupID64>", block)
        name = re.search(r"<groupName><!\[CDATA\[(.*?)\]\]></groupName>", block)
        url = re.search(r"<groupURL><!\[CDATA\[(.*?)\]\]></groupURL>", block)
        members = re.search(r"<memberCount>(\d+)</memberCount>", block)
        groups.append({
            "group_id64": group_id.group(1) if group_id else None,
            "name": name.group(1) if name else None,
            "url": url.group(1) if url else None,
            "member_count": int(members.group(1)) if members else None,
        })
    return groups


def get_friends_list(user):
    """
    Public friends list (Steam IDs only - Steam has never included persona
    names in this endpoint's response). Accepts a vanity name or a
    SteamID64. Note: this specific endpoint has a noticeably tighter
    per-IP rate limit than the others in this file; a 429 here doesn't
    necessarily mean anything's wrong.
    """
    resp = _get(f"{_profile_base(user)}friends/?xml=1")
    if resp is None:
        return None
    if resp.status_code == 429:
        return None
    if resp.status_code != 200 or "<error>" in resp.text:
        return None

    return re.findall(r"<steamID64>(\d+)</steamID64>", resp.text.split("</profile>")[-1] if "</profile>" in resp.text else resp.text)


def get_games_list(user):
    # Steam has locked the games-owned view (both /games/?xml=1 and the HTML
    # games tab) behind a login wall for every profile tested, regardless of
    # that profile's privacy settings - there's no public, unauthenticated
    # path left for this. The Web API equivalent (GetOwnedGames) needs a
    # Steam Web API key, which is a developer credential, not a public request.
    return None


def get_group_data(group_url_name):
    """group_url_name is the slug from a steamcommunity.com/groups/<slug> URL."""
    result = {"platform": "Steam", "group": group_url_name, "exists": None}
    resp = _get(f"https://steamcommunity.com/groups/{group_url_name}/memberslistxml/?xml=1")
    if resp is None:
        result["error"] = "request error"
        return result
    if resp.status_code != 200:
        result["error"] = f"HTTP {resp.status_code}"
        return result
    if "<error>" in resp.text or "<groupID64>" not in resp.text:
        result["exists"] = False
        return result

    group_id = re.search(r"<groupID64>(\d+)</groupID64>", resp.text)
    member_count = re.search(r"<memberCount>(\d+)</memberCount>", resp.text)
    result.update({
        "exists": True,
        "group_id64": group_id.group(1) if group_id else None,
        "member_count": int(member_count.group(1)) if member_count else None,
        "member_steamids": re.findall(r"<steamID64>(\d+)</steamID64>", resp.text),
    })
    return result


if __name__ == "__main__":
    _show("profile (by vanity name)", get_profile_data(USERNAME))
    _show("profile (by SteamID64)", get_profile_data(STEAMID64))
    _show("post", get_post_data(POST))
    _show("views", get_views(POST))
    _show("likers", get_likers(POST))
    _show("groups", get_groups(USERNAME))
    _show("friends_list", get_friends_list(USERNAME))
    _show("games_list", get_games_list(USERNAME))
    _show("group_data", get_group_data(GROUP))
