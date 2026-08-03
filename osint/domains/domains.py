"""
osint_domain.py - Single-file domain OSINT toolkit.

Covers: DNS records, SPF/DMARC, WHOIS, RDAP, certificate-transparency
subdomain discovery, and Wayback Machine history for a hardcoded domain,
with a numbered action menu and JSON report export.

Optional dependency for full DNS record types (MX, TXT, NS, CNAME, SOA):
    pip install dnspython
Without it, DNS lookups fall back to A records only (via socket).
"""

import ipaddress
import json
import os
import re
import socket
import urllib.request
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
DOMAIN = "example.com"
# ============================================================

SOCKET_TIMEOUT = 8
WHOIS_PORT = 43
IANA_WHOIS = "whois.iana.org"
REPORTS_DIR = "reports"

TLD_WHOIS_FALLBACK = {
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "io": "whois.nic.io",
    "co": "whois.nic.co",
    "dev": "whois.nic.google",
    "app": "whois.nic.google",
}

REFERRAL_PATTERNS = [
    re.compile(r"Registrar WHOIS Server:\s*(\S+)", re.IGNORECASE),
    re.compile(r"refer:\s*(\S+)", re.IGNORECASE),
    re.compile(r"whois:\s*(\S+)", re.IGNORECASE),
]

DNS_RECORD_TYPES = ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]


# ------------------------------------------------------------
# DNS
# ------------------------------------------------------------

def dns_records(domain: str) -> dict:
    """Return {record_type: [values]} for the common record types."""
    records = {}

    if _HAVE_DNSPYTHON:
        resolver = dns.resolver.Resolver()
        resolver.timeout = SOCKET_TIMEOUT
        resolver.lifetime = SOCKET_TIMEOUT
        for rtype in DNS_RECORD_TYPES:
            try:
                answer = resolver.resolve(domain, rtype)
                values = [rec.to_text().strip('"') for rec in answer]
                if values:
                    records[rtype] = values
            except Exception:
                continue
    else:
        try:
            _, _, ips = socket.gethostbyname_ex(domain)
            if ips:
                records["A"] = ips
        except socket.error:
            pass

    return records


def _query_txt_records(name: str) -> list:
    if not _HAVE_DNSPYTHON:
        return []
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = SOCKET_TIMEOUT
        resolver.lifetime = SOCKET_TIMEOUT
        answer = resolver.resolve(name, "TXT")
        return [rec.to_text().strip('"') for rec in answer]
    except Exception:
        return []


def spf_dmarc(domain: str) -> dict:
    result = {"spf": None, "dmarc": None}

    for txt in _query_txt_records(domain):
        if txt.lower().startswith("v=spf1"):
            result["spf"] = txt

    for txt in _query_txt_records(f"_dmarc.{domain}"):
        if txt.lower().startswith("v=dmarc1"):
            result["dmarc"] = txt

    return result


# ------------------------------------------------------------
# WHOIS
# ------------------------------------------------------------

def _is_ip(target: str) -> bool:
    try:
        ipaddress.ip_address(target)
        return True
    except ValueError:
        return False


def _query_whois_server(server: str, query: str, port: int = WHOIS_PORT) -> str:
    with socket.create_connection((server, port), timeout=SOCKET_TIMEOUT) as sock:
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


def whois_lookup(domain: str):
    """Return (raw_text, error). error is None on success."""
    if _is_ip(domain):
        server = IANA_WHOIS
    else:
        tld = domain.rsplit(".", 1)[-1].lower()
        server = TLD_WHOIS_FALLBACK.get(tld, IANA_WHOIS)

    try:
        response = _query_whois_server(server, domain)
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as exc:
        return None, f"Failed querying {server}: {exc}"

    referral = _find_referral(response)
    if referral and referral.lower() != server.lower():
        try:
            follow_up = _query_whois_server(referral, domain)
            response += f"\n\n--- Referral: {referral} ---\n\n" + follow_up
        except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError):
            pass

    return response, None


# ------------------------------------------------------------
# RDAP
# ------------------------------------------------------------

def rdap_lookup(domain: str):
    """Return (raw_rdap_dict, error). error is None on success."""
    if _is_ip(domain):
        url = f"https://rdap.org/ip/{domain}"
    else:
        url = f"https://rdap.org/domain/{domain}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/rdap+json"})
        with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT) as resp:
            return json.loads(resp.read().decode()), None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        return {}, str(exc)


# ------------------------------------------------------------
# Certificate transparency (crt.sh)
# ------------------------------------------------------------

