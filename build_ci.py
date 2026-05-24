#!/usr/bin/env python3
"""
CI dashboard generator (runs in GitHub Actions).
Auto-discovers the authenticated user's repos via the GitHub API, clones each,
computes activity metrics from git history, and writes a self-contained
output/dashboard.html (+ output/data.csv). Delivery (email / private repo push)
is handled by the workflow, not this script.

The HTML is e-mail-safe: no JavaScript, no <svg>, no base64 images. Charts are
drawn with plain inline-styled HTML tables so they render inside Gmail (which
strips scripts, data: images and inline SVG). The table rows are pre-rendered
server-side for the same reason.

Two windows are shown: the last 30 days first, then the last 12 months. The
all-time view was dropped because the oldest repo (2014) stretched the axis so
far that recent activity was unreadable. Commit counts shown in the cards,
charts and timeline numbers are *windowed* (summed from the per-repo weekly
commit buckets); the repo table keeps lifetime totals so it matches data.csv.

Privacy: prints ONLY aggregate counts to stdout — never repo names — because
public-repo Actions logs are world-readable.

Env:
  GH_TOKEN   (required)  fine-grained PAT with Contents:read + Metadata:read
  GH_AFFIL   (optional)  affiliation filter, default "owner"
  EXTRA_REPOS(optional)  comma-separated owner/name to also include (e.g. collaborator repos)
  EXCLUDE_REPOS(optional) comma-separated owner/name to skip
"""
import os, sys, json, subprocess, tempfile, shutil, csv, html, urllib.request
from datetime import datetime, timedelta

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
    first = [l for l in g("log","--all","--reverse","--date=short","--format=%ad").splitlines() if l]
    last  = [l for l in g("log","--all","--date=short","--format=%ad").splitlines() if l]
    days  = sorted(set(l for l in g("log","--all","--date=short","--format=%ad").splitlines() if l))
    weekly = {}
    for w in g("log","--all","--date=format:%G-%V","--format=%ad").splitlines():
        if w: weekly[w] = weekly.get(w,0)+1
    ins=dele=0
    ns = run(["git","-c","diff.renames=false","log","--no-renames","--numstat","--format=","HEAD"], repo, timeout=300)
    for line in ns.splitlines():
        p=line.split("\t")
        if len(p)>=2 and p[0].isdigit() and p[1].isdigit():
            ins+=int(p[0]); dele+=int(p[1])
    return {"name": full.split("/")[1], "full": full,
            "commits": int(count) if count.isdigit() else 0,
            "first": first[0] if first else "", "last": last[0] if last else "",
            "active_days": len(days), "insertions": ins, "deletions": dele,
            "churn": ins+dele, "weekly": weekly}

# --------------------------------------------------------------------------
#  E-mail-safe HTML rendering (no JS, no base64, no SVG; inline styles only)
# --------------------------------------------------------------------------
NAVY="#1f3864"; TEAL="#2c7fb8"; INK="#1a2233"; MUTE="#667"; LINE="#e7eaf1"
AXIS=80.0  # % of a bar track used by bars; remainder reserved for the number label

def esc(s): return html.escape(str(s))
def fmt(n): return f"{n:,}"
def d(s):   return datetime.strptime(s, "%Y-%m-%d")
def week_monday(w): return datetime.strptime(w+"-1", "%G-%V-%u")  # "%G-%V" -> that week's Monday

def weeks_in(start, end):
    """Ordered list of ISO 'year-week' keys for every Monday in [start, end]."""
    dd = start - timedelta(days=start.weekday())   # back up to Monday
    out = []
    while dd <= end:
        out.append(dd.strftime("%G-%V")); dd += timedelta(days=7)
    return out

def win_commits(m, weekset):
    return sum(m["weekly"].get(w, 0) for w in weekset)

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

def hbar_rows(items, color):
    """items: list of (label, value). Horizontal bar, length proportional to value."""
    if not items:
        return '<tr><td style="padding:10px 0;color:#889;font-size:13px">No activity in this window.</td></tr>'
    mx = max(v for _, v in items) or 1
    out = []
    for label, v in items:
        pct  = max(v / mx * AXIS, 1.2)
        rest = max(100 - pct, 0.1)
        out.append('<tr>'
            f'<td style="width:160px;font-size:13px;color:{INK};padding:5px 10px 5px 0;text-align:left;vertical-align:middle;white-space:nowrap">{esc(label)}</td>'
            '<td style="padding:5px 0;vertical-align:middle">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse"><tr>'
            f'<td width="{pct:.2f}%" style="width:{pct:.2f}%"><div style="background:{color};height:16px;border-radius:3px;font-size:0;line-height:16px">&nbsp;</div></td>'
            f'<td width="{rest:.2f}%" style="padding-left:8px;font-size:12px;color:{MUTE};white-space:nowrap;text-align:left">{fmt(v)}</td>'
            '</tr></table></td></tr>')
    return "".join(out)

def gantt_rows(rows, win_start, win_end, color):
    """rows: list of (label, first_date, last_date, number). Bar = first->last clamped to window."""
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

