"""
Ethereum address lookup.
Edit ADDRESS below, then run: python eth_address.py
(or: python eth_address.py <address>)

Balance/nonce/contract-check come from a public no-key JSON-RPC endpoint
(ethereum.publicnode.com, falling back to blastapi.io - cloudflare-eth.com
was tried first but currently errors on every method, so it was dropped).
Tags, ENS name, and recent tx history come from
Blockscout's free v2 explorer API (eth.blockscout.com) - Blockscout
maintains a public label DB for known contracts/exchanges, which is
exposed on every address lookup, no key required.

Optional: set ETHERSCAN_API_KEY below (free at etherscan.io/apis) to
pull tx history from Etherscan instead, if Blockscout is ever down.
"""
import json
import sys

import requests

ADDRESS = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # example (vitalik.eth), edit me
RECENT_TX_LIMIT = 10
ETHERSCAN_API_KEY = ""  # optional, leave blank to skip

RPC_URLS = [
    "https://ethereum.publicnode.com",
    "https://eth-mainnet.public.blastapi.io",
]
BLOCKSCOUT_API = "https://eth.blockscout.com/api/v2"
HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}


def _rpc(method, params):
    last_error = None
    for url in RPC_URLS:
        try:
            resp = requests.post(
                url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                headers=HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                last_error = f"{url}: {data['error']}"
                continue
            return data.get("result")
        except (requests.RequestException, ValueError) as exc:
            last_error = f"{url} failed: {exc}"
    print(f"[!] RPC error: {last_error}", file=sys.stderr)
    return None


def _blockscout_get(path):
    try:
        resp = requests.get(f"{BLOCKSCOUT_API}{path}", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            print(f"[!] Blockscout HTTP {resp.status_code}", file=sys.stderr)
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[!] Blockscout request failed: {exc}", file=sys.stderr)
        return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def get_balance(address):
    result = _rpc("eth_getBalance", [address, "latest"])
    return int(result, 16) / 1e18 if result else None


def get_tx_count(address):
    result = _rpc("eth_getTransactionCount", [address, "latest"])
    return int(result, 16) if result else None


def is_contract(address):
    code = _rpc("eth_getCode", [address, "latest"])
    if code is None:
        return None
    return code != "0x"


def get_tags_and_ens(address):
    data = _blockscout_get(f"/addresses/{address}")
    if data is None:
        return {}
    tags = [t.get("label") for t in data.get("public_tags", []) if t.get("label")]
    return {
        "name": data.get("name"),
        "ens_domain_name": data.get("ens_domain_name"),
        "is_verified_contract": data.get("is_verified"),
        "public_tags": tags,
    }


def get_recent_txs(address, limit=RECENT_TX_LIMIT):
    data = _blockscout_get(f"/addresses/{address}/transactions")
    if data is None:
        return []
    out = []
    for tx in data.get("items", [])[:limit]:
        out.append({
            "hash": tx.get("hash"),
            "from": (tx.get("from") or {}).get("hash"),
            "to": (tx.get("to") or {}).get("hash"),
            "value_eth": int(tx.get("value", "0")) / 1e18,
            "timestamp": tx.get("timestamp"),
            "method": tx.get("method"),
        })
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ADDRESS
    _show("balance_eth", get_balance(target))
    _show("tx_count", get_tx_count(target))
    _show("is_contract", is_contract(target))
    _show("tags_and_ens", get_tags_and_ens(target))
    _show("recent_txs", get_recent_txs(target))
