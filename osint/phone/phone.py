import json
import os
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

import phonenumbers
from phonenumbers import carrier as pn_carrier
from phonenumbers import geocoder as pn_geocoder
from phonenumbers import timezone as pn_timezone

# ============================================================
# CONFIG - edit these
# ============================================================
PHONE_NUMBER = ""    # include country code with + if possible
DEFAULT_REGION = "US"            # used only if PHONE_NUMBER has no leading +country code
NUMVERIFY_API_KEY = ""           # optional - https://numverify.com (free tier: 100 req/month)
# ============================================================

TIMEOUT = 8
REPORTS_DIR = "reports"
LINE_WIDTH = 64
UA = "Mozilla/5.0 (osint-toolkit)"

NUMBER_TYPE_NAMES = {
    phonenumbers.PhoneNumberType.FIXED_LINE: "Fixed line",
    phonenumbers.PhoneNumberType.MOBILE: "Mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed line or mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "Toll free",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "Premium rate",
    phonenumbers.PhoneNumberType.SHARED_COST: "Shared cost",
    phonenumbers.PhoneNumberType.VOIP: "VOIP",
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "Personal number",
    phonenumbers.PhoneNumberType.PAGER: "Pager",
    phonenumbers.PhoneNumberType.UAN: "UAN",
    phonenumbers.PhoneNumberType.VOICEMAIL: "Voicemail",
    phonenumbers.PhoneNumberType.UNKNOWN: "Unknown",
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


def kv_line(key: str, value, key_width: int = 20) -> str:
    if value is None or value == "":
        value = "n/a"
    return f"  {key.ljust(key_width, '.')}: {value}"


# ------------------------------------------------------------
# Core parsing (offline, via Google's libphonenumber data)
# ------------------------------------------------------------

def parse_number(raw_number: str, default_region: str):
    try:
        return phonenumbers.parse(raw_number, default_region), None
    except phonenumbers.NumberParseException as exc:
        return None, str(exc)


def analyze_number(raw_number: str, default_region: str) -> dict:
    number, err = parse_number(raw_number, default_region)
    if err:
        return {"error": err}

    return {
        "valid": phonenumbers.is_valid_number(number),
        "possible": phonenumbers.is_possible_number(number),
        "country_code": number.country_code,
        "national_number": number.national_number,
        "region_code": phonenumbers.region_code_for_number(number),
        "number_type": NUMBER_TYPE_NAMES.get(phonenumbers.number_type(number), "Unknown"),
        "formats": {
            "e164": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
            "international": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
            "national": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.NATIONAL),
            "rfc3966": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.RFC3966),
        },
        "carrier": pn_carrier.name_for_number(number, "en") or None,
        "location": pn_geocoder.description_for_number(number, "en") or None,
        "timezones": list(pn_timezone.time_zones_for_number(number)),
    }


# ------------------------------------------------------------
# Optional online cross-check (NumVerify)
# ------------------------------------------------------------

def numverify_lookup(raw_number: str, api_key: str) -> dict:
    if not api_key:
        return {"checked": False, "note": "no NumVerify API key set - https://numverify.com"}

    url = f"http://apilayer.net/api/validate?access_key={api_key}&number={urllib.parse.quote(raw_number)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"checked": False, "error": str(exc)}

    if "error" in data:
        return {"checked": True, "error": data["error"]}

    return {
        "checked": True,
        "valid": data.get("valid"),
        "line_type": data.get("line_type"),
        "carrier": data.get("carrier"),
        "location": data.get("location"),
        "country_name": data.get("country_name"),
    }


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

def action_analyze(number):
    print(subheader("PARSED NUMBER"))
    result = analyze_number(number, DEFAULT_REGION)
    if "error" in result:
        print(f"  {result['error']}")
        return

    print(kv_line("Valid", result["valid"]))
    print(kv_line("Possible", result["possible"]))
    print(kv_line("Country code", f"+{result['country_code']}"))
    print(kv_line("Region", result["region_code"]))
    print(kv_line("Number type", result["number_type"]))
    print(kv_line("Carrier (offline)", result["carrier"]))
    print(kv_line("Location", result["location"]))
    print(kv_line("Timezone(s)", ", ".join(result["timezones"]) if result["timezones"] else None))

    print(subheader("FORMATS"))
    for fmt_name, value in result["formats"].items():
        print(kv_line(fmt_name.upper(), value))


def action_online_check(number):
    print(subheader("ONLINE CROSS-CHECK (NumVerify)"))
    result = numverify_lookup(number, NUMVERIFY_API_KEY)
    if not result.get("checked"):
        print(f"  {result.get('note') or result.get('error')}")
        return
    if "error" in result:
        print(f"  {result['error']}")
        return
    print(kv_line("Valid", result["valid"]))
    print(kv_line("Line type", result["line_type"]))
    print(kv_line("Carrier", result["carrier"]))
    print(kv_line("Location", result["location"]))
    print(kv_line("Country", result["country_name"]))


def action_save_report(number):
    print(subheader("SAVE REPORT"))
    data = {
        "offline_analysis": analyze_number(number, DEFAULT_REGION),
        "online_check": numverify_lookup(number, NUMVERIFY_API_KEY),
    }
    path = save_report("phone", number, data)
    print(f"  Saved -> {path}")


ACTIONS = {
    "1": ("Parse, validate & classify", action_analyze),
    "2": ("Online carrier/line-type cross-check", action_online_check),
    "3": ("Save full report to file", action_save_report),
}


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    print(header(f"PHONE OSINT: {PHONE_NUMBER}"))

    for _, (label, func) in ACTIONS.items():
        func(PHONE_NUMBER)

    print("\n" + "=" * LINE_WIDTH + "\n")


if __name__ == "__main__":
    main()