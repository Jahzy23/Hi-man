"""Forward log: finalists on bars after the backtest window (never used for selection).
Run from quant/:  python3 -m lab.forward
Funding after the archive end is counted as zero (ESTIMATE). Uses the phase-2 primary ExecConfig."""
import json
import pandas as pd
from lab import engine as E, phase2 as P, run_all as RA

START = pd.Timestamp("2026-10-31 16:00", tz="UTC")


def main():
    top = json.load(open("lab/results/top3.json"))
    cfg = P.primary_cfg()
    rows = []
    for f in top["top3"]:
        rank = f["rank"]
        name = f["module"]
        syms = f["symbols"]
        m = RA.load_module(name)
        for s in syms:
            df = E.get_frame(s, m.TIMEFRAME)
            end = df.index[-1]
            r = E.run_module(m, s, params=f.get("params"), start=START, end=end, cfg=cfg)
            px = df.loc[START:end, "close"]
            rows.append(dict(setup=name, rank=rank, symbol=s, trades=len(r.trades),
                             net=r.net_return, end=str(end), move=px.iloc[-1] / px.iloc[0] - 1))
    print(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    main()
