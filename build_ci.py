#!/usr/bin/env python3
"""
CI dashboard generator (runs in GitHub Actions).
Auto-discovers the authenticated user's repos via the GitHub API, clones each,
computes activity metrics from git history, and writes a self-contained
output/dashboard.html (+ output/data.csv). Delivery (email / private repo push)
is handled by the workflow, not this script.

The HTML is e-mail-safe: no JavaScript, no <svg>, no base64 images. Charts are
plain inline-styled / bgcolor HTML tables so they render inside Gmail (which
strips scripts, data: images and inline SVG). Table rows are pre-rendered too.

What it shows:
  * Work patterns (last 12 months): an auto-written Insights block, plus a
    weekday x hour-of-day "punch card" (when you actually commit).
  * Last 30 days: cards, a timeline, and commits-per-app.
  * Last 12 months: cards, a per-repo weekly-activity heatmap (so you see each
    repo's whole rhythm, not just first/last), commits-per-app, and a table.

All commit times use each commit's own local timezone (git's recorded offset),
i.e. when you were actually working. Counts in cards/charts are windowed; the
repo table keeps lifetime totals so it matches data.csv.

Privacy: prints ONLY aggregate counts to stdout — never repo names — because
public-repo Actions logs are world-readable.

Env:
  GH_TOKEN   (required)  fine-grained PAT with Contents:read + Metadata:read
  GH_AFFIL   (optional)  affiliation filter, default "owner"
  EXTRA_REPOS(optional)  comma-separated owner/name to also include
  EXCLUDE_REPOS(optional) comma-separated owner/name to skip
"""
import os, sys, json, subprocess, tempfile, shutil, csv, html, urllib.request
from datetime import datetime, timedelta, date

API = "https://api.github.com"
TOKEN = os.environ.get("GH_TOKEN", "").strip()
AFFIL = os.environ.get("GH_AFFIL", "owner").strip()
EXTRA = [r.strip() for r in os.environ.get("EXTRA_REPOS", "").split(",") if r.strip()]
EXCLUDE = set(r.strip().lower() for r in os.environ.get("EXCLUDE_REPOS", "").split(",") if r.strip())
VENDOR_OWNERS = {"juce-framework","liebharc","taigrr","kushalshah0","bastibe"}
OUT = os.path.join(os.getcwd(), "output")
os.makedirs(OUT, exist_ok=True)

def api(path):
    req = urllib.request.Request(API+path, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "app-dashboard-ci"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def list_repos():
    out, page = [], 1
    while True:
        chunk = api(f"/user/repos?per_page=100&page={page}&affiliation={AFFIL}&sort=created")
        if not chunk: break
        out += chunk; page += 1
        if len(chunk) < 100: break
    repos = []
    for r in out:
        if r.get("fork") or r.get("archived"): continue
        full = r["full_name"]
        if full.lower() in EXCLUDE: continue
        if full.split("/")[0].lower() in VENDOR_OWNERS: continue
        repos.append(full)
    for e in EXTRA:
        if e not in repos and e.lower() not in EXCLUDE: repos.append(e)
    return repos

def run(args, cwd, timeout=300):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""

def clone(full, dest):
    url = f"https://x-access-token:{TOKEN}@github.com/{full}.git"
    # full history (needed for first-commit + churn); no submodules (skips vendored libs)
    subprocess.run(["git","clone","--quiet",url,dest], check=True,
                   capture_output=True, text=True, timeout=600)

def metrics(full, repo):
    g = lambda *a: run(["git",*a], repo)
    count = g("rev-list","--all","--count").strip()
    # one pass: day | iso-week | dow(1=Mon..7=Sun) | hour(00-23), in each commit's own tz
    events = []          # list of (day 'YYYY-MM-DD', isoweek '%G-%V', dow int, hour int)
    days = set()
    for line in g("log","--all","--date=format:%Y-%m-%d|%G-%V|%u|%H","--format=%ad").splitlines():
        line = line.strip()
        if not line: continue
        p = line.split("|")
        if len(p) != 4: continue
        day, wk, dow, hr = p
        if not (dow.isdigit() and hr.isdigit() and len(day) == 10): continue
        events.append((day, wk, int(dow), int(hr))); days.add(day)
    ins=dele=0
    ns = run(["git","-c","diff.renames=false","log","--no-renames","--numstat","--format=","HEAD"], repo, timeout=300)
    for line in ns.splitlines():
        pp=line.split("\t")
        if len(pp)>=2 and pp[0].isdigit() and pp[1].isdigit():
            ins+=int(pp[0]); dele+=int(pp[1])
    return {"name": full.split("/")[1], "full": full,
            "commits": int(count) if count.isdigit() else 0,
            "first": min(days) if days else "", "last": max(days) if days else "",
            "active_days": len(days), "insertions": ins, "deletions": dele,
            "churn": ins+dele, "events": events}

# --------------------------------------------------------------------------
#  E-mail-safe HTML rendering (no JS, no base64, no SVG; inline styles + bgcolor)
# --------------------------------------------------------------------------
NAVY="#1f3864"; TEAL="#2c7fb8"; INK="#1a2233"; MUTE="#667"; LINE="#e7eaf1"; EMPTY="#eef1f6"
RAMP=["#cfe3f3","#9cc6e6","#5fa3d6","#2c7fb8","#1f3864"]   # light -> dark
AXIS=80.0
DOW=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]

