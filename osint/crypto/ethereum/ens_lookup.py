"""
ENS (Ethereum Name Service) lookup, both directions.
Edit QUERY below, then run: python ens_lookup.py
(or: python ens_lookup.py <name.eth-or-0xaddress>)

Forward (name -> address) and reverse (address -> name) both go through
ensdata.net's free public API, which was built specifically for this and
accepts either an ENS name or an address in the same endpoint. Blockscout
is queried as a second, independent source to cross-check the reverse
direction (address -> primary ENS name) since it maintains its own
ENS-name index.

APIs occasionally change their response shape - if the expected fields
aren't found, the raw JSON is printed instead of silently failing, so
you can still read the answer.
"""
import json
import re
import sys

import requests

QUERY = "vitalik.eth"

ENSDATA_URL = "https://api.ensdata.net"
BLOCKSCOUT_API = "https://eth.blockscout.com/api/v2"
HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}

ETH_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def ensdata_lookup(query):
    try:
        resp = requests.get(f"{ENSDATA_URL}/{query}", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"error": str(exc)}


def blockscout_reverse(address):
    """Cross-check: address -> primary ENS name, from Blockscout's own index."""
    try:
        resp = requests.get(f"{BLOCKSCOUT_API}/addresses/{address}", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json().get("ens_domain_name")
    except (requests.RequestException, ValueError):
        return None


def resolve(query):
    result = {"query": query}
    result["ensdata"] = ensdata_lookup(query)

    if ETH_ADDR_RE.match(query):
        result["blockscout_ens_name"] = blockscout_reverse(query)
    elif isinstance(result["ensdata"], dict) and result["ensdata"].get("address"):
        result["blockscout_ens_name"] = blockscout_reverse(result["ensdata"]["address"])

    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else QUERY
    _show("resolution", resolve(target))