def weekly_columns(weeks, totals, color, height=92):
    """Vertical bar chart: one column per ISO week, height proportional to commits.
    Email-safe via a single table row of bottom-aligned cells holding a colored div."""
    mx = max(totals) if totals else 0
    if mx <= 0:
        return '<div style="padding:10px 0;color:#889;font-size:13px">No commits in this window.</div>'
    show_nums = len(weeks) <= 8
    bars, labels = [], []
    prev_month = None
    for wk, t in zip(weeks, totals):
        h = int(round(t / mx * height)) if t > 0 else 0
        numtag = (f'<div style="font-size:10px;color:{MUTE};line-height:1;margin-bottom:2px">{t}</div>'
                  if (show_nums and t > 0) else "")
        bar = (f'{numtag}<div style="background:{color};height:{h}px;font-size:0;line-height:0;border-radius:2px 2px 0 0">&nbsp;</div>'
               if h > 0 else '<div style="height:2px;background:'+LINE+';font-size:0;line-height:0">&nbsp;</div>')
        bars.append(f'<td valign="bottom" style="vertical-align:bottom;padding:0 1px;text-align:center">{bar}</td>')
        mon = week_monday(wk)
        lab = ""
        if (mon.year, mon.month) != prev_month:
            lab = mon.strftime("%b"); prev_month = (mon.year, mon.month)
        labels.append(f'<td style="font-size:9px;color:{MUTE};text-align:left;padding-top:4px;white-space:nowrap">{lab}</td>')
    return ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;table-layout:fixed">'
            f'<tr style="height:{height}px">{"".join(bars)}</tr>'
            f'<tr>{"".join(labels)}</tr></table>'
            f'<div style="font-size:11px;color:{MUTE};margin-top:6px">Commits per ISO week &middot; peak {mx} in a week</div>')

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

def section(title, win_start, win_end, color, repos):
    weekset = weeks_in(win_start, win_end)
    wk_index = {w: i for i, w in enumerate(weekset)}
    active = []
    for m in repos:
        wc = win_commits(m, weekset)
        if wc > 0:
            active.append((m, wc))
    # window totals (genuinely windowed via weekly buckets)
    totals_by_week = [0] * len(weekset)
    for m, _ in active:
        for w, c in m["weekly"].items():
            if w in wk_index: totals_by_week[wk_index[w]] += c
    n = len(active)
    commits = sum(wc for _, wc in active)
    active_weeks = sum(1 for t in totals_by_week if t > 0)
    busiest = max(totals_by_week) if totals_by_week else 0

    cards = ('<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
             'style="width:100%;border-collapse:separate;border-spacing:10px 0;margin:0 -10px 4px"><tr>'
             + card(fmt(n), "Repos active") + card(fmt(commits), "Commits")
             + card(fmt(active_weeks), "Active weeks") + card(fmt(busiest), "Busiest week")
             + '</tr></table>')

    # timeline (chronological), commits-per-app (desc), both using windowed commit counts
    gantt = sorted(active, key=lambda t: (t[0]["first"], t[0]["name"]))
    gantt_data = [(m["name"], d(m["first"]), d(m["last"]), wc) for m, wc in gantt]
    bars = sorted(active, key=lambda t: -t[1])
    bar_data = [(m["name"], wc) for m, wc in bars]
    tbl = sorted(active, key=lambda t: -t[1])

    s = []
    s.append(f'<h2 style="margin:34px 0 2px;font-size:19px;color:{NAVY}">{esc(title)}</h2>')
    s.append(f'<div style="font-size:12px;color:{MUTE};margin-bottom:12px">'
             f'Window {win_start.date().isoformat()} &rarr; {win_end.date().isoformat()}. '
             'Commit counts below are for this window; the table shows lifetime totals.</div>')
    s.append(cards)
    s.append(panel("Timeline", "bar = first&rarr;latest commit (clamped to window), number = commits",
                   '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse">'
                   + gantt_rows(gantt_data, win_start, win_end, color) + '</table>'))
    s.append(panel("Weekly effort", "commits across all repos, per ISO week",
                   weekly_columns(weekset, totals_by_week, color)))
    s.append(panel("Commits per app", "in this window",
                   '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse">'
                   + hbar_rows(bar_data, color) + '</table>'))
    s.append(panel("Repos", "lifetime totals", table_block([m for m, _ in tbl])))
    return "".join(s)

def render_html(repos, now_str, today=None):
    if today is None: today = datetime.utcnow()
    sec30 = section("Last 30 days",  today - timedelta(days=30),  today, NAVY, repos)
    sec365 = section("Last 12 months", today - timedelta(days=365), today, TEAL, repos)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>App Portfolio Dashboard</title></head>
<body style="margin:0;padding:0;background:#f4f6fb">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:#f4f6fb"><tr><td align="center" style="padding:0">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:700px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:{INK}"><tr><td style="padding:0 16px 40px">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:{NAVY};border-radius:0 0 12px 12px;margin-bottom:6px"><tr><td style="padding:20px 22px">
<div style="color:#ffffff;font-size:21px;font-weight:700">App Portfolio Dashboard</div>
<div style="color:#c7d2e8;font-size:12px;margin-top:4px">Last updated {esc(now_str)} &middot; built in GitHub Actions from your repos</div>
</td></tr></table>
<div style="font-size:13px;color:{MUTE};margin:14px 2px 0;line-height:1.5">Showing the <b>last 30 days</b> first, then the <b>last 12 months</b>. Cards, charts and timeline numbers are windowed; the repo tables list lifetime totals (matching data.csv). A repo appears in a window only if it had a commit during it.</div>
{sec30}
{sec365}
<div style="color:#889;font-size:12px;margin-top:30px;line-height:1.6;border-top:1px solid {LINE};padding-top:14px">Auto-generated in GitHub Actions. Forks, archived repos and vendored libraries are excluded; new repos are picked up automatically. Charts are plain HTML so they render inside email.</div>
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
# end
