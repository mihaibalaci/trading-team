"""
run_5_trades.py — Simulate 5 full trades through Finn → Remy pipeline.
Uses synthetic data with different scenarios (longs, shorts, wins, losses).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timedelta, time as dtime
from signal_engine import FinnSignal
from execution import ExecutionEngine, SessionGuard, TradeStatus

SCENARIOS = [
    {
        "name": "SPY Long — Hammer at S1",
        "instrument": "SPY", "direction": "long",
        "entry": 542.50, "stop": 540.80, "t1": 545.05, "t2": 547.00,
        "atr": 1.20, "strength": 74, "pattern": "Hammer", "confluence": 4,
        "bars": [
            (542.30, 542.10), (542.55, 542.35), (543.20, 542.80),
            (544.10, 543.60), (545.10, 544.50), (545.80, 545.30),
            (547.05, 546.40), (546.50, 546.10), (545.90, 545.60),
        ],
    },
    {
        "name": "QQQ Short — Evening Star at R1",
        "instrument": "QQQ", "direction": "short",
        "entry": 478.00, "stop": 479.60, "t1": 475.60, "t2": 474.00,
        "atr": 1.10, "strength": 68, "pattern": "Evening Star", "confluence": 3,
        "bars": [
            (478.20, 478.30), (477.95, 478.10), (477.40, 477.60),
            (476.80, 477.00), (475.55, 475.90), (475.20, 475.50),
            (473.95, 474.30), (474.50, 474.60), (474.90, 474.80),
        ],
    },
    {
        "name": "AAPL Long — Bullish Engulfing at Fib 61.8%",
        "instrument": "AAPL", "direction": "long",
        "entry": 218.40, "stop": 217.20, "t1": 220.20, "t2": 221.50,
        "atr": 0.85, "strength": 81, "pattern": "Bullish Engulfing", "confluence": 5,
        "bars": [
            (218.20, 218.00), (218.45, 218.25), (219.00, 218.70),
            (219.60, 219.30), (220.25, 219.90), (220.80, 220.40),
            (221.55, 221.10), (221.20, 221.00), (220.70, 220.80),
        ],
    },
    {
        "name": "TSLA Long — Stopped Out (failed breakout)",
        "instrument": "TSLA", "direction": "long",
        "entry": 275.00, "stop": 272.50, "t1": 278.75, "t2": 281.00,
        "atr": 1.80, "strength": 52, "pattern": "Pin Bar", "confluence": 3,
        "bars": [
            (274.80, 274.50), (275.10, 274.90), (274.60, 274.40),
            (273.80, 274.00), (273.20, 273.50), (272.40, 272.80),
        ],
    },
    {
        "name": "NVDA Short — Shooting Star at round number",
        "instrument": "NVDA", "direction": "short",
        "entry": 950.00, "stop": 954.00, "t1": 944.00, "t2": 940.00,
        "atr": 2.80, "strength": 70, "pattern": "Shooting Star", "confluence": 4,
        "bars": [
            (950.30, 950.50), (949.90, 950.10), (948.50, 949.00),
            (947.00, 947.80), (944.80, 945.50), (943.90, 944.30),
            (939.80, 940.50), (941.00, 940.80), (942.20, 941.50),
        ],
    },
]

def make_signal(s, now):
    d = s["direction"]
    entry, stop = s["entry"], s["stop"]
    dist = abs(entry - stop)
    equity = 100_000
    pos = round(equity * 0.01 / dist)
    return FinnSignal(
        timestamp=now, instrument=s["instrument"], direction=d,
        signal_strength=s["strength"], confidence="High" if s["strength"]>=65 else "Medium",
        timeframe="Intraday", model="MTF-Price-Action-v1",
        pattern_15m=s["pattern"], pattern_strength="high",
        confluence_score=s["confluence"],
        confluence_detail=f"{s['confluence']}/5 factors aligned",
        trend_bias_30m="bullish" if d=="long" else "bearish",
        stoch_k_15m=22.0 if d=="long" else 78.0,
        stoch_k_1m=25.0 if d=="long" else 75.0,
        entry_price=entry, stop_loss=stop, stop_distance=dist,
        target_1=s["t1"], target_2=s["t2"], atr_15m=s["atr"],
        risk_reward_t1=abs(s["t1"]-entry)/dist,
        position_size_1pct=pos,
    )


def run_trade(scenario, trade_num):
    s = scenario
    base = datetime(2026, 3, 31, 10, 30, 0) + timedelta(hours=trade_num)
    signal = make_signal(s, base)
    guard = SessionGuard(session_open=dtime(9,30), session_close=dtime(16,0), news_times=[])
    engine = ExecutionEngine(signal=signal, session_guard=guard, commission_per_unit=0.0)

    print(f"\n{'='*64}")
    print(f"  TRADE {trade_num+1}/5 — {s['name']}")
    print(f"{'='*64}")
    print(f"  {signal.direction.upper():5s}  {signal.instrument}  "
          f"str={signal.signal_strength}/100  conf={signal.confluence_score}/5  "
          f"pattern={signal.pattern_15m}")
    print(f"  Entry: ${signal.entry_price:.2f}  Stop: ${signal.stop_loss:.2f}  "
          f"T1: ${signal.target_1:.2f}  T2: ${signal.target_2:.2f}  "
          f"Size: {signal.position_size_1pct:.0f} shares")

    price0 = s["bars"][0][0]
    ok, reason = engine.accept(current_price=price0, now=base)
    if not ok:
        print(f"  REJECTED: {reason}")
        return 0.0

    print(f"  ACCEPTED @ ${price0:.2f}")
    print()

    for i, (price, ema9) in enumerate(s["bars"]):
        t = base + timedelta(minutes=i+1)
        prev_status = engine.trade.status
        log_before = len(engine._log)
        engine.tick(current_price=price, ema9_1m=ema9, now=t, spread=0.0)
        new_logs = engine._log[log_before:]
        status = engine.trade.status.value.upper().replace("_"," ")
        marker = ""
        for log_line in new_logs:
            if "T1" in log_line or "TARGET" in log_line.upper():
                marker = " ← T1 HIT"
            elif "T2" in log_line:
                marker = " ← T2 HIT"
            elif "STOP" in log_line.upper() and "breakeven" in log_line.lower():
                marker = " ← STOP→BE"
            elif "runner" in log_line.lower() and "stop" in log_line.lower():
                marker = " ← RUNNER STOPPED"
            elif "stopped" in log_line.lower():
                marker = " ← STOPPED OUT"
        print(f"  Bar {i+1}: ${price:.2f}  [{status}]{marker}")
        if engine.trade.status == TradeStatus.CLOSED:
            break

    report = engine.get_report()
    pnl_r = engine.trade.realized_pnl / max(signal.position_size_1pct, 1)
    dur = f"{report.hold_duration_min:.0f}m" if report.hold_duration_min else "—"
    result = "WIN" if pnl_r > 0 else "LOSS" if pnl_r < 0 else "FLAT"
    print(f"\n  Result: {result}  |  P&L: {pnl_r:+.2f}R  |  Duration: {dur}")
    return pnl_r


def main():
    print()
    print("=" * 64)
    print("  FINN / REMY — 5-TRADE SIMULATION")
    print("  Full pipeline: Signal → Validate → Execute → Report")
    print("=" * 64)

    total_r = 0.0
    results = []
    for i, scenario in enumerate(SCENARIOS):
        pnl = run_trade(scenario, i)
        total_r += pnl
        results.append((scenario["instrument"], scenario["direction"], pnl))

    print(f"\n\n{'='*64}")
    print("  SESSION SUMMARY")
    print(f"{'='*64}")
    wins = sum(1 for _,_,p in results if p > 0)
    losses = sum(1 for _,_,p in results if p < 0)
    for sym, d, p in results:
        tag = "WIN " if p > 0 else "LOSS" if p < 0 else "FLAT"
        print(f"  {sym:6s}  {d:5s}  {p:+.2f}R  [{tag}]")
    print(f"{'─'*64}")
    print(f"  Total: {total_r:+.2f}R  |  {wins}W / {losses}L  |  "
          f"Win rate: {wins/len(results)*100:.0f}%")
    print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
