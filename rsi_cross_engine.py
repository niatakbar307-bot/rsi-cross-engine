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

# ATR کی بنیاد پر سٹاپ لاس اور ٹارگٹ (فکسڈ % کی بجائے)
ATR_PERIOD = 14
STOP_LOSS_ATR_MULT = 1.5
TARGET_ATR_MULT = 2.0


def get_klines(symbol, interval=INTERVAL, limit=KLINES_LIMIT):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(f"{FAPI_BASE}/fapi/v1/klines", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_ohlc(klines):
    """
    Binance ہمیشہ آخری کینڈل کے طور پر ابھی جاری (ادھوری) کینڈل بھی بھیجتا ہے۔
    ہم اسے ہٹا دیتے ہیں تاکہ صرف مکمل بند ہو چکی کینڈلز پر RSI/ATR کیلکولیٹ ہو —
    ورنہ کراس اور ATR ہر چند منٹ بعد بدلتے رہیں گے اور غیر مستحکم رہیں گے۔
    """
    if not klines:
        return [], [], []
    highs = [float(k[2]) for k in klines[:-1]]
    lows = [float(k[3]) for k in klines[:-1]]
    closes = [float(k[4]) for k in klines[:-1]]
    return highs, lows, closes


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


def calc_atr(highs, lows, closes, period=ATR_PERIOD):
    """
    Average True Range: مارکیٹ کی حقیقی volatility ناپتا ہے،
    تاکہ SL/Target فکسڈ % کی بجائے ہر کوائن کی اپنی حرکت کے مطابق سیٹ ہوں۔
    """
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def check_cross(closes):
    """
    RSI(6) کا RSI(14) اور RSI(24) دونوں کو کراس کرنا:
    - BUY: RSI6 پہلے دونوں سے اوپر نہیں تھا (کم از کم ایک سے نیچے/برابر)، اب دونوں سے اوپر ہے
    - SELL: RSI6 پہلے دونوں سے نیچے نہیں تھا (کم از کم ایک سے اوپر/برابر)، اب دونوں سے نیچے ہے

    نوٹ: پرانی شرط (was_below/was_above) بہت سخت تھی — وہ صرف اُس کینڈل کو پکڑتی تھی
    جہاں RSI6 ایک ہی قدم میں دونوں لائنز سے نیچے سے دونوں کے اوپر (یا اس کے برعکس)
    چلا جائے۔ حقیقت میں RSI6 عام طور پر پہلے ایک لائن کراس کرتا ہے، پھر کچھ دیر بعد
    دوسری — اس لیے وہ سخت شرط اکثر کوئی کراس پکڑ ہی نہیں پاتی تھی۔ نیا منطق اس
    لمحے کو پکڑتا ہے جب RSI6 آخری بار جس بھی ایک لائن سے نیچے (یا اوپر) تھا،
    اُس سے بھی نکل کر دونوں سے اوپر (یا نیچے) ہو جائے۔
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

    was_not_above = not (f_prev > m_prev and f_prev > s_prev)
    now_above = f_now > m_now and f_now > s_now
    if was_not_above and now_above:
        return "BUY", rsi_snapshot

    was_not_below = not (f_prev < m_prev and f_prev < s_prev)
    now_below = f_now < m_now and f_now < s_now
    if was_not_below and now_below:
        return "SELL", rsi_snapshot

    return None, rsi_snapshot


def has_open_position(symbol):
    url = f"{SUPABASE_URL}/rest/v1/positions"
    params = {"symbol": f"eq.{symbol}", "status": "eq.open", "select": "id", "limit": "1"}
    resp = requests.get(url, headers=SB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return len(resp.json()) > 0


def calc_levels(signal_type, entry_price, atr):
    if signal_type == "BUY":
        stop_loss = entry_price - (atr * STOP_LOSS_ATR_MULT)
        target = entry_price + (atr * TARGET_ATR_MULT)
    else:
        stop_loss = entry_price + (atr * STOP_LOSS_ATR_MULT)
        target = entry_price - (atr * TARGET_ATR_MULT)
    return stop_loss, target


def save_signal(symbol, signal_type, entry_price, rsi_snapshot, atr):
    stop_loss, target = calc_levels(signal_type, entry_price, atr)
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
    print(f"Signal saved: {symbol} -> {signal_type} @ {entry_price} (ATR={atr:.6f}, SL={stop_loss}, TP={target})")


def main():
    print("Starting RSI-cross signal engine (10-symbol test project)...")

    signals_found = 0

    for symbol in SYMBOLS:
        try:
            klines = get_klines(symbol)
            highs, lows, closes = get_ohlc(klines)  # صرف بند ہو چکی کینڈلز

            signal_type, rsi_snapshot = check_cross(closes)

            if signal_type is not None:
                if has_open_position(symbol):
                    print(f"Skip {symbol}: already has an open position.")
                    continue
                atr = calc_atr(highs, lows, closes)
                if atr is None:
                    print(f"Skip {symbol}: not enough data for ATR.")
                    continue
                entry_price = closes[-1]
                save_signal(symbol, signal_type, entry_price, rsi_snapshot, atr)
                signals_found += 1
            else:
                print(f"{symbol}: no cross.")

        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            continue

    print(f"Done. Signals found this run: {signals_found}")


if __name__ == "__main__":
    main()
