import os
from dotenv import load_dotenv

load_dotenv()

TRADIER_API_KEY = os.getenv("TRADIER_API_KEY", "")
TRADIER_BASE_URL = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1")

# Tickers to scan by default — override via CLI or run_pipeline.py args
DEFAULT_TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "TSLA", "NVDA", "AMD", "AMZN"]

# DTE window for expiration selection
MIN_DTE = 7
MAX_DTE = 60

# Delta range for tradeable contracts (absolute value, symmetric for calls + puts)
MIN_DELTA = 0.10
MAX_DELTA = 0.50

# Minimum liquidity thresholds — contracts below these are dropped during cleaning
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 10

# BPFV composite score thresholds (0–100 scale)
# Tier 1 — Best  : score >= TIER_1_MIN
# Tier 2 — Good  : TIER_2_MIN <= score < TIER_1_MIN
# Tier 3 — Watch : score < TIER_2_MIN
TIER_1_MIN = 70
TIER_2_MIN = 45

# Output directories (relative to the project root or OUTPUT_DIR env var)
_base = os.getenv("OUTPUT_DIR", "data")
RAW_DIR = os.path.join(_base, "raw")
CLEAN_DIR = os.path.join(_base, "clean")
TIER_DIR = os.path.join(_base, "tiers")
