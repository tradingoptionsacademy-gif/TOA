"""
BPFV Options Chain Pipeline — main entry point.

Runs the full three-step pipeline:
    1. Fetch  — pull live options chain data from Tradier
    2. Clean  — normalize, validate, add derived fields
    3. Tier   — score and assign BPFV tiers

Usage
-----
    # Run with defaults from config.py
    python -m options_chain.run_pipeline

    # Override tickers and DTE window
    python -m options_chain.run_pipeline --tickers SPY QQQ IWM --min-dte 14 --max-dte 45

    # Narrow to a specific delta range
    python -m options_chain.run_pipeline --min-delta 0.15 --max-delta 0.35
"""

import argparse
import logging
import sys
from pathlib import Path

# Allow running as `python scripts/options_chain/run_pipeline.py` without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from options_chain.fetch_options_chain import fetch_all, save_raw
from options_chain.clean_options_data import flatten_options, clean, save_clean
from options_chain.bpfv_tier_logic import assign_tiers, filter_tradeable, save_tiers, top_by_tier
from options_chain.config import DEFAULT_TICKERS, MIN_DTE, MAX_DTE, MIN_DELTA, MAX_DELTA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SEP = "═" * 58


def run(
    tickers: list[str],
    min_dte: int,
    max_dte: int,
    min_delta: float,
    max_delta: float,
) -> None:
    logger.info(SEP)
    logger.info("  BPFV Options Chain Pipeline")
    logger.info("  Tickers : %s", " ".join(tickers))
    logger.info("  DTE     : %d – %d days", min_dte, max_dte)
    logger.info("  Delta   : %.2f – %.2f", min_delta, max_delta)
    logger.info(SEP)

    # ── Step 1: Fetch ──────────────────────────────────────────
    logger.info("[1/3] Fetching options chain data from Tradier…")
    raw_data = fetch_all(tickers=tickers, min_dte=min_dte, max_dte=max_dte)
    raw_path = save_raw(raw_data)

    # ── Step 2: Clean ──────────────────────────────────────────
    logger.info("[2/3] Cleaning and normalising data…")
    df = flatten_options(raw_data)
    df = clean(df)
    clean_paths = save_clean(df)

    if df.empty:
        logger.warning("No contracts survived cleaning — check filters or data source.")
        return

    # ── Step 3: Score & Tier ───────────────────────────────────
    logger.info("[3/3] Scoring BPFV tiers…")
    df = filter_tradeable(df, min_delta=min_delta, max_delta=max_delta)
    df = assign_tiers(df)
    tier_paths = save_tiers(df)

    # ── Summary ────────────────────────────────────────────────
    logger.info(SEP)
    logger.info("Pipeline complete.")
    logger.info("  Raw    → %s", raw_path)
    logger.info("  Clean  → %s", clean_paths.get("csv"))
    logger.info("  Tiers  → %s", tier_paths.get("csv"))
    logger.info(SEP)

    top = top_by_tier(df, n=5)
    display_cols = [
        "underlying", "option_type", "strike", "expiration_date", "dte",
        "bid", "ask", "mid", "iv", "abs_delta",
        "bpfv_score", "bpfv_tier",
    ]
    display_cols = [c for c in display_cols if c in top.columns]
    print("\nTop picks per tier (up to 5 each):")
    print(top[display_cols].to_string(index=False))
    print()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BPFV Options Chain Pipeline")
    p.add_argument("--tickers",   nargs="+",  default=DEFAULT_TICKERS, metavar="TICKER")
    p.add_argument("--min-dte",   type=int,   default=MIN_DTE)
    p.add_argument("--max-dte",   type=int,   default=MAX_DTE)
    p.add_argument("--min-delta", type=float, default=MIN_DELTA)
    p.add_argument("--max-delta", type=float, default=MAX_DELTA)
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run(
        tickers=args.tickers,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        min_delta=args.min_delta,
        max_delta=args.max_delta,
    )
