"""Nightly Kullamagi scan: fetch, scan, render, publish to GitHub Pages, ping Discord.

Runs 22:15 LU Mon-Sat (= 16:15 ET, fifteen minutes after the US close). That is early
enough that the grouped daily aggregates may not have settled yet, so the fetch retries
until the session looks complete rather than publishing a partial universe.

The scan engine itself lives in Code\\kullamagy_research\\bt so the research and the
routine never fork. This file is only the wrapper: freshness, publish, alert.

Exit codes
  0  published, or already current (idempotent)
  2  data never settled - nothing published
  3  scan produced no usable as-of session
  4  git push failed - page did not update
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
DOCS = REPO / "docs"
ENGINE = Path(r"C:\Users\Anwender\Code\kullamagy_research\bt")
PY = r"C:\Users\Anwender\AppData\Local\Python\bin\python.exe"
LOG = REPO / "ICT_kullamagi_scan_log.txt"
NEEDS_ATTENTION = Path(r"C:\Users\Anwender\needs_attention.log")

EQUITY = 10_000.0
RISK_PCT = 0.5
PAGES_URL = "https://epinay197.github.io/kullamagi-scan/"

# A complete US session lists roughly 12k common-stock symbols. Well below that and
# the aggregates are still being written, so the universe would be wrong.
MIN_TICKERS = 8_000
FRESH_TRIES = 12          # 12 x 150s = 30 minutes of patience
FRESH_WAIT = 150

sys.path.insert(0, str(ENGINE))


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def attention(msg):
    """Their standing rule: no popups, write to needs_attention.log and the brief."""
    try:
        with open(NEEDS_ATTENTION, "a", encoding="utf-8") as fh:
            fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} [kullamagi-scan] {msg}\n")
    except Exception as e:
        log(f"could not write needs_attention: {e}")


def run(cmd, cwd=REPO, timeout=900):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, shell=False)


def post_discord(text):
    """Best-effort, same pattern as ICT_daily_badge: env var, then the shared file."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        for wf in (Path(r"C:\Users\Anwender\Code\nokepa\scripts\.discord_webhook.txt"),
                   Path(r"C:\Users\Anwender\Code\nokepa\data\discord_webhook.txt")):
            if wf.exists():
                url = wf.read_text(encoding="utf-8", errors="ignore").strip()
                break
    if not url.startswith("http"):
        log("discord: no webhook configured, skipping")
        return False
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"content": text[:1900]}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
        log(f"discord posted={ok}")
        return ok
    except Exception as e:
        log(f"discord failed: {e}")
        return False


# ------------------------------------------------------------------ data
def target_session():
    """The US session this run is meant to cover.

    Fired at 22:15 LU the same calendar day is the session that just closed. A Saturday
    run has no new session, so it targets Friday and will normally find it already
    published - which is the point: Saturday is Friday's safety net.
    """
    today = dt.date.today()
    if today.weekday() == 5:                       # Saturday
        return today - dt.timedelta(days=1)
    if today.weekday() == 6:                       # Sunday, shouldn't be scheduled
        return today - dt.timedelta(days=2)
    return today


def fetch_until_settled(day):
    """Pull one grouped-daily snapshot, retrying until the ticker count looks complete."""
    import config as C
    import massive as M
    import pandas as pd

    dest = C.DAILY / f"{day.isoformat()}.parquet"
    if dest.exists():
        n = len(pd.read_parquet(dest))
        if n >= MIN_TICKERS:
            log(f"{day} already cached with {n} tickers")
            return n
        log(f"{day} cached but only {n} tickers - refetching")

    cols = {"T": "ticker", "o": "open", "h": "high", "l": "low", "c": "close",
            "v": "volume", "n": "trades", "vw": "vwap"}
    for attempt in range(1, FRESH_TRIES + 1):
        try:
            j = M.grouped_daily(day.isoformat())
        except M.NotAuthorized:
            log(f"{day} NOT_AUTHORIZED - outside the plan's history window")
            return 0
        except Exception as e:
            log(f"attempt {attempt}: fetch error {e}")
            j = {}
        res = j.get("results") or []
        if not res:
            log(f"attempt {attempt}: no results yet (holiday, or not written)")
        else:
            df = pd.DataFrame(res).rename(columns=cols)
            df = df[[c for c in cols.values() if c in df.columns]].copy()
            df["date"] = day.isoformat()
            df = df[df["ticker"].str.fullmatch(r"[A-Z]{1,5}")]
            if len(df) >= MIN_TICKERS:
                df.to_parquet(dest, index=False)
                log(f"{day} settled with {len(df)} tickers on attempt {attempt}")
                return len(df)
            log(f"attempt {attempt}: only {len(df)} tickers, below {MIN_TICKERS} - waiting")
        if attempt < FRESH_TRIES:
            time.sleep(FRESH_WAIT)
    return 0


