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

# نوٹ: SNDK, SKHYNIX, SNXX, BZ جیسے Stock Perps صرف Futures مارکیٹ پر ہیں،
# اس لیے یہاں Spot API (data-api.binance.vision) کی بجائے Futures API استعمال ہو رہا ہے۔
FAPI_BASE = "https://fapi.binance.com"

# آپ کی منتخب کردہ 10 علامتیں
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

STOP_LOSS_PCT = 0.02   # فکسڈ 2%
TARGET_PCT = 0.02      # فکسڈ 2%

COOLDOWN_HOURS = 12


def get_klines(symbol, interval=INTERVAL, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(f"{FAPI_BASE}/fapi/v1/klines", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_closed_closes(klines):
    """
    Binance ہمیشہ آخری کینڈل کے طور پر ابھی جاری (ادھوری) کینڈل بھی بھیجتا ہے۔
    ہم اسے ہٹا دیتے ہیں تاکہ صرف مکمل بند ہو چکی کینڈلز پر RSI کیلکولیٹ ہو —
    ورنہ کراس ہر 5 منٹ بعد بدلتا رہے گا اور غیر مستحکم رہے گا۔
    """
    if not klines:
        return []
    return [float(k[4]) for k in klines[:-1]]


def calc_rsi(closes, period):
    if len(closes) < period + 1:
        return [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    rsi_values = [None] * period
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    rsi_values.append(100 - (100 / (1 + rs)))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi_values.append(100 - (100 / (1 + rs)))
    return rsi_values


def check_cross(closes):
    """
    RSI(6) کا RSI(14) اور RSI(24) دونوں کو کراس کرنا:
    - نیچے سے اوپر کراس (RSI6 دونوں سے کم تھا، اب دونوں سے زیادہ) -> BUY
    - اوپر سے نیچے کراس (RSI6 دونوں سے زیادہ تھا، اب دونوں سے کم) -> SELL
    """
    need = max(RSI_SLOW, RSI_MED, RSI_FAST) + 2
    if len(closes) < need:
        return None, None

    rsi_fast = calc_rsi(closes, RSI_FAST)
    rsi_med = calc_rsi(closes, RSI_MED)
    rsi_slow = calc_rsi(closes, RSI_SLOW)

    f_now, f_prev = rsi_fast[-1], rsi_fast[-2]
    m_now, m_prev = rsi_med[-1], rsi_med[-2]
    s_now, s_prev = rsi_slow[-1], rsi_slow[-2]

    if None in (f_now, f_prev, m_now, m_prev, s_now, s_prev):
        return None, None

    rsi_snapshot = {"rsi6": f_now, "rsi14": m_now, "rsi24": s_now}

    was_below = f_prev <= m_prev and f_prev <= s_prev
    now_above = f_now > m_now and f_now > s_now
    if was_below and now_above:
        return "BUY", rsi_snapshot

    was_above = f_prev >= m_prev and f_prev >= s_prev
    now_below = f_now < m_now and f_now < s_now
    if was_above and now_below:
        return "SELL", rsi_snapshot

    return None, rsi_snapshot


def has_open_position(symbol):
    url = f"{SUPABASE_URL}/rest/v1/positions"
    params = {"symbol": f"eq.{symbol}", "status": "eq.open", "select": "id", "limit": "1"}
    resp = requests.get(url, headers=SB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return len(resp.json()) > 0


def is_in_cooldown(symbol, cooldown_hours=COOLDOWN_HOURS):
    url = f"{SUPABASE_URL}/rest/v1/positions"
    params = {
        "symbol": f"eq.{symbol}",
        "select": "created_at",
        "order": "created_at.desc",
        "limit": "1",
    }
    resp = requests.get(url, headers=SB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return False

    last_created = rows[0].get("created_at")
    if not last_created:
        return False

    last_time = datetime.fromisoformat(last_created.replace("Z", "+00:00"))
    elapsed_hours = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600
    return elapsed_hours < cooldown_hours


def calc_levels(signal_type, entry_price):
    if signal_type == "BUY":
        stop_loss = entry_price * (1 - STOP_LOSS_PCT)
        target = entry_price * (1 + TARGET_PCT)
    else:
        stop_loss = entry_price * (1 + STOP_LOSS_PCT)
        target = entry_price * (1 - TARGET_PCT)
    return stop_loss, target


def save_signal(symbol, signal_type, entry_price, rsi_snapshot):
    stop_loss, target = calc_levels(signal_type, entry_price)
    positions_url = f"{SUPABASE_URL}/rest/v1/positions"
    payload = {
        "symbol": symbol,
        "signal_type": signal_type,
        "entry_price": entry_price,
        "rsi6": rsi_snapshot["rsi6"],
        "rsi14": rsi_snapshot["rsi14"],
        "rsi24": rsi_snapshot["rsi24"],
        "status": "open",
        "stop_loss": stop_loss,
        "target_1": target,
        "targets_hit": 0,
    }
    r = requests.post(positions_url, headers=SB_HEADERS, json=payload, timeout=20)
    r.raise_for_status()
    print(f"Signal saved: {symbol} -> {signal_type} @ {entry_price} (SL={stop_loss}, TP={target})")


def main():
    print("Starting RSI-cross signal engine (10-symbol test project)...")

    signals_found = 0

    for symbol in SYMBOLS:
        try:
            klines = get_klines(symbol)
            closes = get_closed_closes(klines)  # صرف بند ہو چکی کینڈلز

            signal_type, rsi_snapshot = check_cross(closes)

            if signal_type is not None:
                if has_open_position(symbol):
                    print(f"Skip {symbol}: already has an open position.")
                    continue
                if is_in_cooldown(symbol):
                    print(f"Skip {symbol}: cooldown active.")
                    continue
                entry_price = closes[-1]
                save_signal(symbol, signal_type, entry_price, rsi_snapshot)
                signals_found += 1
            else:
                print(f"{symbol}: no cross.")

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            continue

    print(f"Done. Signals found this run: {signals_found}")


if __name__ == "__main__":
    main()
