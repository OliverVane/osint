"""
ip_intel.py - Single-file IP address OSINT/intel toolkit.

Covers: basic classification (private/reserved/etc), reverse DNS,
geolocation + ISP/ASN info (ip-api.com), ASN cross-check (Team Cymru),
WHOIS, RDAP + abuse contact, reverse IP / shared hosting (HackerTarget),
DNSBL blocklist membership, and Tor exit node check.

All lookups are passive - public APIs and DNS queries only. No port
scanning or direct probing of the target host.

Optional dependency (only used for the Team Cymru ASN TXT lookup;
everything else works without it):
    pip install dnspython
"""

import ipaddress
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
# CONFIG - edit this
# ============================================================
TARGET_IP = "8.8.8.8"
# ============================================================

TIMEOUT = 8
REPORTS_DIR = "reports"
LINE_WIDTH = 64
UA = "Mozilla/5.0 (osint-toolkit)"
WHOIS_PORT = 43
IANA_WHOIS = "whois.iana.org"

REFERRAL_PATTERNS = [
    re.compile(r"Registrar WHOIS Server:\s*(\S+)", re.IGNORECASE),
    re.compile(r"refer:\s*(\S+)", re.IGNORECASE),
    re.compile(r"whois:\s*(\S+)", re.IGNORECASE),
]

DNSBL_ZONES = [
    "zen.spamhaus.org",
    "b.barracudacentral.org",
    "dnsbl.sorbs.net",
]

TOR_EXIT_LIST_URL = "https://check.torproject.org/torbulkexitlist"


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
    return f"  {key.ljust(key_width, '.')}: {value}"


# ------------------------------------------------------------
# Basic classification
# ------------------------------------------------------------

def ip_basic_info(ip: str) -> dict:
    addr = ipaddress.ip_address(ip)
    return {
        "version": addr.version,
        "compressed": addr.compressed,
        "is_private": addr.is_private,
        "is_global": addr.is_global,
        "is_loopback": addr.is_loopback,
        "is_multicast": addr.is_multicast,
        "is_link_local": addr.is_link_local,
        "is_reserved": addr.is_reserved,
    }


def reverse_dns_lookup(ip: str):
    try:
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname
    except (socket.herror, socket.gaierror):
        return None


# ------------------------------------------------------------
# Geolocation + ISP (ip-api.com, free, no key)
# ------------------------------------------------------------

def geolocation_lookup(ip: str) -> dict:
    fields = (
        "status,message,continent,country,countryCode,region,regionName,"
        "city,zip,lat,lon,timezone,isp,org,as,asname,mobile,proxy,hosting,query"
    )
    url = f"http://ip-api.com/json/{urllib.parse.quote(ip)}?fields={fields}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        return {"error": str(exc)}

    if data.get("status") != "success":
        return {"error": data.get("message", "lookup failed")}

    return data


# ------------------------------------------------------------
# ASN cross-check (Team Cymru DNS lookup, IPv4 only)
# ------------------------------------------------------------

def asn_lookup_cymru(ip: str) -> dict:
    if not _HAVE_DNSPYTHON:
        return {"error": "dnspython not installed - pip install dnspython"}

    addr = ipaddress.ip_address(ip)
    if addr.version != 4:
        return {"error": "Cymru lookup here is IPv4-only; skipped for IPv6"}

    reversed_ip = ".".join(reversed(ip.split(".")))
    query = f"{reversed_ip}.origin.asn.cymru.com"

    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = TIMEOUT
        resolver.lifetime = TIMEOUT
        answer = resolver.resolve(query, "TXT")
        txt = answer[0].to_text().strip('"')
        parts = [p.strip() for p in txt.split("|")]
        keys = ["asn", "bgp_prefix", "country", "registry", "allocated"]
        return dict(zip(keys, parts))
    except Exception as exc:
        return {"error": str(exc)}


# ------------------------------------------------------------
# WHOIS
# ------------------------------------------------------------

def _query_whois_server(server: str, query: str, port: int = WHOIS_PORT) -> str:
    with socket.create_connection((server, port), timeout=TIMEOUT) as sock:
        sock.sendall((query + "\r\n").encode())
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode(errors="replace")


def _find_referral(response: str):
    for pattern in REFERRAL_PATTERNS:
        match = pattern.search(response)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate.lower() != "whois":
                return candidate
    return None