def esc(s): return html.escape(str(s))
def fmt(n): return f"{n:,}"
def d(s):   return datetime.strptime(s, "%Y-%m-%d")
def week_monday(w): return datetime.strptime(w+"-1", "%G-%V-%u")

def weeks_in(start, end):
    dd = start - timedelta(days=start.weekday())
    out = []
    while dd <= end:
        out.append(dd.strftime("%G-%V")); dd += timedelta(days=7)
    return out

def shade(v, mx):
    if v <= 0 or mx <= 0: return EMPTY
    r = (v / mx) ** 0.6
    i = min(len(RAMP)-1, max(0, int(round(r*(len(RAMP)-1)))))
    return RAMP[i]

def card(value, label, sub=""):
    subhtml = f'<div style="font-size:11px;color:{MUTE};margin-top:2px">{esc(sub)}</div>' if sub else ""
    return ('<td style="background:#ffffff;border:1px solid '+LINE+';border-radius:10px;padding:12px 14px;vertical-align:top">'
            f'<div style="font-size:24px;font-weight:700;color:{NAVY};line-height:1.1">{esc(value)}</div>'
            f'<div style="font-size:11px;color:{MUTE};text-transform:uppercase;letter-spacing:.04em;margin-top:4px">{esc(label)}</div>'
            f'{subhtml}</td>')

def panel(title, sub, inner):
    subhtml = f' <span style="font-weight:400;color:{MUTE};font-size:12px">&middot; {esc(sub)}</span>' if sub else ""
    return ('<div style="background:#ffffff;border:1px solid '+LINE+';border-radius:10px;padding:14px 18px;margin:14px 0">'
            f'<div style="font-size:14px;font-weight:600;color:{INK};margin-bottom:10px">{esc(title)}{subhtml}</div>'
            f'{inner}</div>')

def legend():
    sw = "".join(f'<td bgcolor="{c}" style="width:16px;height:12px"></td>' for c in RAMP)
    return ('<table role="presentation" cellpadding="0" cellspacing="2" border="0" style="border-collapse:separate;margin-top:8px">'
            f'<tr><td style="font-size:10px;color:{MUTE};padding-right:4px">less</td>{sw}'
            f'<td style="font-size:10px;color:{MUTE};padding-left:4px">more</td></tr></table>')

def hbar_rows(items, color):
    if not items:
        return '<tr><td style="padding:10px 0;color:#889;font-size:13px">No activity in this window.</td></tr>'
    mx = max(v for _, v in items) or 1
    out = []
    for label, v in items:
        pct  = max(v / mx * AXIS, 1.2); rest = max(100 - pct, 0.1)
        out.append('<tr>'
            f'<td style="width:160px;font-size:13px;color:{INK};padding:5px 10px 5px 0;text-align:left;vertical-align:middle;white-space:nowrap">{esc(label)}</td>'
            '<td style="padding:5px 0;vertical-align:middle">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse"><tr>'
            f'<td width="{pct:.2f}%" style="width:{pct:.2f}%"><div style="background:{color};height:16px;border-radius:3px;font-size:0;line-height:16px">&nbsp;</div></td>'
            f'<td width="{rest:.2f}%" style="padding-left:8px;font-size:12px;color:{MUTE};white-space:nowrap;text-align:left">{fmt(v)}</td>'
            '</tr></table></td></tr>')
    return "".join(out)

