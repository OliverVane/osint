"""
Bitcoin fund-flow tracer.
Edit START / DIRECTION / MAX_HOPS below, then run: python btc_trace.py
(or: python btc_trace.py <address-or-txid> [forward|backward] [max_hops])

Walks the public UTXO graph outward from a starting address (or the
outputs of a starting transaction), hop by hop, using the free Esplora
API (blockstream.info / mempool.space). This is the same "follow the
money" technique blockchain-analytics tools are built on - it only reads
already-public chain data, nothing more.

Two heuristics are included:
- forward trace: for each address, look at where its received coins were
  later spent to (its outgoing tx outputs) - "where did the money go".
- backward trace: look at where its coins came from (its incoming tx
  inputs) - "where did the money come from".
- common-input-ownership clustering: every input address of a single
  transaction is, by convention, spent by the same wallet/owner (you
  need the private keys for all of them to sign it). Grouping input
  addresses this way is a standard first-pass deanonymization heuristic.

Branching is capped (BRANCH_LIMIT) so a popular address (e.g. an
exchange hot wallet) doesn't explode into thousands of API calls.
Known-service addresses are tagged live via WalletExplorer's free
lookup API so exchange/mixer hops are flagged instead of traced further.
"""
import json
import sys
import time

import requests

START = "1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF"  # example: Mt. Gox trustee cold wallet (receive-only)
DIRECTION = "backward"  # "forward" (where funds went) or "backward" (where they came from)
MAX_HOPS = 2
BRANCH_LIMIT = 3  # max addresses to follow per hop, ranked by value
REQUEST_DELAY = 0.2  # seconds between API calls, be polite to free services

ESPLORA_HOSTS = [
    "https://blockstream.info/api",
    "https://mempool.space/api",
]
WALLETEXPLORER_URL = "https://www.walletexplorer.com/api/1/address-lookup"
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


def wallet_tag(address):
    """Live lookup against WalletExplorer's free known-wallet-cluster tagger."""
    try:
        resp = requests.get(
            WALLETEXPLORER_URL,
            params={"address": address, "caller": "crypto-osint-toolkit"},
            headers=HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        label = data.get("label")
        wallet_id = data.get("wallet_id")
        return {"label": label, "wallet_id": wallet_id} if (label or wallet_id) else None
    except (requests.RequestException, ValueError):
        return None


def get_txs(address):
    resp = _get(f"/address/{address}/txs")
    if resp is None:
        return []
    try:
        return resp.json()
    except ValueError:
        return []


def get_tx(txid):
    resp = _get(f"/tx/{txid}")
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def common_input_cluster(txid):
    """Every input address on this tx is presumed same-owner (co-spend heuristic)."""
    tx = get_tx(txid)
    if tx is None:
        return []
    addrs = set()
    for vin in tx.get("vin", []):
        addr = vin.get("prevout", {}).get("scriptpubkey_address")
        if addr:
            addrs.add(addr)
    return sorted(addrs)


def _next_hop_addresses(address, direction):
    """Return [(other_address, value_sats, txid), ...] this address moved value with."""
    edges = []
    for tx in get_txs(address):
        txid = tx.get("txid")
        if direction == "forward":
            spent_here = any(
                vin.get("prevout", {}).get("scriptpubkey_address") == address
                for vin in tx.get("vin", [])
            )
            if not spent_here:
                continue
            for vout in tx.get("vout", []):
                addr = vout.get("scriptpubkey_address")
                if addr and addr != address:
                    edges.append((addr, vout.get("value", 0), txid))
        else:  # backward
            received_here = any(
                vout.get("scriptpubkey_address") == address for vout in tx.get("vout", [])
            )
            if not received_here:
                continue
            for vin in tx.get("vin", []):
                prevout = vin.get("prevout", {})
                addr = prevout.get("scriptpubkey_address")
                if addr and addr != address:
                    edges.append((addr, prevout.get("value", 0), vin.get("txid")))
    edges.sort(key=lambda e: e[1], reverse=True)
    return edges


def trace(start, direction=DIRECTION, max_hops=MAX_HOPS, branch_limit=BRANCH_LIMIT):
    visited = set()
    graph = {"start": start, "direction": direction, "hops": []}

    frontier = [start]
    for hop in range(1, max_hops + 1):
        hop_record = {"hop": hop, "edges": []}
        next_frontier = []

        for address in frontier:
            if address in visited:
                continue
            visited.add(address)

            tag = wallet_tag(address)
            time.sleep(REQUEST_DELAY)

            edges = _next_hop_addresses(address, direction)[:branch_limit]
            time.sleep(REQUEST_DELAY)

            tag_display = (tag["label"] or f"wallet {tag['wallet_id']}") if tag else None
            for other, value_sats, txid in edges:
                edge = {
                    "from": address,
                    "to": other,
                    "value_btc": value_sats / 1e8,
                    "txid": txid,
                    "from_tag": tag,
                }
                hop_record["edges"].append(edge)
                arrow = "->" if direction == "forward" else "<-"
                tag_note = f" [{tag_display}]" if tag else ""
                print(f"  hop {hop}: {address}{tag_note} {arrow} {other}  "
                      f"({value_sats / 1e8:.8f} BTC, tx {txid})")
                if not tag:  # don't keep tracing past a known exchange/service, dead end
                    next_frontier.append(other)

        graph["hops"].append(hop_record)
        frontier = next_frontier
        if not frontier:
            break

    return graph


if __name__ == "__main__":
    args = sys.argv[1:]
    start = args[0] if len(args) > 0 else START
    direction = args[1] if len(args) > 1 else DIRECTION
    max_hops = int(args[2]) if len(args) > 2 else MAX_HOPS

    print(f"[*] Tracing {direction} from {start}, up to {max_hops} hop(s)\n")
    result = trace(start, direction, max_hops)

    print("\n[*] Full trace graph (JSON):")
    print(json.dumps(result, indent=2, default=str))
