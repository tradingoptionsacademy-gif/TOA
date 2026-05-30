"""
Best Premium / Fair Value (BPFV) tier scoring.

Composite score (0 – 100)
─────────────────────────
  Component          Weight   What it rewards
  ─────────────────  ──────   ──────────────────────────────────────────────
  IV Quality           35 pts  Elevated IV (high IV rank within ticker)
  Spread Quality       25 pts  Tight bid-ask spread (better fill / less edge lost)
  Liquidity            20 pts  Deep OI + volume (easy entry/exit)
  Fair Value Gap       20 pts  Option priced at or below estimated fair value

Tier assignment
───────────────
  Tier 1 — Best  : bpfv_score >= 70
  Tier 2 — Good  : 45 <= bpfv_score < 70
  Tier 3 — Watch : bpfv_score < 45

Can be run standalone:
    python -m options_chain.bpfv_tier_logic data/clean/options_clean_<ts>.csv
"""

import os
import sys
import logging
from datetime import datetime

import numpy as np
import pandas as pd

from .config import TIER_1_MIN, TIER_2_MIN, TIER_DIR, MIN_DELTA, MAX_DELTA

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Score sub-components
# ──────────────────────────────────────────────

def _score_iv_quality(df: pd.DataFrame) -> pd.Series:
    """
    IV quality (0–35 pts).
    Ranks each contract's IV against the IV range seen for its underlying on
    today's chain. High IV rank = elevated premium environment = higher score.
    """
    out = pd.Series(np.zeros(len(df)), index=df.index)
    for ticker in df["underlying"].unique():
        mask = df["underlying"] == ticker
        iv_vals = df.loc[mask, "iv"].dropna()
        if iv_vals.empty or iv_vals.max() == iv_vals.min():
            continue
        iv_rank = (df.loc[mask, "iv"] - iv_vals.min()) / (iv_vals.max() - iv_vals.min())
        out.loc[mask] = (iv_rank * 35).clip(0, 35)
    return out.fillna(0)


def _score_spread_quality(df: pd.DataFrame) -> pd.Series:
    """
    Spread quality (0–25 pts).
    Linear decay from 25 (spread_pct = 0%) to 0 (spread_pct >= 25%).
    """
    sp = df["spread_pct"].clip(0, None).fillna(1.0)
    return (25 * (1 - (sp / 0.25).clip(0, 1))).round(4)


def _score_liquidity(df: pd.DataFrame) -> pd.Series:
    """
    Liquidity (0–20 pts).
    Log-scaled composite of OI (60%) and volume (40%), normalised per ticker
    so scores are relative within each underlying's universe.
    """
    oi_log = np.log1p(df["open_interest"].clip(0))
    vol_log = np.log1p(df["volume"].clip(0))
    raw = 0.6 * oi_log + 0.4 * vol_log

    out = pd.Series(np.zeros(len(df)), index=df.index)
    for ticker in df["underlying"].unique():
        mask = df["underlying"] == ticker
        rng = raw.loc[mask]
        if rng.max() == 0:
            continue
        out.loc[mask] = ((rng / rng.max()) * 20).clip(0, 20)
    return out.fillna(0)


