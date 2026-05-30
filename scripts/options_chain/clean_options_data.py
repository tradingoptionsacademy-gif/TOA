"""
Cleans and normalizes raw options chain JSON produced by fetch_options_chain.

Steps applied
-------------
1. Flatten nested JSON (greeks sub-object merged into main row).
2. Cast all numeric fields; coerce bad values to NaN.
3. Drop rows missing required identifiers (symbol, strike, bid/ask, expiry).
4. Drop contracts with inverted or zero bid/ask markets.
5. Apply minimum open-interest and volume filters.
6. Add derived columns: mid, spread, spread_pct, dte, moneyness, abs_delta, iv.
7. Save to data/clean/ as both CSV and JSON.

Can be run standalone:
    python -m options_chain.clean_options_data data/raw/options_raw_<ts>.json
"""

import json
import os
import sys
import logging
from datetime import date, datetime

import numpy as np
import pandas as pd

from .config import MIN_OPEN_INTEREST, MIN_VOLUME, CLEAN_DIR

logger = logging.getLogger(__name__)

_REQUIRED = ["symbol", "underlying", "option_type", "strike", "expiration_date", "bid", "ask"]


# ──────────────────────────────────────────────
# I/O helpers
# ──────────────────────────────────────────────

def load_raw(path: str) -> dict:
    with open(path) as fh:
        return json.load(fh)


def flatten_options(raw: dict) -> pd.DataFrame:
    """
    Convert the {ticker: {quote, options}} structure into a flat DataFrame.
    Greeks sub-dict is merged into each row. Underlying spot price is added
    as `underlying_last`.
    """
    rows = []
    for ticker, payload in raw.items():
        quote = payload.get("quote") or {}
        spot = quote.get("last") or quote.get("close")
        for opt in payload.get("options") or []:
            opt = dict(opt)  # copy so we don't mutate the source
            greeks = opt.pop("greeks", None) or {}
            rows.append({**opt, **greeks, "underlying_last": spot})
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# Cleaning pipeline
# ──────────────────────────────────────────────

def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all cleaning and normalization steps. Returns a new DataFrame."""
    df = df.copy()

    # ── 1. Drop rows missing required fields ──
    df = df.dropna(subset=[c for c in _REQUIRED if c in df.columns])

    # ── 2. Numeric casts ──
    numeric_cols = [
        "strike", "bid", "ask", "last", "volume", "open_interest",
        "underlying_last", "delta", "gamma", "theta", "vega",
        "mid_iv", "bid_iv", "ask_iv", "smv_vol",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = _to_numeric(df[col])

    df["volume"] = df.get("volume", pd.Series(0, index=df.index)).fillna(0).astype(int)
    df["open_interest"] = df.get("open_interest", pd.Series(0, index=df.index)).fillna(0).astype(int)

    # ── 3. Canonical IV: prefer mid_iv, fall back to smv_vol ──
    if "mid_iv" in df.columns and "smv_vol" in df.columns:
        df["iv"] = df["mid_iv"].combine_first(df["smv_vol"])
    elif "mid_iv" in df.columns:
        df["iv"] = df["mid_iv"]
    elif "smv_vol" in df.columns:
        df["iv"] = df["smv_vol"]
    else:
        df["iv"] = np.nan

    # Drop rows with clearly bad IV (negative)
    df = df[df["iv"].isna() | (df["iv"] > 0)]

    # ── 4. Market sanity checks ──
    df = df[(df["bid"] >= 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])]

    # ── 5. Liquidity filters ──
    df = df[df["open_interest"] >= MIN_OPEN_INTEREST]
    df = df[df["volume"] >= MIN_VOLUME]

    # ── 6. Normalise option_type label ──
    df["option_type"] = (
        df["option_type"].str.lower()
        .map({"call": "call", "put": "put"})
    )
    df = df[df["option_type"].isin(["call", "put"])]

    # ── 7. Derived columns ──
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["spread"] = df["ask"] - df["bid"]
    df["spread_pct"] = df["spread"] / df["mid"].replace(0, np.nan)

    df["expiration_date"] = pd.to_datetime(df["expiration_date"]).dt.date
    today = date.today()
    df["dte"] = df["expiration_date"].apply(lambda x: (x - today).days)

    # Moneyness: > 1 means ITM for both calls and puts
    mask_c = df["option_type"] == "call"
    mask_p = df["option_type"] == "put"
    df["moneyness"] = np.nan
    df.loc[mask_c, "moneyness"] = df.loc[mask_c, "underlying_last"] / df.loc[mask_c, "strike"]
    df.loc[mask_p, "moneyness"] = df.loc[mask_p, "strike"] / df.loc[mask_p, "underlying_last"]

    df["abs_delta"] = df["delta"].abs()

    df = df.reset_index(drop=True)
    logger.info("Cleaned: %d rows, %d columns", len(df), len(df.columns))
    return df


# ──────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────

def save_clean(
    df: pd.DataFrame,
    output_dir: str = CLEAN_DIR,
    fmt: str = "both",
) -> dict[str, str]:
    """Save cleaned DataFrame as CSV and/or JSON. Returns a dict of written paths."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths: dict[str, str] = {}

    if fmt in ("csv", "both"):
        p = os.path.join(output_dir, f"options_clean_{ts}.csv")
        df.to_csv(p, index=False)
        paths["csv"] = p
        logger.info("Clean CSV  → %s", p)

    if fmt in ("json", "both"):
        p = os.path.join(output_dir, f"options_clean_{ts}.json")
        df.to_json(p, orient="records", indent=2, date_format="iso")
        paths["json"] = p
        logger.info("Clean JSON → %s", p)

    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not raw_path:
        raise SystemExit("Usage: python -m options_chain.clean_options_data <raw_json_path>")
    raw = load_raw(raw_path)
    df = flatten_options(raw)
    df = clean(df)
    save_clean(df)