def gantt_rows(rows, win_start, win_end, color):
    if not rows:
        return '<tr><td style="padding:10px 0;color:#889;font-size:13px">No activity in this window.</td></tr>'
    span = (win_end - win_start).days or 1
    out = []
    for label, fdt, ldt, num in rows:
        f = max(fdt, win_start); l = min(ldt, win_end)
        off = max((f - win_start).days, 0) / span * AXIS
        dur = max((l - f).days / span * AXIS, 1.2)
        if off + dur > AXIS: dur = max(AXIS - off, 1.2)
        rest = max(100 - off - dur, 0.1)
        out.append('<tr>'
            f'<td style="width:160px;font-size:13px;color:{INK};padding:5px 10px 5px 0;text-align:left;vertical-align:middle;white-space:nowrap">{esc(label)}</td>'
            '<td style="padding:5px 0;vertical-align:middle">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse"><tr>'
            f'<td width="{off:.2f}%" style="width:{off:.2f}%;font-size:0">&nbsp;</td>'
            f'<td width="{dur:.2f}%" style="width:{dur:.2f}%"><div style="background:{color};height:16px;border-radius:3px;font-size:0;line-height:16px">&nbsp;</div></td>'
            f'<td width="{rest:.2f}%" style="padding-left:8px;font-size:12px;color:{MUTE};white-space:nowrap;text-align:left">{fmt(num)}</td>'
            '</tr></table></td></tr>')
    return "".join(out)

def repo_week_heatmap(rows, weekset):
    """rows: list of (name, {week:count}). One row per repo, one cell per ISO week."""
    if not rows:
        return '<div style="padding:10px 0;color:#889;font-size:13px">No activity in this window.</div>'
    mx = max((c for _, wk in rows for c in wk.values()), default=0)
    head = ['<td style="width:150px"></td>']
    prev = None
    for w in weekset:
        mon = week_monday(w); lab = ""
        if (mon.year, mon.month) != prev:
            lab = mon.strftime("%b"); prev = (mon.year, mon.month)
        head.append(f'<td style="font-size:9px;color:{MUTE};text-align:left;white-space:nowrap">{lab}</td>')
    body = ['<tr style="height:14px">' + "".join(head) + '</tr>']
    for name, wk in rows:
        cells = [f'<td style="width:150px;font-size:12px;color:{INK};white-space:nowrap;padding-right:6px">{esc(name)}</td>']
        for w in weekset:
            c = wk.get(w, 0)
            t = f' title="{c}"' if c else ""
            cells.append(f'<td bgcolor="{shade(c, mx)}"{t}></td>')
        body.append('<tr style="height:13px">' + "".join(cells) + '</tr>')
    return ('<table role="presentation" cellpadding="0" cellspacing="1" border="0" bgcolor="#ffffff" '
            'style="width:100%;border-collapse:separate;table-layout:fixed">'
            + "".join(body) + '</table>'
            f'<div style="font-size:11px;color:{MUTE};margin-top:2px">Each cell = one ISO week &middot; peak {mx} commits/week</div>'
            + legend())

