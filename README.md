# kullamagi-scan

Nightly Kullamägi continuation-breakout scan over the whole US equity market,
published as a static page to GitHub Pages.

**→ https://epinay197.github.io/kullamagi-scan/**

## What it does

Runs after the US close, ranks the eligible universe, finds bases that have printed a
trigger bar, and publishes one page per session with, for each candidate:

- the **buy stop** (the trigger bar's high) and the **initial stop** (3-bar structural low)
- risk as a % of price and as a multiple of the stock's own ADR, against a 1.0× ceiling
- share count and cash at risk for a $10,000 account at 0.5% risk, 25% per-name cap
- a chart with the base shaded and both levels drawn
- a watchlist of intact bases whose trigger bar has not printed yet

## Read it as a watchlist, not a signal

The pattern was placebo-tested on two years of the whole US market and **did not beat a
matched control** (p = 0.88). None of 17 hypotheses survived false-discovery correction,
and the 10R+ tail the method depends on never appeared. The scan exists because the rules
are objective and worth watching, not because the edge is established.

- Method: [the playbook](https://claude.ai/code/artifact/b34345be-b35b-4337-aa06-34a0be26c806)
- Evidence: [the backtest](https://claude.ai/code/artifact/289410ac-2e27-49d3-a9e1-b44f0c069c11)

## Mechanics

`ICT_kullamagi_scan.py` is the wrapper: fetch → panel → scan → render → commit → push →
Pages dispatch → Discord. The scan engine lives in `Code\kullamagy_research\bt` so the
research and the routine never diverge.

Scheduled **09:00 LU Mon–Sat**. The data plan does not serve the *current* day at all
(today returns 403 while weekends return 200 with zero rows), so an evening same-day run
could only ever fetch the previous close — by which time today has already traded. A
morning run at 09:00 LU (03:00 ET) reads yesterday's close and publishes roughly six and a
half hours before the 15:30 LU US open.

Targeting walks back from yesterday until the API actually returns rows, so holidays,
weekends and any provider write lag are handled without calendar arithmetic. If the
session it lands on is already published the run is a clean no-op, which makes every run
idempotent and makes the extra days free retries.

The task runs with an **Interactive** logon, which matches all 51 existing ICT_/MOTC_/FIN_
tasks on this machine and is sufficient because autologon is enabled (`AutoAdminLogon=1`)
— the session always exists, so there is nothing for an S4U principal to fix.
`WakeToRun` is on and battery conditions are off.

Failure is silent by design: nothing is published, the reason goes to
`ICT_kullamagi_scan_log.txt` and `needs_attention.log`. Zero candidates is not a failure —
it publishes, because that is information.