def whois_lookup(ip: str):
    try:
        response = _query_whois_server(IANA_WHOIS, ip)
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as exc:
        return None, f"Failed querying {IANA_WHOIS}: {exc}"

    referral = _find_referral(response)
    if referral and referral.lower() != IANA_WHOIS.lower():
        try:
            follow_up = _query_whois_server(referral, ip)
            response += f"\n\n--- Referral: {referral} ---\n\n" + follow_up
        except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError):
            pass

    return response, None


# ------------------------------------------------------------
# RDAP + abuse contact
# ------------------------------------------------------------

def rdap_lookup(ip: str):
    url = f"https://rdap.org/ip/{ip}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/rdap+json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode()), None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {}, str(exc)


def _entity_contact(entity: dict) -> dict:
    email, name = None, None
    vcard = entity.get("vcardArray")
    if vcard and len(vcard) > 1:
        for field in vcard[1]:
            if field[0] == "email":
                email = field[3]
            if field[0] == "fn":
                name = field[3]
    return {"name": name, "email": email, "handle": entity.get("handle")}


def extract_abuse_contact(rdap_data: dict):
    for entity in rdap_data.get("entities", []):
        if "abuse" in entity.get("roles", []):
            return _entity_contact(entity)
        for sub in entity.get("entities", []):
            if "abuse" in sub.get("roles", []):
                return _entity_contact(sub)
    return None


# ------------------------------------------------------------
# Reverse IP / shared hosting (HackerTarget, free tier)
# ------------------------------------------------------------

def reverse_ip_lookup(ip: str):
    url = f"https://api.hackertarget.com/reverseiplookup/?q={urllib.parse.quote(ip)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode(errors="replace").strip()
    except Exception as exc:
        return [], str(exc)

    lowered = text.lower()
    if not text or "no dns" in lowered or "no records" in lowered or "api count exceeded" in lowered or "error" in lowered:
        return [], text or "no records found"

    return [line.strip() for line in text.splitlines() if line.strip()], None


# ------------------------------------------------------------
# DNSBL / blocklist check
# ------------------------------------------------------------

def dnsbl_check(ip: str) -> dict:
    addr = ipaddress.ip_address(ip)
    if addr.version != 4:
        return {"note": "DNSBL checks here support IPv4 only"}

    reversed_ip = ".".join(reversed(ip.split(".")))
    results = {}
    for zone in DNSBL_ZONES:
        query = f"{reversed_ip}.{zone}"
        try:
            socket.gethostbyname(query)
            results[zone] = "LISTED"
        except socket.gaierror:
            results[zone] = "clean"
        except socket.error as exc:
            results[zone] = f"error: {exc}"
    return results


# ------------------------------------------------------------
# Tor exit node check
# ------------------------------------------------------------