def crtsh_subdomains(domain: str):
    """Return (set_of_names, error) found in CT logs for *.domain."""
    url = f"https://crt.sh/?q=%25.{domain}&output=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "osint-toolkit/1.0"})
        with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT + 12) as resp:
            raw = resp.read().decode(errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        return set(), f"crt.sh query failed: {exc}"

    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return set(), "crt.sh returned an unexpected response (try again shortly)"

    names = set()
    for entry in entries:
        value = entry.get("name_value", "")
        for line in value.split("\n"):
            line = line.strip().lower()
            if line:
                names.add(line)

    return names, None


# ------------------------------------------------------------
# Wayback Machine
# ------------------------------------------------------------

def _wayback_timestamp(domain: str, limit: int):
    url = (
        "https://web.archive.org/cdx/search/cdx"
        f"?url={domain}&output=json&fl=timestamp&collapse=timestamp:8&limit={limit}"
    )
    try:
        with urllib.request.urlopen(url, timeout=SOCKET_TIMEOUT) as resp:
            rows = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None

    if not rows:
        return None
    data_rows = rows[1:] if rows[0] == ["timestamp"] else rows
    if not data_rows:
        return None

    raw_ts = data_rows[-1][0] if limit < 0 else data_rows[0][0]
    try:
        return datetime.strptime(raw_ts, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return raw_ts


def wayback_history(domain: str) -> dict:
    return {
        "first_snapshot": _wayback_timestamp(domain, limit=1),
        "latest_snapshot": _wayback_timestamp(domain, limit=-1),
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
# Actions (things you can look up about DOMAIN)
# ------------------------------------------------------------

def action_dns_records(domain):
    print("\n[1] DNS RECORDS")
    records = dns_records(domain)
    if not records:
        print("  no DNS records found")
        return
    for rtype, values in records.items():
        for value in values:
            print(f"    {rtype:<6} {value}")


def action_spf_dmarc(domain):
    print("\n[2] SPF / DMARC")
    result = spf_dmarc(domain)
    print(f"    SPF:   {result['spf'] or 'not found'}")
    print(f"    DMARC: {result['dmarc'] or 'not found'}")


def action_whois(domain):
    print("\n[3] WHOIS")
    output, err = whois_lookup(domain)
    if err:
        print(f"  {err}")
        return
    print(output[:3000])


def action_rdap(domain):
    print("\n[4] RDAP")
    data, err = rdap_lookup(domain)
    if err:
        print(f"  {err}")
        return
    events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", [])}
    print(f"    registered:   {events.get('registration', 'n/a')}")
    print(f"    expires:      {events.get('expiration', 'n/a')}")
    print(f"    last changed: {events.get('last changed', 'n/a')}")
    print(f"    status:       {', '.join(data.get('status', [])) or 'n/a'}")
    print(f"    nameservers:  {', '.join(ns.get('ldhName', '') for ns in data.get('nameservers', [])) or 'n/a'}")


def action_subdomains(domain):
    print(f"\n[5] SUBDOMAINS - Searching certificate transparency logs for *.{domain}...")
    names, err = crtsh_subdomains(domain)
    if err:
        print(f"  {err}")
        return
    print(f"  {len(names)} unique names found:")
    for name in sorted(names):
        print(f"    {name}")


def action_wayback(domain):
    print("\n[6] WAYBACK MACHINE")
    result = wayback_history(domain)
    print(f"    first archived:  {result['first_snapshot'] or 'never'}")
    print(f"    latest archived: {result['latest_snapshot'] or 'never'}")


def action_save_report(domain):
    print(f"\n[7] SAVE REPORT - Gathering OSINT on {domain}...")
    subdomains, _ = crtsh_subdomains(domain)
    data = {
        "dns_records": dns_records(domain),
        "spf_dmarc": spf_dmarc(domain),
        "subdomains_from_ct_logs": sorted(subdomains),
        "wayback": wayback_history(domain),
    }
    path = save_report("domain", domain, data)
    print(f"  Saved -> {path}")


ACTIONS = {
    "1": ("DNS records", action_dns_records),
    "2": ("SPF / DMARC check", action_spf_dmarc),
    "3": ("Whois", action_whois),
    "4": ("RDAP", action_rdap),
    "5": ("Subdomains (certificate transparency)", action_subdomains),
    "6": ("Wayback Machine history", action_wayback),
    "7": ("Save full report to file", action_save_report),
}


# ------------------------------------------------------------
# Main - runs every action against DOMAIN in order
# ------------------------------------------------------------

def main():
    print("=" * 60)
    print(f" DOMAIN OSINT: {DOMAIN}")
    print("=" * 60)

    for _, (label, func) in ACTIONS.items():
        func(DOMAIN)

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()