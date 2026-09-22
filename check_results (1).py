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


def get_open_positions():
    url = f"{SUPABASE_URL}/rest/v1/positions"
    params = {"status": "eq.open", "select": "*"}
    resp = requests.get(url, headers=SB_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def get_current_price(symbol):
    resp = requests.get(
        f"{FAPI_BASE}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=20
    )
    resp.raise_for_status()
    return float(resp.json()["price"])


def check_outcome(position, current_price):
    """
    فکسڈ 2% سٹاپ لاس/ٹارگٹ کے مطابق چیک کرتا ہے:
    - BUY: قیمت target_1 تک پہنچے تو WIN، stop_loss تک گرے تو LOSS
    - SELL: قیمت target_1 تک گرے تو WIN، stop_loss تک بڑھے تو LOSS
    """
    signal_type = position["signal_type"]
    stop_loss = float(position["stop_loss"])
    target = float(position["target_1"])

    if signal_type == "BUY":
        if current_price >= target:
            return "WIN"
        if current_price <= stop_loss:
            return "LOSS"
    else:  # SELL
        if current_price <= target:
            return "WIN"
        if current_price >= stop_loss:
            return "LOSS"

    return None


def move_to_results(position, exit_price, outcome):
    entry_price = float(position["entry_price"])
    signal_type = position["signal_type"]

    if signal_type == "BUY":
        pnl_percent = (exit_price - entry_price) / entry_price * 100
    else:
        pnl_percent = (entry_price - exit_price) / entry_price * 100

    results_url = f"{SUPABASE_URL}/rest/v1/results"
    payload = {
        "position_id": position["id"],
        "symbol": position["symbol"],
        "signal_type": signal_type,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "stop_loss": position["stop_loss"],
        "target_1": position["target_1"],
        "outcome": outcome,
        "pnl_percent": pnl_percent,
        "rsi6": position.get("rsi6"),
        "rsi14": position.get("rsi14"),
        "rsi24": position.get("rsi24"),
        "opened_at": position.get("created_at"),
    }
    r = requests.post(results_url, headers=SB_HEADERS, json=payload, timeout=20)
    r.raise_for_status()

    positions_url = f"{SUPABASE_URL}/rest/v1/positions"
    update_params = {"id": f"eq.{position['id']}"}
    update_payload = {"status": "closed"}
    r2 = requests.patch(
        positions_url, headers=SB_HEADERS, params=update_params, json=update_payload, timeout=20
    )
    r2.raise_for_status()

    print(f"{position['symbol']}: {outcome} (PnL: {pnl_percent:.2f}%)")


def main():
    print("Checking open positions for stop-loss/target hits...")

    open_positions = get_open_positions()
    print(f"Found {len(open_positions)} open position(s).")

    closed_count = 0

    for position in open_positions:
        try:
            symbol = position["symbol"]
            current_price = get_current_price(symbol)
            outcome = check_outcome(position, current_price)

            if outcome is not None:
                move_to_results(position, current_price, outcome)
                closed_count += 1
            else:
                print(f"{symbol}: still open, no hit yet.")

        except Exception as e:
            print(f"Error processing {position.get('symbol')}: {e}")
            continue

    print(f"Done. Closed {closed_count} position(s) this run.")


if __name__ == "__main__":
    main()
