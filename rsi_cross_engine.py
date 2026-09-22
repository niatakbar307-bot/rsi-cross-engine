import os
import sys
import requests
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: SUPABASE_URL or SUPABASE_KEY missing.")
    sys.exit(1)

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

FAPI_BASE = "https://fapi.binance.com"

SYMBOLS = [
    "SNDKUSDT",
    "ETHUSDT",
    "DOGEUSDT",
    "SNXXUSDT",
    "SKHYNIXUSDT",
    "BZUSDT",
    "KAITOUSDT",
    "HYPEUSDT",
    "BTCUSDT",
    "AGTUSDT",
]

INTERVAL = "1h"
KLINES_LIMIT = 100

RSI_FAST = 6
RSI_MED = 14
RSI_SLOW = 24

STOP_LOSS_PCT = 0.02
TARGET_PCT = 0.02

COOLDOWN_HOURS = 12


def get_klines(symbol, interval=INTERVAL, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(f"{FAPI_BASE}/fapi/v1/klines", params=params, timeout=20)
