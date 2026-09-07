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

Scheduled 22:15 LU Mon–Sat (16:15 ET). Because that is only fifteen minutes after the
close, the fetch retries for up to thirty minutes until the session's ticker count looks
complete, rather than publishing a partial universe. Saturday targets Friday and normally
finds it already published — it is Friday's safety net.

Failure is silent by design: nothing is published, the reason goes to
`ICT_kullamagi_scan_log.txt` and `needs_attention.log`. Zero candidates is not a failure —
it publishes, because that is information.
