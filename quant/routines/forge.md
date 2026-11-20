# Forge nightly
1. `python3 -m pytest bot/tests -q -p no:cacheprovider`; report failures, fix only real bugs, never
   skip or weaken a test.
2. Read bot/logs/supervisor.log and the last day of bot/logs/decisions.jsonl for crashes, restarts,
   feed errors, blocks and halts; fix root causes with a test first.
3. After any code change: `bot/run.sh restart`, confirm status, commit and push.
4. Never switch mode away from paper. bot/state/GO_LIVE_CONFIRMED is created by the owner only.