def tor_exit_check(ip: str) -> dict:
    try:
        req = urllib.request.Request(TOR_EXIT_LIST_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            text = resp.read().decode(errors="replace")
    except Exception as exc:
        return {"checked": False, "error": str(exc)}

    exit_ips = {line.strip() for line in text.splitlines() if line.strip()}
    return {"checked": True, "is_tor_exit": ip in exit_ips}


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

def action_basic_info(ip):
    print(subheader("BASIC CLASSIFICATION"))
    info = ip_basic_info(ip)
    print(kv_line("IP version", f"IPv{info['version']}"))
    print(kv_line("Compressed form", info["compressed"]))
    print(kv_line("Private", info["is_private"]))
    print(kv_line("Globally routable", info["is_global"]))
    print(kv_line("Loopback", info["is_loopback"]))
    print(kv_line("Multicast", info["is_multicast"]))
    print(kv_line("Link-local", info["is_link_local"]))
    print(kv_line("Reserved", info["is_reserved"]))

    print(subheader("REVERSE DNS"))
    hostname = reverse_dns_lookup(ip)
    print(kv_line("PTR record", hostname or "none found"))


def action_geolocation(ip):
    print(subheader("GEOLOCATION & ISP (ip-api.com)"))
    data = geolocation_lookup(ip)
    if "error" in data:
        print(f"  {data['error']}")
        return
    print(kv_line("Country", f"{data.get('country')} ({data.get('countryCode')})"))
    print(kv_line("Region", data.get("regionName")))
    print(kv_line("City", data.get("city")))
    print(kv_line("Zip", data.get("zip")))
    print(kv_line("Lat/Lon", f"{data.get('lat')}, {data.get('lon')}"))
    print(kv_line("Timezone", data.get("timezone")))
    print(kv_line("ISP", data.get("isp")))
    print(kv_line("Org", data.get("org")))
    print(kv_line("ASN", data.get("as")))
    print(kv_line("Mobile carrier", data.get("mobile")))
    print(kv_line("Known proxy/VPN", data.get("proxy")))
    print(kv_line("Hosting/datacenter", data.get("hosting")))


def action_asn(ip):
    print(subheader("ASN CROSS-CHECK (Team Cymru)"))
    result = asn_lookup_cymru(ip)
    if "error" in result:
        print(f"  {result['error']}")
        return
    print(kv_line("ASN", result.get("asn")))
    print(kv_line("BGP Prefix", result.get("bgp_prefix")))
    print(kv_line("Country", result.get("country")))
    print(kv_line("Registry", result.get("registry")))
    print(kv_line("Allocated", result.get("allocated")))


def action_whois(ip):
    print(subheader("WHOIS"))
    output, err = whois_lookup(ip)
    if err:
        print(f"  {err}")
        return
    print(output[:3000])


def action_rdap(ip):
    print(subheader("RDAP + ABUSE CONTACT"))
    data, err = rdap_lookup(ip)
    if err:
        print(f"  {err}")
        return
    print(kv_line("Handle", data.get("handle")))
    print(kv_line("Name", data.get("name")))
    print(kv_line("Status", ", ".join(data.get("status", [])) or "n/a"))
    print(kv_line("Country", data.get("country")))

    abuse = extract_abuse_contact(data)
    if abuse:
        print(kv_line("Abuse contact", abuse.get("email") or "n/a"))
        print(kv_line("Abuse org", abuse.get("name") or "n/a"))
    else:
        print(kv_line("Abuse contact", "not found"))


def action_reverse_ip(ip):
    print(subheader("REVERSE IP / SHARED HOSTING (HackerTarget)"))
    hostnames, err = reverse_ip_lookup(ip)
    if err:
        print(f"  {err}")
        return
    print(f"  {len(hostnames)} hostname(s) found on this IP:")
    for h in hostnames:
        print(f"    {h}")


def action_dnsbl(ip):
    print(subheader("DNSBL / BLOCKLIST CHECK"))
    results = dnsbl_check(ip)
    if "note" in results:
        print(f"  {results['note']}")
        return
    for zone, status in results.items():
        print(kv_line(zone, status))


def action_tor(ip):
    print(subheader("TOR EXIT NODE CHECK"))
    result = tor_exit_check(ip)
    if not result.get("checked"):
        print(f"  {result.get('error')}")
        return
    print(kv_line("Is Tor exit node", result["is_tor_exit"]))


def action_save_report(ip):
    print(subheader("SAVE REPORT"))
    whois_out, whois_err = whois_lookup(ip)
    rdap_data, rdap_err = rdap_lookup(ip)
    hostnames, reverse_err = reverse_ip_lookup(ip)

    data = {
        "basic_info": ip_basic_info(ip),
        "reverse_dns": reverse_dns_lookup(ip),
        "geolocation": geolocation_lookup(ip),
        "asn_cymru": asn_lookup_cymru(ip),
        "whois": whois_out if not whois_err else {"error": whois_err},
        "rdap": rdap_data if not rdap_err else {"error": rdap_err},
        "abuse_contact": extract_abuse_contact(rdap_data) if not rdap_err else None,
        "reverse_ip_hosts": hostnames if not reverse_err else {"error": reverse_err},
        "dnsbl": dnsbl_check(ip),
        "tor_exit": tor_exit_check(ip),
    }
    path = save_report("ip", ip, data)
    print(f"  Saved -> {path}")


ACTIONS = {
    "1": ("Basic classification + reverse DNS", action_basic_info),
    "2": ("Geolocation & ISP", action_geolocation),
    "3": ("ASN cross-check", action_asn),
    "4": ("Whois", action_whois),
    "5": ("RDAP + abuse contact", action_rdap),
    "6": ("Reverse IP / shared hosting", action_reverse_ip),
    "7": ("DNSBL blocklist check", action_dnsbl),
    "8": ("Tor exit node check", action_tor),
    "9": ("Save full report to file", action_save_report),
}


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    try:
        ipaddress.ip_address(TARGET_IP)
    except ValueError:
        print(f"'{TARGET_IP}' is not a valid IP address.")
        return

    print(header(f"IP INTEL: {TARGET_IP}"))

    for _, (label, func) in ACTIONS.items():
        func(TARGET_IP)

    print("\n" + "=" * LINE_WIDTH + "\n")


if __name__ == "__main__":
    main()