def punch_card(punch):
    """punch: dict {(dow1-7, hour0-23): count}. 7 rows x 24 cols heatmap."""
    mx = max(punch.values(), default=0)
    if mx <= 0:
        return '<div style="padding:10px 0;color:#889;font-size:13px">No commit-time data in this window.</div>'
    head = ['<td style="width:34px"></td>']
    for h in range(24):
        lab = str(h) if h % 3 == 0 else ""
        head.append(f'<td style="font-size:9px;color:{MUTE};text-align:center">{lab}</td>')
    body = ['<tr style="height:13px">' + "".join(head) + '</tr>']
    for di in range(1, 8):
        cells = [f'<td style="width:34px;font-size:11px;color:{INK};padding-right:4px;text-align:right">{DOW[di-1]}</td>']
        for h in range(24):
            c = punch.get((di, h), 0)
            t = f' title="{DOW[di-1]} {h:02d}:00 - {c}"' if c else ""
            cells.append(f'<td bgcolor="{shade(c, mx)}"{t}></td>')
        body.append('<tr style="height:15px">' + "".join(cells) + '</tr>')
    return ('<table role="presentation" cellpadding="0" cellspacing="1" border="0" bgcolor="#ffffff" '
            'style="width:100%;border-collapse:separate;table-layout:fixed">'
            + "".join(body) + '</table>'
            f'<div style="font-size:11px;color:{MUTE};margin-top:2px">Hour of day (0&ndash;23) across the week &middot; peak {mx} in one hour-slot &middot; commit-local time</div>'
            + legend())

def chip(big, label):
    return ('<td style="background:#ffffff;border:1px solid '+LINE+';border-radius:10px;padding:11px 13px;vertical-align:top">'
            f'<div style="font-size:18px;font-weight:700;color:{NAVY};line-height:1.15">{esc(big)}</div>'
            f'<div style="font-size:11px;color:{MUTE};margin-top:3px">{esc(label)}</div></td>')

def longest_streak(day_strs):
    ds = sorted(set(date.fromisoformat(x) for x in day_strs))
    best = cur = 0; prev = None
    for x in ds:
        cur = cur + 1 if (prev and (x - prev).days == 1) else 1
        best = max(best, cur); prev = x
    return best

def insights_block(events, repos_window):
    """events: aggregated (day,wk,dow,hr) in window. repos_window: list of (name, commits, churn)."""
    if not events:
        return '<div style="color:#889;font-size:13px">No commits in this window.</div>'
    n = len(events)
    by_hour = {}; by_dow = {}; by_day = {}; by_week = {}
    for day, wk, dw, hr in events:
        by_hour[hr] = by_hour.get(hr, 0) + 1
        by_dow[dw] = by_dow.get(dw, 0) + 1
        by_day[day] = by_day.get(day, 0) + 1
        by_week[wk] = by_week.get(wk, 0) + 1
    peak_hr = max(by_hour, key=by_hour.get)
    peak_dw = max(by_dow, key=by_dow.get)
    night = sum(c for h, c in by_hour.items() if h >= 18 or h < 6)
    weekend = sum(c for dw, c in by_dow.items() if dw >= 6)
    streak = longest_streak(by_day.keys())
    busiest_week = max(by_week.values(), default=0)
    top_repo = max(repos_window, key=lambda r: r[1]) if repos_window else None
    top_churn = max(repos_window, key=lambda r: r[2]) if repos_window else None
    chips = [
        chip(f"{peak_hr:02d}:00", "Peak hour"),
        chip(DOW[peak_dw-1], "Most active day"),
        chip(f"{round(night/n*100)}%", "Commits 6pm&ndash;6am (night owl)"),
        chip(f"{round(weekend/n*100)}%", "Weekend commits"),
        chip(f"{streak} days", "Longest daily streak"),
        chip(f"{busiest_week}", "Busiest week (commits)"),
    ]
    if top_repo: chips.append(chip(top_repo[0], f"Top repo &middot; {fmt(top_repo[1])} commits"))
    if top_churn and (not top_repo or top_churn[0] != top_repo[0]):
        chips.append(chip(top_churn[0], f"Most churn &middot; {fmt(top_churn[2])} lines"))
    rows = ""
    for i in range(0, len(chips), 3):
        rows += ('<tr>' + "".join(chips[i:i+3])
                 + "".join('<td></td>' for _ in range(3 - len(chips[i:i+3]))) + '</tr>')
    return ('<table role="presentation" cellpadding="0" cellspacing="8" border="0" '
            'style="width:100%;border-collapse:separate;table-layout:fixed">' + rows + '</table>')