# ------------------------------------------------------------------ publish
def write_index(newest_name, asof, n_cand, regime_on):
    state = "LONG BOOK OPEN" if regime_on else "STAND ASIDE"
    (DOCS / "index.html").write_text(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url={newest_name}">
<title>Kullamagi scan</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
a{{color:#60a5fa}}</style></head><body>
<p>Redirecting to the <a href="{newest_name}">{asof} scan</a>
&mdash; {state}, {n_cand} candidate{"" if n_cand == 1 else "s"}&hellip;</p>
</body></html>""", encoding="utf-8")


def git_publish(asof, n_cand):
    run(["git", "add", "docs/"])
    st = run(["git", "status", "--porcelain", "docs/"])
    if not st.stdout.strip():
        log("nothing to commit - docs identical")
        return True
    c = run(["git", "commit", "-m", f"scan: {asof} ({n_cand} candidates)"])
    log(f"commit rc={c.returncode} {c.stdout.strip()[:120]}")
    p = run(["git", "push", "origin", "main"], timeout=300)
    if p.returncode != 0:
        log("push rejected - rebasing onto origin/main and retrying")
        run(["git", "fetch", "origin"], timeout=300)
        rb = run(["git", "rebase", "--autostash", "origin/main"], timeout=300)
        if rb.returncode != 0:
            run(["git", "rebase", "--abort"])
            log(f"rebase failed: {(rb.stdout + rb.stderr).strip()[:200]}")
            return False
        p = run(["git", "push", "origin", "main"], timeout=300)
    log(f"push rc={p.returncode} {(p.stdout + p.stderr).strip()[:160]}")
    if p.returncode != 0:
        return False
    d = run(["gh", "workflow", "run", "pages.yml"], timeout=180)
    log(f"pages dispatch rc={d.returncode} {(d.stdout + d.stderr).strip()[:160]}")
    return True


# ------------------------------------------------------------------ main
def main():
    log("=== run start ===")
    DOCS.mkdir(parents=True, exist_ok=True)
    day = target_session()
    log(f"target session {day} ({day:%a})")

    n = fetch_until_settled(day)
    if n == 0:
        log("ABORT: session never settled or was a holiday - publishing nothing")
        attention(f"scan {day}: data never settled, page not updated")
        return 2

    import build_panel as BP
    import config as C
    import pandas as pd
    import render_scan as R
    import scan as S

    BP.main()
    panel = pd.read_parquet(C.DATA / "panel.parquet")
    have = pd.Timestamp(pd.to_datetime(panel["date"]).max())
    if have.date() != day:
        log(f"ABORT: panel latest is {have.date()}, expected {day}")
        attention(f"scan {day}: panel latest is {have.date()}, page not updated")
        return 3

    s = S.run(panel, asof=have, equity=EQUITY, risk_pct=RISK_PCT)
    name = f"scan_{have.date()}.html"
    (DOCS / name).write_text(
        R.page(s, results_url="https://claude.ai/code/artifact/289410ac-2e27-49d3-a9e1-b44f0c069c11",
               playbook_url="https://claude.ai/code/artifact/b34345be-b35b-4337-aa06-34a0be26c806"),
        encoding="utf-8")
    write_index(name, have.date(), len(s["candidates"]), s["regime_on"])
    log(f"rendered {name}: {len(s['candidates'])} candidates, "
        f"{len(s['gated'])} gated, {len(s['watchlist'])} watchlist, "
        f"regime {'ON' if s['regime_on'] else 'OFF'}")

    if not git_publish(have.date(), len(s["candidates"])):
        attention(f"scan {have.date()}: git push failed, page did not update")
        return 4

    # ---- Discord: candidates only, with the regime state front and centre ----
    cands = s["candidates"]
    if cands:
        state = "LONG BOOK OPEN" if s["regime_on"] else "REGIME OFF - not actionable"
        head = (f"**Kullamagi scan {have.date()}** - {state}\n"
                f"{len(cands)} candidate{'' if len(cands) == 1 else 's'} "
                f"for the next session")
        lines = []
        for c in cands[:8]:
            lines.append(
                f"`{c['ticker']:<6}` buy **{c['pivot']:.2f}**  stop **{c['stop']:.2f}**  "
                f"({c['risk_pct']:.2f}% = {c['adr_mult']:.2f}x ADR)  "
                f"{c['shares']}sh / ${c['cash_risk']:.0f} risk")
        tail = (f"\n<{PAGES_URL}>\n"
                f"_Watchlist not a signal: this pattern did not beat a matched control "
                f"(p=0.88) in a 2-year placebo test._")
        post_discord(head + "\n" + "\n".join(lines) + tail)
    else:
        log("no candidates - no Discord ping (only candidates trigger a push)")

    log(f"=== run done: {PAGES_URL} ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"UNHANDLED: {type(e).__name__}: {e}")
        attention(f"scan crashed: {type(e).__name__}: {e}")
        raise
