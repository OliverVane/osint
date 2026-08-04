"""
Known-service (exchange/mixer/bridge) address tagger.
Edit ADDRESSES below, then run: python exchange_tags.py
(or: python exchange_tags.py <address> [<address> ...])

Auto-detects chain by address format and queries a free, no-key,
live tagging service rather than shipping a static hardcoded address
list (exchange hot/cold wallets rotate constantly, so a baked-in list
would just go stale and quietly mislead an investigation):

- Bitcoin (1.../3.../bc1...) -> WalletExplorer's address-lookup API,
  a long-running free service purpose-built for wallet-cluster tagging.
- Ethereum (0x + 40 hex)     -> Blockscout's public label DB, the same
  "known contract/exchange" tags shown on eth.blockscout.com.

For anything more authoritative (e.g. OFAC-sanctioned addresses), pull
the official Treasury SDN list (https://sanctionslist.ofac.treas.gov,
includes a dedicated digital-currency-address section) and cross-check
against that separately - this script only surfaces exchange/service
labels, not sanctions status.
"""
import json
import re
import sys

import requests

ADDRESSES = [
    "1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF",
]

WALLETEXPLORER_URL = "https://www.walletexplorer.com/api/1/address-lookup"
BLOCKSCOUT_API = "https://eth.blockscout.com/api/v2"
HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}

BTC_RE = re.compile(r"^(1|3|bc1)[a-zA-HJ-NP-Z0-9]{20,60}$")
ETH_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def detect_chain(address):
    if ETH_RE.match(address):
        return "eth"
    if BTC_RE.match(address):
        return "btc"
    return None


def tag_btc(address):
    try:
        resp = requests.get(
            WALLETEXPLORER_URL,
            params={"address": address, "caller": "crypto-osint-toolkit"},
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json()
        label = data.get("label")
        wallet_id = data.get("wallet_id")
        return {"tagged": bool(label or wallet_id), "label": label, "wallet_id": wallet_id}
    except (requests.RequestException, ValueError) as exc:
        return {"error": str(exc)}


def tag_eth(address):
    try:
        resp = requests.get(f"{BLOCKSCOUT_API}/addresses/{address}", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json()
        tags = [t.get("label") for t in data.get("public_tags", []) if t.get("label")]
        name = data.get("name")
        if name:
            tags.append(name)
        return {
            "tagged": bool(tags),
            "labels": tags,
            "is_contract": data.get("is_contract"),
            "ens_domain_name": data.get("ens_domain_name"),
        }
    except (requests.RequestException, ValueError) as exc:
        return {"error": str(exc)}


def tag_address(address):
    chain = detect_chain(address)
    if chain == "btc":
        return {"address": address, "chain": "btc", **tag_btc(address)}
    if chain == "eth":
        return {"address": address, "chain": "eth", **tag_eth(address)}
    return {"address": address, "chain": None, "error": "unrecognized address format"}


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else ADDRESSES
    for addr in targets:
        result = tag_address(addr)
        print(json.dumps(result, indent=2, default=str))