def table_block(rows):
    th = ('<th style="padding:7px 10px;border-bottom:2px solid '+LINE+';font-size:11px;color:'+MUTE+';'
          'text-transform:uppercase;letter-spacing:.03em;{align}">{t}</th>')
    head = (th.format(align="text-align:left", t="App") + th.format(align="text-align:right", t="Start")
            + th.format(align="text-align:right", t="Latest") + th.format(align="text-align:right", t="Commits")
            + th.format(align="text-align:right", t="Active days") + th.format(align="text-align:right", t="Lines changed")
            + th.format(align="text-align:left", t="Repo"))
    body = []
    for m in rows:
        url = "https://github.com/" + m["full"]
        cell = 'padding:7px 10px;border-bottom:1px solid '+LINE+';font-size:13px'
        body.append('<tr>'
            f'<td style="{cell};text-align:left">{esc(m["name"])}</td>'
            f'<td style="{cell};text-align:right;color:{MUTE}">{esc(m["first"])}</td>'
            f'<td style="{cell};text-align:right;color:{MUTE}">{esc(m["last"])}</td>'
            f'<td style="{cell};text-align:right">{fmt(m["commits"])}</td>'
            f'<td style="{cell};text-align:right">{fmt(m["active_days"])}</td>'
            f'<td style="{cell};text-align:right">{fmt(m["churn"])}</td>'
            f'<td style="{cell};text-align:left"><a href="{esc(url)}" style="color:{TEAL};text-decoration:none">{esc(m["full"])}</a></td>'
            '</tr>')
    return ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse">'
            f'<tr>{head}</tr>{"".join(body)}</table>')

def events_in(m, ws):
    return [e for e in m["events"] if e[0] >= ws]

def work_patterns(repos, today):
    ws = (today - timedelta(days=365)).date().isoformat()
    agg = []
    for m in repos: agg += events_in(m, ws)
    punch = {}
    for _, _, dw, hr in agg: punch[(dw, hr)] = punch.get((dw, hr), 0) + 1
    repos_window = [(m["name"], len(events_in(m, ws)),
                     int(m["churn"] * len(events_in(m, ws)) / max(m["commits"], 1)))
                    for m in repos if events_in(m, ws)]
    return "".join([
        f'<h2 style="margin:30px 0 2px;font-size:19px;color:{NAVY}">Work patterns</h2>',
        f'<div style="font-size:12px;color:{MUTE};margin-bottom:12px">Based on the last 12 months &middot; times are each commit&rsquo;s local timezone.</div>',
        panel("Insights", "last 12 months", insights_block(agg, repos_window)),
        panel("When you commit", "weekday &times; hour-of-day", punch_card(punch))])

def section(title, win_start, win_end, color, repos, with_heatmap):
    weekset = weeks_in(win_start, win_end)
    wkset = set(weekset)
    ws = win_start.date().isoformat()
    active = []
    for m in repos:
        ev = events_in(m, ws)
        if ev: active.append((m, len(ev), ev))
    totals_by_week = {}
    for _, _, ev in active:
        for _, wk, _, _ in ev:
            if wk in wkset: totals_by_week[wk] = totals_by_week.get(wk, 0) + 1
    n = len(active)
    commits = sum(c for _, c, _ in active)
    active_weeks = sum(1 for v in totals_by_week.values() if v > 0)
    busiest = max(totals_by_week.values(), default=0)

    cards = ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
             'style="width:100%;border-collapse:separate;border-spacing:10px 0;margin:0 -10px 4px"><tr>'
             + card(fmt(n), "Repos active") + card(fmt(commits), "Commits")
             + card(fmt(active_weeks), "Active weeks") + card(fmt(busiest), "Busiest week")
             + '</tr></table>')

    by_first = sorted(active, key=lambda t: (t[0]["first"], t[0]["name"]))
    gantt_data = [(m["name"], d(m["first"]), d(m["last"]), c) for m, c, _ in by_first]
    by_commits = sorted(active, key=lambda t: -t[1])
    bar_data = [(m["name"], c) for m, c, _ in by_commits]

    s = [f'<h2 style="margin:34px 0 2px;font-size:19px;color:{NAVY}">{esc(title)}</h2>',
         f'<div style="font-size:12px;color:{MUTE};margin-bottom:12px">'
         f'Window {win_start.date().isoformat()} &rarr; {win_end.date().isoformat()}. '
         'Commit counts here are for this window; the table shows lifetime totals.</div>',
         cards]
    if with_heatmap:
        hm_rows = []
        for m, _, ev in by_commits:
            wk = {}
            for _, w, _, _ in ev:
                if w in wkset: wk[w] = wk.get(w, 0) + 1
            hm_rows.append((m["name"], wk))
        s.append(panel("Weekly activity by repo", "each repo&rsquo;s rhythm over the window",
                       repo_week_heatmap(hm_rows, weekset)))
    else:
        s.append(panel("Timeline", "bar = first&rarr;latest commit (clamped to window), number = commits",
                       '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse">'
                       + gantt_rows(gantt_data, win_start, win_end, color) + '</table>'))
    s.append(panel("Commits per app", "in this window",
                   '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse">'
                   + hbar_rows(bar_data, color) + '</table>'))
    if with_heatmap:
        s.append(panel("Repos", "lifetime totals", table_block([m for m, _, _ in by_commits])))
    return "".join(s)