def _score_fair_value_gap(df: pd.DataFrame) -> pd.Series:
    """
    Fair value gap (0–20 pts).
    Compares the option's market mid price to a simplified fair-value estimate:

        fair_value = intrinsic + time_premium
        time_premium = IV * spot * sqrt(DTE/365) * |delta| * 0.40

    gap_ratio = mid / fair_value
      < 1.0  →  trading below fair value (great)  → score near 20
      = 1.0  →  at fair value                     → score ≈ 10
      ≥ 2.0  →  significantly overpriced          → score → 0
    """
    dte_adj = (df["dte"].clip(1) / 365) ** 0.5
    delta_adj = df["abs_delta"].fillna(0.30).clip(0.05, 0.95)
    iv = df["iv"].fillna(df["iv"].median()).fillna(0.25)
    spot = df["underlying_last"].fillna(100)

    mask_c = df["option_type"] == "call"
    mask_p = df["option_type"] == "put"
    intrinsic = pd.Series(np.zeros(len(df)), index=df.index)
    intrinsic.loc[mask_c] = (spot.loc[mask_c] - df.loc[mask_c, "strike"]).clip(0)
    intrinsic.loc[mask_p] = (df.loc[mask_p, "strike"] - spot.loc[mask_p]).clip(0)

    time_premium = iv * spot * dte_adj * delta_adj * 0.40
    fair_value = (intrinsic + time_premium).clip(0.01)

    gap_ratio = (df["mid"] / fair_value).clip(0.25, 4.0)
    # Score: 20 at gap=0.25 (max discount), decays linearly to 0 at gap=2.0
    return (20 * (1 - ((gap_ratio - 0.25) / 1.75).clip(0, 1))).fillna(0)


# ──────────────────────────────────────────────
# Tier assignment
# ──────────────────────────────────────────────

def assign_tiers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute the four BPFV sub-scores, sum them into `bpfv_score`, and assign
    `bpfv_tier` labels.  Adds six new columns to a copy of df.
    """
    df = df.copy()

    df["score_iv"]       = _score_iv_quality(df).round(2)
    df["score_spread"]   = _score_spread_quality(df).round(2)
    df["score_liquidity"]= _score_liquidity(df).round(2)
    df["score_fv"]       = _score_fair_value_gap(df).round(2)
    df["bpfv_score"]     = (
        df["score_iv"] + df["score_spread"] + df["score_liquidity"] + df["score_fv"]
    ).round(2)

    df["bpfv_tier"] = pd.cut(
        df["bpfv_score"],
        bins=[-np.inf, TIER_2_MIN, TIER_1_MIN, np.inf],
        labels=["Tier 3 — Watch", "Tier 2 — Good", "Tier 1 — Best"],
    )

    logger.info("Tier distribution:\n%s", df["bpfv_tier"].value_counts().to_string())
    return df


def filter_tradeable(
    df: pd.DataFrame,
    min_delta: float = MIN_DELTA,
    max_delta: float = MAX_DELTA,
) -> pd.DataFrame:
    """Keep only contracts whose absolute delta falls within the tradeable range."""
    return df[
        df["abs_delta"].between(min_delta, max_delta, inclusive="both")
    ].copy()


def top_by_tier(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Return up to n top-scoring contracts per tier, sorted best-first."""
    return (
        df.sort_values("bpfv_score", ascending=False)
          .groupby("bpfv_tier", observed=True)
          .head(n)
          .sort_values(["bpfv_tier", "bpfv_score"], ascending=[True, False])
    )


# ──────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────

def save_tiers(df: pd.DataFrame, output_dir: str = TIER_DIR) -> dict[str, str]:
    """Save tiered DataFrame as CSV and JSON. Returns a dict of written paths."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths: dict[str, str] = {}

    csv_path = os.path.join(output_dir, f"bpfv_tiers_{ts}.csv")
    df.to_csv(csv_path, index=False)
    paths["csv"] = csv_path
    logger.info("Tier CSV  → %s", csv_path)

    json_path = os.path.join(output_dir, f"bpfv_tiers_{ts}.json")
    df.to_json(json_path, orient="records", indent=2, date_format="iso")
    paths["json"] = json_path
    logger.info("Tier JSON → %s", json_path)

    return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    clean_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not clean_path:
        raise SystemExit("Usage: python -m options_chain.bpfv_tier_logic <clean_csv_or_json_path>")

    df = pd.read_json(clean_path, orient="records") if clean_path.endswith(".json") else pd.read_csv(clean_path)
    df = filter_tradeable(df)
    df = assign_tiers(df)
    save_tiers(df)
    print("\nTop picks per tier:")
    print(top_by_tier(df, n=5).to_string())
