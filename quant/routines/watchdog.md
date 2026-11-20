# Watchdog (Shield's outside check)
1. `cd /home/user/Hi-man/quant && bot/run.sh status | head -20`.
2. If the bot is not running and bot/state has books: `bot/run.sh start`. If bot/state is empty
   (new container): `git pull`, then `bot/run.sh start --restore`. Never use --fresh.
3. If Shield shows a HALT that needs a human, do NOT clear it. Leave it and say so.
4. Commit and push `paper_state/` if it changed.
5. Reply in one or two lines: running or not, WARMING or trading, open positions, any halt.
   If nothing changed since the last check, say "no change".
