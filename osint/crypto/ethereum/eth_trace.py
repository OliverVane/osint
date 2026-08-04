"""
Ethereum fund-flow tracer.
Edit START / DIRECTION / MAX_HOPS below, then run: python eth_trace.py
(or: python eth_trace.py <address> [forward|backward] [max_hops])

Walks native-ETH value transfers outward from a starting address, hop by
hop, using Blockscout's free v2 explorer API (eth.blockscout.com) - no
key required. Blockscout also maintains a public label DB for known
contracts/exchanges/bridges, surfaced on every address lookup, so known
service hops get tagged and treated as dead ends instead of traced
further (same convention as btc_trace.py).

This only follows native ETH transfers between EOAs/contracts by value.
Funds routed through a DEX/bridge/mixer contract will show as a
transfer "to" that contract - re-run the trace starting from the
contract's own outgoing side, or check token_transfers on it manually,
to keep following the money past that hop.
"""
import json
import sys
import time

import requests

START = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"  # example (vitalik.eth), edit me
DIRECTION = "forward"  # "forward" (where funds went) or "backward" (where they came from)
MAX_HOPS = 2
BRANCH_LIMIT = 3
REQUEST_DELAY = 0.2

BLOCKSCOUT_API = "https://eth.blockscout.com/api/v2"
HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}


def _get(path, params=None, timeout=10):
    try:
        resp = requests.get(f"{BLOCKSCOUT_API}{path}", params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            print(f"[!] Blockscout HTTP {resp.status_code} for {path}", file=sys.stderr)
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[!] Blockscout request failed: {exc}", file=sys.stderr)
        return None


def get_tags(address):
    data = _get(f"/addresses/{address}")
    if data is None:
        return []
    return [t.get("label") for t in data.get("public_tags", []) if t.get("label")]


def _next_hop_addresses(address, direction):
    """Return [(other_address, value_eth, tx_hash), ...] this address exchanged value with."""
    data = _get(f"/addresses/{address}/transactions", params={"filter": direction})
    if data is None:
        return []

    edges = []
    for tx in data.get("items", []):
        value_wei = tx.get("value", "0")
        try:
            value = int(value_wei) / 1e18
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue

        frm = (tx.get("from") or {}).get("hash")
        to = (tx.get("to") or {}).get("hash")
        if direction == "to" and frm and frm.lower() != address.lower():
            # "to" filter = txs where this address is a recipient; other end is `frm`
            edges.append((frm, value, tx.get("hash")))
        elif direction == "from" and to and to.lower() != address.lower():
            edges.append((to, value, tx.get("hash")))

    edges.sort(key=lambda e: e[1], reverse=True)
    return edges


def trace(start, direction=DIRECTION, max_hops=MAX_HOPS, branch_limit=BRANCH_LIMIT):
    blockscout_filter = "from" if direction == "forward" else "to"
    visited = set()
    graph = {"start": start, "direction": direction, "hops": []}

    frontier = [start]
    for hop in range(1, max_hops + 1):
        hop_record = {"hop": hop, "edges": []}
        next_frontier = []

        for address in frontier:
            key = address.lower()
            if key in visited:
                continue
            visited.add(key)

            tags = get_tags(address)
            time.sleep(REQUEST_DELAY)

            edges = _next_hop_addresses(address, blockscout_filter)[:branch_limit]
            time.sleep(REQUEST_DELAY)

            for other, value_eth, tx_hash in edges:
                edge = {
                    "from": address,
                    "to": other,
                    "value_eth": value_eth,
                    "tx_hash": tx_hash,
                    "from_tags": tags,
                }
                hop_record["edges"].append(edge)
                arrow = "->" if direction == "forward" else "<-"
                tag_note = f" [{', '.join(tags)}]" if tags else ""
                print(f"  hop {hop}: {address}{tag_note} {arrow} {other}  "
                      f"({value_eth:.6f} ETH, tx {tx_hash})")
                if not tags:  # known service = dead end, don't trace past it
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
