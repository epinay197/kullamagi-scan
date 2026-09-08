"""Nightly Kullamagi scan: fetch, scan, render, publish to GitHub Pages, ping Discord.

Runs 09:00 LU Mon-Sat. Not after the close: the data plan returns 403 for the CURRENT
day, so the earliest a session can be scanned is the following morning. The fetch still
retries until the ticker count looks complete rather than publishing a partial universe.

On a day with no new session (weekend, holiday) the run is a deliberate no-op, and it
posts a one-line heartbeat so that silence from this routine always means broken.

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
HEARTBEAT_STATE = REPO / ".last_heartbeat"   # one skip-day ping per day, not per run

# sizing yardstick now lives in the engine's config so page and wrapper agree
PAGES_URL = "https://epinay197.github.io/kullamagi-scan/"

# A complete US session lists roughly 12k common-stock symbols. Well below that and
# the aggregates are still being written, so the universe would be wrong.
MIN_TICKERS = 8_000
FRESH_WAIT = 150
FRESH_TRIES_PARTIAL = 12  # data is clearly being written -> wait up to 30 min
FRESH_TRIES_EMPTY = 4     # nothing at all -> probably a holiday, give up after 10 min

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
        # Discord sits behind Cloudflare, which rejects Python-urllib's default
        # user agent with error 1010. Without this header every post 403s silently.
        req = urllib.request.Request(
            url, data=json.dumps({"content": text[:1900]}).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "DiscordBot (https://github.com/epinay197/kullamagi-scan, 1.0)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = 200 <= r.status < 300
        log(f"discord posted={ok}")
        return ok
    except Exception as e:
        log(f"discord failed: {e}")
        return False


# ------------------------------------------------------------------ data
def target_session(published):
    """The most recent session that actually has data, walking back from yesterday.

    The data plan does not serve the CURRENT day at all - today returns 403 while
    weekends return 200 with zero rows - so calendar arithmetic is not enough. Walking
    back until the API yields rows makes this correct through holidays, weekends and
    whatever the provider's write lag turns out to be.

    Returns (date, n_tickers, skip). skip is None when there is work to do, otherwise a
    dict saying why this run is a no-op: kind "current" is benign (nothing new to scan),
    kind "nodata" means the feed itself is not answering and is a fault.
    """
    import massive as M

    if "--session" in sys.argv:
        d = dt.date.fromisoformat(sys.argv[sys.argv.index("--session") + 1])
        return d, -1, None                             # caller fetches it explicitly

    day = dt.date.today() - dt.timedelta(days=1)
    for _ in range(8):                                 # covers a long holiday weekend
        try:
            j = M.grouped_daily(day.isoformat())
            n = len(j.get("results") or [])
            if n >= MIN_TICKERS:
                log(f"most recent session with data: {day} ({day:%a}) - {n} tickers")
                if day.isoformat() in published:
                    import calendar_us as CAL
                    gap = CAL.gap_note(day, dt.date.today())
                    log(f"{day} already published - nothing to do"
                        + (f" (market shut since: {gap})" if gap else ""))
                    return None, 0, {"kind": "current", "session": day, "gap": gap}
                return day, n, None
            log(f"{day} ({day:%a}): {n} rows - not a session, stepping back")
        except M.NotAuthorized:
            log(f"{day} ({day:%a}): 403 - outside the served window, stepping back")
        except Exception as e:
            log(f"{day}: {type(e).__name__} {e} - stepping back")
        day -= dt.timedelta(days=1)
    return None, 0, {"kind": "nodata"}


def already_published():
    return {p.stem.replace("scan_", "") for p in DOCS.glob("scan_*.html")}


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
    saw_partial = False
    attempt = 0
    while True:
        attempt += 1
        limit = FRESH_TRIES_PARTIAL if saw_partial else FRESH_TRIES_EMPTY
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
            log(f"attempt {attempt}/{limit}: no results (holiday, or not written yet)")
        else:
            df = pd.DataFrame(res).rename(columns=cols)
            df = df[[c for c in cols.values() if c in df.columns]].copy()
            df["date"] = day.isoformat()
            df = df[df["ticker"].str.fullmatch(r"[A-Z]{1,5}")]
            if len(df) >= MIN_TICKERS:
                df.to_parquet(dest, index=False)
                log(f"{day} settled with {len(df)} tickers on attempt {attempt}")
                return len(df)
            saw_partial = True
            log(f"attempt {attempt}: only {len(df)} tickers, below {MIN_TICKERS} - waiting")
        if attempt >= limit:
            log(f"gave up after {attempt} attempts "
                f"({'partial data never completed' if saw_partial else 'no data at all'})")
            return 0
        time.sleep(FRESH_WAIT)


# ------------------------------------------------------------------ publish
def write_index(newest_name, asof, n_cand, regime_on, entry_session=None, tickers=None):
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

    # Sidecar so a skip-day heartbeat can report the standing plan without rebuilding
    # the panel. On a no-op there is no new data to scan - only news to relay.
    (DOCS / "latest.json").write_text(json.dumps({
        "asof": str(asof), "page": newest_name, "candidates": int(n_cand),
        "regime_on": bool(regime_on), "entry_session": str(entry_session or ""),
        "tickers": list(tickers or []),
    }, indent=2), encoding="utf-8")


def read_latest():
    """The last published scan's summary, or None if it predates the sidecar."""
    try:
        return json.loads((DOCS / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def next_scan_note():
    """The task fires 09:00 LU Monday to Saturday, so the next run is tomorrow unless
    tomorrow is a Sunday."""
    d = dt.date.today() + dt.timedelta(days=1)
    while d.weekday() == 6:
        d += dt.timedelta(days=1)
    return f"{d:%a %d %b} 09:00 LU"


def heartbeat_due():
    """One heartbeat per calendar day. The task fires once, but manual reruns should not
    spam the channel."""
    try:
        return (HEARTBEAT_STATE.read_text(encoding="utf-8").strip()
                != dt.date.today().isoformat())
    except Exception:
        return True


def mark_heartbeat():
    try:
        HEARTBEAT_STATE.write_text(dt.date.today().isoformat(), encoding="utf-8")
    except Exception as e:
        log(f"could not record heartbeat state: {e}")


def skip_heartbeat(skip):
    """One line on a skip day.

    The point is not "the scan ran". It is that the plan already on the page is aimed at
    a session that may well be TODAY, and whether the regime says it is actionable.
    """
    day, gap = skip["session"], skip.get("gap")
    line = f"**Kullamagi scan** - no new session since {day:%a %d %b}"
    if gap:
        line += f", market shut for {gap}"
    line += ". Page unchanged."

    lat = read_latest()
    if lat:
        n = int(lat.get("candidates") or 0)
        ent, tk = lat.get("entry_session") or "", lat.get("tickers") or []
        state = ("LONG BOOK OPEN" if lat.get("regime_on")
                 else "REGIME OFF - not actionable")
        line += f" Standing plan: {n} candidate{'' if n == 1 else 's'}"
        if tk:
            line += " [" + ", ".join(tk[:8]) + "]"
        if ent:
            today = ent[:10] == dt.date.today().isoformat()
            line += f" for the {ent} session{' (today)' if today else ''}"
        line += f" - {state}."
    line += f" Next scan {next_scan_note()}. <{PAGES_URL}>"
    post_discord(line)


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
    # Pages serves docs/ straight off main, so the push itself triggers the rebuild -
    # there is no workflow to dispatch and no workflow token scope needed.
    return True


# ------------------------------------------------------------------ main
def main():
    log("=== run start ===")
    DOCS.mkdir(parents=True, exist_ok=True)
    published = already_published()
    day, n, skip = target_session(published)
    if day is None:
        if skip and skip["kind"] == "current":
            log("nothing to publish - already current")
            if heartbeat_due():
                skip_heartbeat(skip)
                mark_heartbeat()
            else:
                log("heartbeat already sent today - staying quiet")
            return 0
        # Eight days of walk-back with nothing served is not a shut market, it is a
        # broken feed. Say so loudly rather than exiting clean.
        log("ABORT: no served session found in the last 8 days")
        attention("no served session found in 8 days - check the data entitlement")
        post_discord("**Kullamagi scan** - FAULT: no session served in the last 8 "
                     "days. That is the feed or the entitlement, not the market. "
                     f"Next scan {next_scan_note()}.")
        return 3
    log(f"target session {day} ({day:%a})")

    if fetch_until_settled(day) == 0:
        log("ABORT: session never settled - publishing nothing")
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

    s = S.run(panel, asof=have)
    name = f"scan_{have.date()}.html"
    (DOCS / name).write_text(
        R.page(s, results_url="https://claude.ai/code/artifact/289410ac-2e27-49d3-a9e1-b44f0c069c11",
               playbook_url="https://claude.ai/code/artifact/b34345be-b35b-4337-aa06-34a0be26c806"),
        encoding="utf-8")
    write_index(name, have.date(), len(s["candidates"]), s["regime_on"],
                entry_session=s.get("entry_date"),
                tickers=[c["ticker"] for c in s["candidates"]])
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
