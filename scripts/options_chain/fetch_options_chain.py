"""
Pulls live options chain data from the Tradier API.

For each ticker this module:
  1. Fetches available expiration dates and filters to the configured DTE window.
  2. Pulls the full options chain (with greeks) for every qualifying expiration.
  3. Fetches the underlying spot quote.
  4. Persists raw results as a timestamped JSON file under data/raw/.

Can be run standalone:
    python -m options_chain.fetch_options_chain

Rate limit: Tradier allows ~200 req/min on sandbox and production accounts.
The 150 ms sleep between per-expiration calls keeps throughput well within that.
"""

import json
import os
import time
import logging
from datetime import datetime, date

import requests

from .config import (
    TRADIER_API_KEY, TRADIER_BASE_URL,
    DEFAULT_TICKERS, MIN_DTE, MAX_DTE, RAW_DIR,
)

logger = logging.getLogger(__name__)

_HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json",
}


# ──────────────────────────────────────────────
# Low-level API helpers
# ──────────────────────────────────────────────

def _get(endpoint: str, params: dict) -> dict:
    """GET request against Tradier with shared auth headers."""
    url = f"{TRADIER_BASE_URL}/{endpoint.lstrip('/')}"
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_expirations(symbol: str) -> list[str]:
    """Return sorted list of expiration date strings (YYYY-MM-DD) for a symbol."""
    data = _get("/markets/options/expirations", {"symbol": symbol, "includeAllRoots": True})
    expirations = (data.get("expirations") or {}).get("date", []) or []
    if isinstance(expirations, str):
        expirations = [expirations]
    return sorted(expirations)


def filter_expirations_by_dte(expirations: list[str], min_dte: int, max_dte: int) -> list[str]:
    """Keep only expirations whose DTE falls within [min_dte, max_dte]."""
    today = date.today()
    return [
        exp for exp in expirations
        if min_dte <= (datetime.strptime(exp, "%Y-%m-%d").date() - today).days <= max_dte
    ]


def get_options_chain(symbol: str, expiration: str) -> list[dict]:
    """Fetch one expiration's full chain including greeks."""
    data = _get("/markets/options/chains", {
        "symbol": symbol,
        "expiration": expiration,
        "greeks": "true",
    })
    options = (data.get("options") or {}).get("option", []) or []
    if isinstance(options, dict):
        options = [options]
    return options


def get_quote(symbol: str) -> dict:
    """Fetch the underlying spot quote for a single symbol."""
    data = _get("/markets/quotes", {"symbols": symbol})
    quote = (data.get("quotes") or {}).get("quote", {})
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    return quote


# ──────────────────────────────────────────────
# High-level fetch
# ──────────────────────────────────────────────

def fetch_all(
    tickers: list[str] | None = None,
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
) -> dict:
    """
    Pull quote + full options chain for every ticker.

    Returns
    -------
    dict  keyed by ticker symbol:
        {
            "quote":   { ... }           # underlying spot data
            "options": [ { ... }, ... ]  # flat list of all option contracts
        }
    """
    tickers = tickers or DEFAULT_TICKERS
    results: dict = {}

    for ticker in tickers:
        logger.info("Fetching %s ...", ticker)
        try:
            quote = get_quote(ticker)
            expirations = get_expirations(ticker)
            expirations = filter_expirations_by_dte(expirations, min_dte, max_dte)

            if not expirations:
                logger.warning("  %s: no expirations in DTE window %d–%d", ticker, min_dte, max_dte)
                continue

            all_options: list[dict] = []
            for exp in expirations:
                chain = get_options_chain(ticker, exp)
                all_options.extend(chain)
                time.sleep(0.15)  # stay well under Tradier rate limits

            results[ticker] = {"quote": quote, "options": all_options}
            logger.info(
                "  %s: %d contracts across %d expirations",
                ticker, len(all_options), len(expirations),
            )

        except requests.HTTPError as exc:
            logger.error("HTTP error for %s: %s", ticker, exc)
        except Exception as exc:
            logger.error("Unexpected error for %s: %s", ticker, exc)

    return results


def save_raw(data: dict, output_dir: str = RAW_DIR) -> str:
    """Persist raw fetch results to a timestamped JSON file. Returns the path."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"options_raw_{timestamp}.json")
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, default=str)
    logger.info("Raw data saved → %s", path)
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw = fetch_all()
    save_raw(raw)
