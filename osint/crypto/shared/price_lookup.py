"""
Coin price / market data lookup, including historical price on a given
date - useful for valuing traced funds at the time they moved rather
than at today's price.
Edit COIN_ID / HISTORICAL_DATE below, then run: python price_lookup.py
(or: python price_lookup.py <coin-id> [yyyy-mm-dd])

Uses CoinGecko's free public API, no key needed. `coin-id` is
CoinGecko's slug, not the ticker - e.g. "bitcoin" not "BTC",
"ethereum" not "ETH". Run list_coin_ids() once if you need to look one
up (it's a ~15k-row list, so it's not printed by default).

CoinGecko now gates their old exact-date `/coins/{id}/history` endpoint
behind an API key, so historical_price() instead pulls a narrow window
from `/coins/{id}/market_chart/range` (still free/no-key) and picks the
sample closest to the requested date - daily granularity, same result
for this toolkit's purposes. That endpoint's free tier is also capped
to the past 365 days - anything older comes back as an error with
error_code 10012, printed as-is so it's clear it's a plan limit rather
than a bug. For older dates you'd need a paid CoinGecko key.
"""
import datetime
import json
import sys

import requests

COIN_ID = "bitcoin"
VS_CURRENCIES = ["usd", "eur"]
HISTORICAL_DATE = "2026-07-04"  # yyyy-mm-dd, must be within the last 365 days on the free tier

API_BASE = "https://api.coingecko.com/api/v3"
HEADERS = {"User-Agent": "crypto-osint-toolkit/1.0"}


def _get(path, params=None):
    try:
        resp = requests.get(f"{API_BASE}{path}", params=params, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            try:
                detail = resp.json().get("error", {}).get("status", {}).get("error_message")
            except ValueError:
                detail = None
            print(f"[!] CoinGecko HTTP {resp.status_code}{': ' + detail if detail else ''}", file=sys.stderr)
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[!] CoinGecko request failed: {exc}", file=sys.stderr)
        return None


def _show(label, value):
    if isinstance(value, (dict, list)):
        print(f"{label}:\n{json.dumps(value, indent=2, default=str)}")
    else:
        print(f"{label}: {value}")


def current_price(coin_id, vs_currencies=VS_CURRENCIES):
    data = _get("/simple/price", params={
        "ids": coin_id,
        "vs_currencies": ",".join(vs_currencies),
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "include_24hr_change": "true",
    })
    if data is None:
        return None
    return data.get(coin_id)


def historical_price(coin_id, date_str, vs_currency="usd"):
    """date_str must be yyyy-mm-dd."""
    target = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    from_ts = int((target - datetime.timedelta(days=2)).timestamp())
    to_ts = int((target + datetime.timedelta(days=2)).timestamp())

    data = _get(f"/coins/{coin_id}/market_chart/range", params={
        "vs_currency": vs_currency,
        "from": from_ts,
        "to": to_ts,
    })
    if data is None or not data.get("prices"):
        return None

    target_ms = target.timestamp() * 1000
    closest = min(data["prices"], key=lambda p: abs(p[0] - target_ms))
    return {"date": date_str, "vs_currency": vs_currency, "price": closest[1]}


def list_coin_ids():
    """Full id/symbol/name list, ~15k entries - use to find the right coin-id."""
    return _get("/coins/list") or []


if __name__ == "__main__":
    args = sys.argv[1:]
    coin_id = args[0] if len(args) > 0 else COIN_ID
    date_str = args[1] if len(args) > 1 else HISTORICAL_DATE

    _show("current_price", current_price(coin_id))
    _show(f"historical_price ({date_str})", historical_price(coin_id, date_str))
