"""
Bitcoin address lookup.
Edit ADDRESS below, then run: python btc_address.py
(or: python btc_address.py <address>)

Uses the Esplora API (blockstream.info), falling back to mempool.space's
identical API if blockstream is unreachable - both are free, no key needed,
and index the full chain including mempool (unconfirmed) transactions.
"""
import json
import sys

import requests

ADDRESS = "1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF"  # example: Mt. Gox trustee cold wallet
RECENT_TX_LIMIT = 10

ESPLORA_HOSTS = [
    "https://blockstream.info/api",
    "https://mempool.space/api",
]

HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}


def _get(path, timeout=10):
    last_error = None
    for host in ESPLORA_HOSTS:
        try:
            resp = requests.get(f"{host}{path}", headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp
            last_error = f"HTTP {resp.status_code} from {host}"
        except requests.RequestException as exc:
            last_error = f"{host} failed: {exc}"
    print(f"[!] {last_error}", file=sys.stderr)
    return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def get_address_info(address):
    resp = _get(f"/address/{address}")
    result = {"address": address}
    if resp is None:
        result["error"] = "request error"
        return result

    try:
        data = resp.json()
    except ValueError:
        result["error"] = "bad JSON response"
        return result

    chain = data.get("chain_stats", {})
    mempool = data.get("mempool_stats", {})
    funded_sats = chain.get("funded_txo_sum", 0)
    spent_sats = chain.get("spent_txo_sum", 0)
    balance_sats = funded_sats - spent_sats

    result.update({
        "tx_count": chain.get("tx_count", 0),
        "total_received_btc": funded_sats / 1e8,
        "total_sent_btc": spent_sats / 1e8,
        "balance_btc": balance_sats / 1e8,
        "unconfirmed_tx_count": mempool.get("tx_count", 0),
    })
    return result


def get_recent_txs(address, limit=RECENT_TX_LIMIT):
    resp = _get(f"/address/{address}/txs")
    if resp is None:
        return []

    try:
        txs = resp.json()
    except ValueError:
        return []

    out = []
    for tx in txs[:limit]:
        status = tx.get("status", {})
        # net effect of this tx on `address`: sum of vout it received minus sum of vin it spent
        received = sum(v.get("value", 0) for v in tx.get("vout", [])
                        if v.get("scriptpubkey_address") == address)
        sent = sum(v.get("prevout", {}).get("value", 0) for v in tx.get("vin", [])
                   if v.get("prevout", {}).get("scriptpubkey_address") == address)
        out.append({
            "txid": tx.get("txid"),
            "confirmed": status.get("confirmed", False),
            "block_time": status.get("block_time"),
            "net_effect_btc": (received - sent) / 1e8,
        })
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else ADDRESS
    _show("address_info", get_address_info(target))
    _show("recent_txs", get_recent_txs(target))
