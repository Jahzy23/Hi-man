# Lab nightly
1. `python3 -m pytest lab/tests -q -p no:cacheprovider` must pass before anything else.
2. Compare paper results with backtest expectation for each book: for every closed paper trade in
   bot/logs/trades.csv, replay the same setup and symbol through lab.engine on the archived bars for
   that period and record entry/exit/price/P&L differences in lab/results/paper_vs_backtest.md.
   Flag any trade the backtest does not reproduce.
3. Do NOT re-open the holdout, re-tune parameters, or re-rank on the same data. Months that arrive
   after 2026-10-31 are new out-of-sample data: extend lab/results/forward_log.md with each
   finalist's result on them.
4. Runner-up rule: a runner-up replaces the leader only after 7 paper days in which it is stronger
   (higher net paper P&L and no worse drawdown). Track the day count in lab/results/succession.md.
   Never swap automatically; recommend it in the brief.
