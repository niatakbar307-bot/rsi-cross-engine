import os
import sys
import requests

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

# Spot API استعمال ہو رہا ہے (futures fapi.binance.com کبھی کبھار
# GitHub Actions کے سرورز کو geo-block کر دیتا ہے — 451 error)
SPOT_BASE = "https://data-api.binance.vision"

EXCLUDE_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
EXCLUDE_BASES = {
    "USDC", "FDUSD", "TUSD", "USDP", "USD1", "RLUSD", "U", "USDE", "BFUSD",
    "XUSD", "PYUSD", "DAI", "USDS", "BUSD", "USDD", "FRAX", "LUSD",
    "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL", "ARS", "UAH", "PLN", "RON", "CZK", "JPY",
}

INTERVAL = "1h"
KLINES_LIMIT = 100
BREAKOUT_LOOKBACK = 6       # کم از کم 6 گھنٹے کی نئی بلندی (12/24 گھنٹے نایاب ثابت ہوئے)
MIN_QUOTE_VOLUME = 5_000_000  # کم از کم 24h USDT ٹرن اوور
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLIER = 1.05    # حالیہ کینڈل کا volume اوسط سے کتنا زیادہ ہو (مزید نرم)

RSI_PERIOD = 14
ATR_PERIOD = 14
ATR_STOP_MULTIPLIER = 2.5
ATR_TARGET_MULTIPLIERS = (4.0, 6.0, 8.0, 10.0, 12.0)
# غیر معمولی بڑے ATR کو حد میں رکھیں تاکہ سٹاپ/ٹارگٹ قابلِ حصول رہیں
MAX_ATR_PCT_FOR_LEVELS = 0.02  # قیمت کا 2%


def get_top_usdt_symbols(limit=150):
    resp = requests.get(f"{SPOT_BASE}/api/v3/ticker/24hr", timeout=20)
    resp.raise_for_status()
    data = resp.json()
    usdt_pairs = []
    for d in data:
        symbol = d["symbol"]
        if not symbol.endswith("USDT") or symbol.endswith(EXCLUDE_SUFFIXES):
            continue
        if symbol[:-4] in EXCLUDE_BASES:
            continue
        try:
            quote_volume = float(d["quoteVolume"])
        except (KeyError, ValueError):
            continue
        if quote_volume < MIN_QUOTE_VOLUME:
            continue
        usdt_pairs.append((symbol, quote_volume))
    usdt_pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in usdt_pairs[:limit]]


def get_klines(symbol, interval=INTERVAL, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(f"{SPOT_BASE}/api/v3/klines", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def calc_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    rsi = 100 - (100 / (1 + rs))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_atr(highs, lows, closes, period=ATR_PERIOD):
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def check_breakout(highs, closes, volumes):
    """
    کم از کم BREAKOUT_LOOKBACK گھنٹوں کی سب سے بلند قیمت سے اوپر بند ہونا،
    ساتھ volume کی تصدیق۔
    """
    if len(closes) < BREAKOUT_LOOKBACK + 2:
        return False

    last_close = closes[-1]
    prior_high = max(highs[-(BREAKOUT_LOOKBACK + 1):-1])  # موجودہ کینڈل سے پہلے کی بلندی

    if last_close <= prior_high:
        return False

    last_volume = volumes[-1]
    avg_volume = sum(volumes[-(VOLUME_LOOKBACK + 1):-1]) / VOLUME_LOOKBACK
    if avg_volume <= 0 or last_volume < avg_volume * VOLUME_MULTIPLIER:
        return False

    return True


def has_open_position(symbol):
    url = f"{SUPABASE_URL}/rest/v1/positions"
    params = {"symbol": f"eq.{symbol}", "status": "eq.open", "select": "id", "limit": "1"}
    resp = requests.get(url, headers=SB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return len(resp.json()) > 0


def calc_levels(entry_price, atr):
    effective_atr = min(atr, entry_price * MAX_ATR_PCT_FOR_LEVELS)
    stop_loss = entry_price - (effective_atr * ATR_STOP_MULTIPLIER)
    targets = [entry_price + (effective_atr * m) for m in ATR_TARGET_MULTIPLIERS]
    return stop_loss, targets


def save_signal(symbol, entry_price, rsi, atr):
    stop_loss, targets = calc_levels(entry_price, atr)
    positions_url = f"{SUPABASE_URL}/rest/v1/positions"
    payload = {
        "symbol": symbol,
        "signal_type": "BUY",
        "entry_price": entry_price,
        "rsi": rsi,
        "atr": atr,
        "status": "open",
        "stop_loss": stop_loss,
        "target_1": targets[0],
        "target_2": targets[1],
        "target_3": targets[2],
        "target_4": targets[3],
        "target_5": targets[4],
        "targets_hit": 0,
    }
    r = requests.post(positions_url, headers=SB_HEADERS, json=payload, timeout=20)
    if not r.ok:
        print(f"Save failed for {symbol}: {r.status_code} {r.text}")
    r.raise_for_status()
    print(f"Signal saved: {symbol} -> BUY @ {entry_price} (24h breakout, ATR={atr}, SL={stop_loss}, TP1={targets[0]})")


def main():
    print(f"Starting {BREAKOUT_LOOKBACK}h Breakout engine...")

    symbols = get_top_usdt_symbols()
    print(f"Scanning {len(symbols)} symbols...")

    signals_found = 0

    for symbol in symbols:
        try:
            klines = get_klines(symbol)
            klines = klines[:-1]  # ادھوری کینڈل ہٹائیں

            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            volumes = [float(k[5]) for k in klines]

            if not check_breakout(highs, closes, volumes):
                continue

            rsi = calc_rsi(closes)

            if has_open_position(symbol):
                print(f"Skip {symbol}: already has an open position.")
                continue

            atr = calc_atr(highs, lows, closes)
            if not atr or atr <= 0 or atr != atr or atr in (float("inf"), float("-inf")):
                print(f"Skip {symbol}: invalid ATR.")
                continue
            if rsi is not None and (rsi != rsi or rsi in (float("inf"), float("-inf"))):
                print(f"Skip {symbol}: invalid RSI, saving without it.")
                rsi = None

            entry_price = closes[-1]
            save_signal(symbol, entry_price, rsi, atr)
            signals_found += 1

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            continue

    print(f"Done. Signals found this run: {signals_found}")


if __name__ == "__main__":
    main()
