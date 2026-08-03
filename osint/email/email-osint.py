"""
osint_email.py - Single-file email OSINT toolkit.

Checks: format/domain validity, MX/mail server presence, disposable-email
detection, Gravatar presence, HaveIBeenPwned breach exposure (optional API
key), and account-existence signals on a handful of services that expose a
public "does this email already have an account" check (the same thing their
own login/signup forms use).

Only queries public signals sites already expose to any visitor. Some may
rate-limit automated requests. Use against emails you're authorized to
investigate - your own accounts, or an authorized engagement.

Optional dependency for MX record lookups:
    pip install dnspython
Without it, MX checks fall back to a weaker A-record-only check.
"""

import hashlib
import json
import os
import re
import socket
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

try:
    import dns.resolver
    _HAVE_DNSPYTHON = True
except ImportError:
    _HAVE_DNSPYTHON = False

# ============================================================
# CONFIG - edit these
# ============================================================
EMAIL = "oliver.vane.ov@protonmail.com"
HIBP_API_KEY = ""   # optional - get one at https://haveibeenpwned.com/API/Key
# ============================================================

TIMEOUT = 8
REPORTS_DIR = "reports"
LINE_WIDTH = 64
UA = "Mozilla/5.0 (osint-toolkit)"

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "getnada.com",
    "sharklasers.com", "dispostable.com", "maildrop.cc", "temp-mail.org",
    "fakeinbox.com", "mytemp.email", "mohmal.com", "moakt.com",
    "emailondeck.com", "inboxkitten.com", "spamgourmet.com",
    "guerrillamail.info", "mail-temporaire.fr", "correotemporal.org",
    "tempinbox.com", "mintemail.com", "discard.email", "mailnesia.com",
    "33mail.com", "spam4.me", "tempr.email", "burnermail.io",
}


# ------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------

def header(title: str) -> str:
    bar = "=" * LINE_WIDTH
    return f"\n{bar}\n {title}\n{bar}"


def subheader(title: str) -> str:
    bar = "-" * LINE_WIDTH
    return f"\n{bar}\n {title}\n{bar}"


def kv_line(key: str, value: str, key_width: int = 18) -> str:
    return f"  {key.ljust(key_width, '.')}: {value}"


# ------------------------------------------------------------
# Basic validation
# ------------------------------------------------------------

def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email))


def extract_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def domain_mx_check(domain: str) -> dict:
    result = {"has_mx": False, "mx_records": [], "note": None}

    if _HAVE_DNSPYTHON:
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = TIMEOUT
            resolver.lifetime = TIMEOUT
            answers = resolver.resolve(domain, "MX")
            result["mx_records"] = sorted(str(r.exchange).rstrip(".") for r in answers)
            result["has_mx"] = bool(result["mx_records"])
        except Exception as exc:
            result["note"] = str(exc)
    else:
        result["note"] = "dnspython not installed - run 'pip install dnspython' for real MX lookups"
        try:
            socket.gethostbyname(domain)
            result["note"] += "; domain does resolve (A record), but MX presence is unknown"
        except socket.error:
            pass

    return result


def disposable_check(domain: str) -> bool:
    return domain in DISPOSABLE_DOMAINS


# ------------------------------------------------------------
# Breach exposure (HaveIBeenPwned)
# ------------------------------------------------------------

def hibp_breach_check(email: str, api_key: str) -> dict:
    if not api_key:
        return {"checked": False, "note": "no HIBP API key set - get one at https://haveibeenpwned.com/API/Key"}

    url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{urllib.parse.quote(email, safe='')}?truncateResponse=false"
    req = urllib.request.Request(url, headers={"hibp-api-key": api_key, "User-Agent": UA})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return {"checked": True, "breached": True, "breaches": [b.get("Name") for b in data]}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"checked": True, "breached": False, "breaches": []}
        return {"checked": True, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"checked": False, "error": str(exc)}


# ------------------------------------------------------------
# Gravatar
# ------------------------------------------------------------

def gravatar_lookup(email: str) -> dict:
    email_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()
    avatar_url = f"https://www.gravatar.com/avatar/{email_hash}?d=404"
    profile_url = f"https://www.gravatar.com/{email_hash}.json"
    result = {"hash": email_hash, "avatar_exists": False, "profile": None}

    try:
        req = urllib.request.Request(avatar_url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            result["avatar_exists"] = resp.status == 200
    except urllib.error.HTTPError:
        result["avatar_exists"] = False
    except Exception:
        pass

    if result["avatar_exists"]:
        try:
            req = urllib.request.Request(profile_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
                entry = (data.get("entry") or [{}])[0]
                result["profile"] = {
                    "display_name": entry.get("displayName"),
                    "profile_url": entry.get("profileUrl"),
                    "linked_accounts": [a.get("shortname") for a in entry.get("accounts", [])],
                }
        except Exception:
            pass

    return result


# ------------------------------------------------------------
# GitHub (public commit-author search)
# ------------------------------------------------------------

def github_commit_search(email: str) -> dict:
    url = f"https://api.github.com/search/commits?q=author-email:{urllib.parse.quote(email)}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": UA})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"found": False, "error": str(exc), "usernames": []}

    usernames = set()
    for item in data.get("items", []):
        for role in ("author", "committer"):
            person = item.get(role)
            if person and person.get("login"):
                usernames.add(person["login"])

    return {"found": bool(usernames), "usernames": sorted(usernames), "total_commits": data.get("total_count", 0)}


# ------------------------------------------------------------
# Account-existence checks
# ------------------------------------------------------------

