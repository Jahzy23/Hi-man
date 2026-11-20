# Radar nightly
1. `python3 datafetch/fetch.py` (archive refresh), then `python3 research/ranking.py`.
2. Update research/news.md with a short dated scan (source URL and date for every item; if search
   is unavailable say so; never invent).
3. Re-run `python3 research/check_no_lookahead.py` only if a strategy module or the loader changed.
4. New setup ideas go into research/idea_queue.md with reason and regime, at most 3 a night. Do not
   add modules to strategies/ outside the Sunday review: every extra test raises the
   multiple-testing bar.
5. Note any regime change per market versus the previous run in research/regime_log.md.
