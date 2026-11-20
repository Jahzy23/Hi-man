# Routines (scheduled tasks fire these into the lead session)

All routines work in /home/user/Hi-man/quant on branch claude/crypto-quant-overnight-bot-wbjzfd.
Standing rules for every routine: paper only, never place a real order, never call a geo-blocked
host or look for mirrors, real data only with source and timestamp, label every estimate, commit and
push at the end. Keep usage modest: do the work directly, spawn a sub-agent only when a step truly
needs one, and stop early if nothing changed. If the repo or bot state is missing (new container),
run `git pull`, then `bot/run.sh start --restore`.

- watchdog.md   every 2 hours
- radar.md      nightly 21:00 owner time
- lab.md        nightly 23:00
- forge.md      nightly 03:00
- brief.md      daily 07:30
- weekly.md     Sunday