def render_html(repos, now_str, today=None):
    if today is None: today = datetime.utcnow()
    wp   = work_patterns(repos, today)
    s30  = section("Last 30 days",  today - timedelta(days=30),  today, NAVY, repos, with_heatmap=False)
    s365 = section("Last 12 months", today - timedelta(days=365), today, TEAL, repos, with_heatmap=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>App Portfolio Dashboard</title></head>
<body style="margin:0;padding:0;background:#f4f6fb">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:#f4f6fb"><tr><td align="center" style="padding:0">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:720px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK}"><tr><td style="padding:0 16px 40px">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:{NAVY};border-radius:0 0 12px 12px;margin-bottom:6px"><tr><td style="padding:20px 22px">
<div style="color:#ffffff;font-size:21px;font-weight:700">App Portfolio Dashboard</div>
<div style="color:#c7d2e8;font-size:12px;margin-top:4px">Last updated {esc(now_str)} &middot; built in GitHub Actions from your repos</div>
</td></tr></table>
<div style="font-size:13px;color:{MUTE};margin:14px 2px 0;line-height:1.5">Work patterns first, then the <b>last 30 days</b> and the <b>last 12 months</b>. Cards, charts and timeline numbers are windowed; the repo table lists lifetime totals (matching data.csv).</div>
{wp}
{s30}
{s365}
<div style="color:#889;font-size:12px;margin-top:30px;line-height:1.6;border-top:1px solid {LINE};padding-top:14px">Auto-generated in GitHub Actions. Forks, archived repos and vendored libraries are excluded; new repos are picked up automatically. Charts are plain HTML so they render inside email. Commit times use each commit&rsquo;s recorded local timezone.</div>
</td></tr></table>
</td></tr></table>
</body></html>"""

def build():
    if not TOKEN:
        print("ERROR: GH_TOKEN not set"); sys.exit(1)
    full_list = list_repos()
    repos=[]
    for full in full_list:
        tmp = tempfile.mkdtemp()
        try:
            clone(full, tmp)
            m = metrics(full, tmp)
            if m["commits"]>0 and m["first"]:
                repos.append(m)
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if not repos:
        print("ERROR: no repos with history found"); sys.exit(1)
    repos.sort(key=lambda m: m["first"])
    now=datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    # csv (lifetime totals — unchanged schema)
    with open(os.path.join(OUT,"data.csv"),"w",newline="") as f:
        w=csv.writer(f); w.writerow(["app","first_commit","last_commit","commits","active_days","insertions","deletions","churn","repo"])
        for m in repos: w.writerow([m["name"],m["first"],m["last"],m["commits"],m["active_days"],m["insertions"],m["deletions"],m["churn"],m["full"]])
    # html (e-mail-safe; rendered inline by the workflow's html_body)
    htmlout = render_html(repos, now)
    with open(os.path.join(OUT,"dashboard.html"),"w",encoding="utf-8") as f: f.write(htmlout)
    # privacy-safe log: counts only, no names
    tot_c=sum(m["commits"] for m in repos); tot_a=sum(m["active_days"] for m in repos); tot_ch=sum(m["churn"] for m in repos)
    print(json.dumps({"repos":len(repos),"commits":tot_c,"active_days":tot_a,"churn":tot_ch,"updated":now}))

if __name__=="__main__":
    build()
