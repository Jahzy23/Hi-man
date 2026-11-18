# Shared contract (Radar, Lab, Forge, Shield all follow this)

## Settings (from the owner)
- Markets: BTCUSDT, ETHUSDT, SOLUSDT USDT-M perps on Binance. Timeframes 1h and 4h.
- Paper account $1,000. Risk 0.5% of equity per trade. Max leverage cap 3x notional.
- Fees: maker 0.02%, taker 0.05%. Funding every 8h (4h at times on SOL), taken from real history.
- Shield: halt at -3% on the UTC day, 3 straight losses, feed stale > 60s, or any exception.
- Real money only after the owner says "go live". Paper first, always.

## Data
- `strategies/data.py: load(sym, tf)` is the ONLY loader. Index = bar open time UTC. All non-price
  columns are known at bar close. Source data.binance.vision, see `data/MANIFEST.json`.
- Backtest window: 2022-11-01 → 2026-10-31 15:59 UTC (funding archive ends there). 2022-08..10 is warm-up.
- Open interest is available for the full window (5-min archive resampled to 1h).
- No symbol in scope was delisted; survivorship note must still be stated in every report.

## Strategy interface (same code runs in backtest and in the bot)
Each setup is a module `strategies/<name>.py` exposing:

    NAME, TIMEFRAME ("1h"|"4h"), REGIME ("trending"|"ranging"|"volatile"), ONE_LINER, DEFAULT_PARAMS: dict
    def signals(df: pd.DataFrame, p: dict, ctx: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame

`df` is `load(sym, TIMEFRAME)`; `ctx` optionally holds other symbols' frames on the same timeframe
(e.g. BTC for lag setups). Returns a frame on the same index with columns:

| column | meaning |
|---|---|
| side | +1 long, -1 short, 0 none. Decided at the CLOSE of row i using rows <= i only. |
| entry | limit price for an order placed at the open of row i+1. NaN = marketable limit at next open (taker). |
| entry_valid | bars the resting limit stays live (int, default 1), then cancelled. |
| stop | protective stop price. REQUIRED for every signal. |
| target | take-profit price or NaN. |
| max_hold | max bars in trade, exit at next open after that (int or NaN). |
| exit | bool. If True at close of row i while in a position, exit at open of row i+1. |

Rules: no look-ahead (anything rolling must end at row i; no centered windows, no .shift(-n),
no full-sample normalisation). `signals(df.iloc[:k])` must equal `signals(df).iloc[:k]` row for row.

## Execution model (Lab backtester = bot paper broker)
- Limit entry fills only if price trades THROUGH the limit (low < limit for buys, high > limit for sells). Maker fee.
- Marketable entries, stop exits, time exits and signal exits pay taker fee + slippage:
  1 bp BTC/ETH, 2 bp SOL; stop exits 2x that. Target exits are resting limits: maker fee, must trade through.
- Stop and target touched in the same bar → assume stop hit first. Gap through stop → fill at bar open.
- Funding: position notional x funding_paid on every bar held (longs pay positive funding).
- One open position per (setup, symbol). Size = 0.005 x equity / |entry - stop|, capped at 3x equity notional.
- Stress runs: fees x2, slippage x2.
