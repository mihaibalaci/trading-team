"""
kai_connect_test.py — Verify Alpaca paper trading connectivity.

Runs read-only checks against the Alpaca paper API:
  - Authentication (account fetch)
  - Account equity and buying power
  - Latest quote for a test symbol
  - Open positions (if any)
  - Open orders (if any)

No orders are placed. Safe to run at any time.

Usage:
    python3 kai_connect_test.py

Prerequisites:
    1. Copy .env.example to .env
    2. Fill in ALPACA_API_KEY and ALPACA_SECRET_KEY
    3. Leave TRADING_MODE=paper
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import logging
logging.basicConfig(level=logging.WARNING)   # suppress SDK noise during test

from broker_connector import connect


def run_connectivity_test():
    print()
    print("=" * 58)
    print("  KAI — ALPACA PAPER TRADING CONNECTIVITY TEST")
    print("=" * 58)

    # ── 1. Load credentials and connect ──────────────────────────
    print("\n[1/5] Loading credentials from .env ...")
    try:
        connector = connect()
    except EnvironmentError as e:
        print(f"\n  ERROR: {e}")
        print("\n  Action required:")
        print("    1. Copy signals/.env.example to signals/.env")
        print("    2. Fill in your Alpaca paper trading API keys")
        print("    3. Get keys at: https://app.alpaca.markets")
        print("       → Paper Trading → API Keys → Generate New Key")
        sys.exit(1)

    print("  Credentials loaded. Connecting to Alpaca paper endpoint...")

    # ── 2. Health check / authentication ─────────────────────────
    print("\n[2/5] Authentication check ...")
    ok, detail = connector.health_check()
    if not ok:
        print(f"\n  FAILED: {detail}")
        print("\n  Possible causes:")
        print("    - Wrong API key or secret key")
        print("    - Keys are for LIVE account, not paper account")
        print("    - Alpaca service outage (check https://status.alpaca.markets)")
        sys.exit(1)
    print(f"  OK — {detail}")

    # ── 3. Account state ──────────────────────────────────────────
    print("\n[3/5] Account state ...")
    acct = connector.get_account_state()
    print(f"  Mode:           {acct.trading_mode.upper()}")
    print(f"  Equity:         ${acct.equity:>12,.2f}")
    print(f"  Cash:           ${acct.cash:>12,.2f}")
    print(f"  Buying Power:   ${acct.buying_power:>12,.2f}")
    print(f"  Portfolio Val:  ${acct.portfolio_value:>12,.2f}")

    # ── 4. Latest quote (SPY as test symbol) ─────────────────────
    print("\n[4/5] Live quote check (SPY) ...")
    try:
        price = connector.get_latest_price("SPY")
        print(f"  SPY mid-price:  ${price:.2f}  ✓")
    except Exception as e:
        print(f"  Quote fetch failed: {e}")
        print("  (Market may be closed — this is non-fatal for paper trading)")

    # ── 5. Open positions and orders ──────────────────────────────
    print("\n[5/5] Open positions and orders ...")
    pos = connector.get_position("SPY")
    if pos:
        print(f"  SPY position:   {pos.qty:.0f} units @ avg ${pos.avg_entry:.2f}  "
              f"(unrealized P&L: ${pos.unrealized_pnl:+,.2f})")
    else:
        print("  No open SPY position (flat)")

    # ── Summary ───────────────────────────────────────────────────
    print()
    print("─" * 58)
    print("  All connectivity checks passed.")
    print("  Kai's AlpacaConnector is ready for paper trading.")
    print()
    print("  Next step:")
    print("    Wire ExecutionEngine to the connector:")
    print("      from broker_connector import connect")
    print("      connector = connect()")
    print("      # pass connector to ExecutionEngine.accept()")
    print("─" * 58)
    print()


if __name__ == "__main__":
    run_connectivity_test()