def check_microsoft_account(email: str) -> dict:
    url = "https://login.microsoftonline.com/common/GetCredentialType"
    payload = json.dumps({"Username": email}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": UA})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"exists": None, "error": str(exc)}

    if_exists = data.get("IfExistsResult")
    return {
        "exists": if_exists == 0,
        "raw_code": if_exists,
        "federated": bool(data.get("IsFederatedNS") or data.get("FederationBrandName")),
    }


def check_adobe_account(email: str) -> dict:
    url = f"https://auth.services.adobe.com/signin/v2/users/accounts?email={urllib.parse.quote(email)}&targetClient=adobedotcom"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    body = None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode()
        except Exception:
            return {"exists": None, "error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"exists": None, "error": str(exc)}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"exists": None, "error": "unexpected response"}

    account_type = data.get("accountType")
    return {"exists": bool(account_type), "account_type": account_type}


def check_firefox_account(email: str) -> dict:
    url = f"https://api.accounts.firefox.com/v1/account/status?email={urllib.parse.quote(email)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return {"exists": data.get("exists", False)}
    except Exception as exc:
        return {"exists": None, "error": str(exc)}


# ------------------------------------------------------------
# Report saving
# ------------------------------------------------------------

def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def save_report(kind: str, name: str, data: dict) -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{kind}_{_safe_filename(name)}_{timestamp}.json"
    path = os.path.join(REPORTS_DIR, filename)

    payload = {
        "kind": kind,
        "target": name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    return path


# ------------------------------------------------------------
# Actions
# ------------------------------------------------------------

def action_validate(email):
    print(subheader("FORMAT & DOMAIN"))
    valid = is_valid_email_format(email)
    domain = extract_domain(email)
    print(kv_line("Format valid", str(valid)))
    print(kv_line("Domain", domain))

    if not valid:
        return

    mx = domain_mx_check(domain)
    print(kv_line("Has MX records", str(mx["has_mx"])))
    if mx["mx_records"]:
        print(kv_line("Mail servers", ", ".join(mx["mx_records"])))
    if mx["note"]:
        print(kv_line("Note", mx["note"]))

    print(kv_line("Disposable domain", str(disposable_check(domain))))


def action_hibp(email):
    print(subheader("BREACH EXPOSURE (HaveIBeenPwned)"))
    result = hibp_breach_check(email, HIBP_API_KEY)
    if not result.get("checked"):
        print(f"  {result.get('note') or result.get('error')}")
        return
    if result.get("error"):
        print(f"  error: {result['error']}")
        return
    if result["breached"]:
        print(f"  found in {len(result['breaches'])} breach(es):")
        for name in result["breaches"]:
            print(f"    - {name}")
    else:
        print("  no known breaches")


def action_gravatar(email):
    print(subheader("GRAVATAR"))
    result = gravatar_lookup(email)
    print(kv_line("MD5 hash", result["hash"]))
    print(kv_line("Avatar exists", str(result["avatar_exists"])))
    if result["profile"]:
        p = result["profile"]
        print(kv_line("Display name", str(p["display_name"])))
        print(kv_line("Profile URL", str(p["profile_url"])))
        if p["linked_accounts"]:
            print(kv_line("Linked accounts", ", ".join(p["linked_accounts"])))


def action_github(email):
    print(subheader("GITHUB (commit author search)"))
    result = github_commit_search(email)
    if result.get("error"):
        print(f"  error: {result['error']}")
        return
    if result["found"]:
        print(f"  {result['total_commits']} public commit(s) found, usernames:")
        for u in result["usernames"]:
            print(f"    - {u}")
    else:
        print("  no public commits found for this email")


def action_account_checks(email):
    print(subheader("ACCOUNT EXISTENCE CHECKS"))

    ms = check_microsoft_account(email)
    print(kv_line("Microsoft/Office365", str(ms.get("exists")) if ms.get("error") is None else f"error: {ms['error']}"))

    adobe = check_adobe_account(email)
    print(kv_line("Adobe", str(adobe.get("exists")) if adobe.get("error") is None else f"error: {adobe['error']}"))

    ff = check_firefox_account(email)
    print(kv_line("Firefox Accounts", str(ff.get("exists")) if ff.get("error") is None else f"error: {ff['error']}"))


def action_save_report(email):
    print(subheader("SAVE REPORT"))
    domain = extract_domain(email)
    data = {
        "valid_format": is_valid_email_format(email),
        "domain": domain,
        "mx": domain_mx_check(domain),
        "disposable": disposable_check(domain),
        "hibp": hibp_breach_check(email, HIBP_API_KEY),
        "gravatar": gravatar_lookup(email),
        "github": github_commit_search(email),
        "accounts": {
            "microsoft": check_microsoft_account(email),
            "adobe": check_adobe_account(email),
            "firefox": check_firefox_account(email),
        },
    }
    path = save_report("email", email, data)
    print(f"  Saved -> {path}")


ACTIONS = {
    "1": ("Validate format / domain / MX / disposable", action_validate),
    "2": ("Breach exposure (HaveIBeenPwned)", action_hibp),
    "3": ("Gravatar lookup", action_gravatar),
    "4": ("GitHub commit-author search", action_github),
    "5": ("Account existence checks (MS/Adobe/Firefox)", action_account_checks),
    "6": ("Save full report to file", action_save_report),
}


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    print(header(f"EMAIL OSINT: {EMAIL}"))

    for _, (label, func) in ACTIONS.items():
        func(EMAIL)

    print("\n" + "=" * LINE_WIDTH + "\n")


if __name__ == "__main__":
    main()