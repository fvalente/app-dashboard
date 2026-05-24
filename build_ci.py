#!/usr/bin/env python3
"""
CI dashboard generator (runs in GitHub Actions).
Auto-discovers the authenticated user's repos via the GitHub API, clones each,
computes activity metrics from git history, and writes a self-contained
output/dashboard.html (+ output/data.csv). Delivery (email / private repo push)
is handled by the workflow, not this script.

Privacy: prints ONLY aggregate counts to stdout — never repo names — because
public-repo Actions logs are world-readable.

Env:
  GH_TOKEN   (required)  fine-grained PAT with Contents:read + Metadata:read
  GH_AFFIL   (optional)  affiliation filter, default "owner"
  EXTRA_REPOS(optional)  comma-separated owner/name to also include (e.g. collaborator repos)
  EXCLUDE_REPOS(optional) comma-separated owner/name to skip
"""
import os, sys, json, subprocess, tempfile, shutil, base64, io, csv, urllib.request
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

def render(repos):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.dates as mdates, numpy as np
    from matplotlib.cm import ScalarMappable; from matplotlib.colors import Normalize
    def d(s): return datetime.strptime(s,"%Y-%m-%d")
    names=[m["name"] for m in repos]; starts=[d(m["first"]) for m in repos]
    lasts=[d(m["last"]) for m in repos]; commits=[m["commits"] for m in repos]
    norm=Normalize(min(commits),max(commits)); cmap=plt.cm.viridis
    colors=[cmap(norm(c)) for c in commits]
    # timeline
    f1,ax=plt.subplots(figsize=(12,0.45*len(repos)+1.5))
    y=list(range(len(repos)))[::-1]
    for yi,s,l,c,col in zip(y,starts,lasts,commits,colors):
        if (l-s).days<3: ax.plot(mdates.date2num(s),yi,"o",color=col,ms=10,mec="#333",mew=.6)
        else: ax.barh(yi,l-s,left=s,height=.6,color=col,ec="#333",lw=.6)
        ax.text(mdates.date2num(l)+4,yi,str(c),va="center",fontsize=9,fontweight="bold")
    ax.set_yticks(y); ax.set_yticklabels(names,fontsize=10)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n%Y"))
    ax.grid(axis="x",color="#eee"); ax.set_axisbelow(True)
    for sp in ["top","right"]: ax.spines[sp].set_visible(False)
    ax.set_title("Timeline — bar = first→latest commit, number = commits",loc="left",fontsize=12,fontweight="bold")
    b1=io.BytesIO(); f1.savefig(b1,format="png",dpi=140,bbox_inches="tight",facecolor="white"); plt.close(f1)
    # weekly heatmap
    def mon(w): return datetime.strptime(w+"-1","%G-%V-%u")
    allw=set()
    for m in repos: allw|=set(m["weekly"].keys())
    heat=""
    if allw:
        wmin=min(mon(w) for w in allw); wmax=max(mon(w) for w in allw)
        mondays=[]; dd=wmin
        while dd<=wmax: mondays.append(dd); dd+=timedelta(days=7)
        lk=[mm.strftime("%G-%V") for mm in mondays]
        M=np.zeros((len(repos),len(mondays)))
        for i,m in enumerate(repos):
            for w,c in m["weekly"].items():
                if w in lk: M[i,lk.index(w)]=c
        f2,ax2=plt.subplots(figsize=(max(12,len(mondays)*0.26),0.42*len(repos)+1.5))
        masked=np.ma.masked_where(M==0,M); cm=plt.cm.YlGnBu.copy(); cm.set_bad("#f7f7f7")
        im=ax2.imshow(masked,aspect="auto",cmap=cm,vmin=1,vmax=max(M.max(),1))
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                if M[i,j]>0: ax2.text(j,i,int(M[i,j]),ha="center",va="center",fontsize=7,
                                      color="white" if M[i,j]>M.max()*.55 else "#222")
        ax2.set_yticks(range(len(names))); ax2.set_yticklabels(names,fontsize=9.5)
        xt=[];xl=[];lastk=None
        for j,mm in enumerate(mondays):
            k=(mm.year,mm.month)
            if k!=lastk: xt.append(j);xl.append(mm.strftime("%b\n%Y"));lastk=k
        ax2.set_xticks(xt); ax2.set_xticklabels(xl,fontsize=8)
        ax2.set_xticks(np.arange(-.5,len(mondays),1),minor=True)
        ax2.set_yticks(np.arange(-.5,len(names),1),minor=True)
        ax2.grid(which="minor",color="white",lw=1.2); ax2.tick_params(which="minor",length=0)
        for sp in ax2.spines.values(): sp.set_visible(False)
        ax2.set_title("Weekly commits per app (blank = no activity)",loc="left",fontsize=12,fontweight="bold")
        b2=io.BytesIO(); f2.savefig(b2,format="png",dpi=130,bbox_inches="tight",facecolor="white"); plt.close(f2)
        heat=base64.b64encode(b2.getvalue()).decode()
    return base64.b64encode(b1.getvalue()).decode(), heat

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
    tl,heat = render(repos)
    open(os.path.join(OUT,"timeline.png"),"wb").write(base64.b64decode(tl))
    if heat: open(os.path.join(OUT,"weekly.png"),"wb").write(base64.b64decode(heat))
    # csv
    with open(os.path.join(OUT,"data.csv"),"w",newline="") as f:
        w=csv.writer(f); w.writerow(["app","first_commit","last_commit","commits","active_days","insertions","deletions","churn","repo"])
        for m in repos: w.writerow([m["name"],m["first"],m["last"],m["commits"],m["active_days"],m["insertions"],m["deletions"],m["churn"],m["full"]])
    # html
    tot_c=sum(m["commits"] for m in repos); tot_a=sum(m["active_days"] for m in repos); tot_ch=sum(m["churn"] for m in repos)
    starts=[m["first"] for m in repos]; lasts=[m["last"] for m in repos]
    now=datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    rows=json.dumps([{"name":m["name"],"first":m["first"],"last":m["last"],"commits":m["commits"],
        "active":m["active_days"],"churn":m["churn"],"full":m["full"]} for m in repos])
    heatpanel = ('<div class="panel"><h2>Weekly effort</h2><img src="data:image/png;base64,'+heat+'"></div>') if heat else ""
    html=TEMPLATE.replace("__NOW__",now).replace("__N__",str(len(repos))).replace("__C__",f"{tot_c:,}")\
        .replace("__A__",f"{tot_a:,}").replace("__CH__",f"{tot_ch:,}").replace("__LO__",min(starts)).replace("__HI__",max(lasts))\
        .replace("__TL__",tl).replace("__HEAT__",heatpanel).replace("__ROWS__",rows)
    with open(os.path.join(OUT,"dashboard.html"),"w",encoding="utf-8") as f: f.write(html)
    # privacy-safe log: counts only, no names
    print(json.dumps({"repos":len(repos),"commits":tot_c,"active_days":tot_a,"churn":tot_ch,"updated":now}))

TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>App Portfolio Dashboard</title>
<style>*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1a2233;background:#f4f6fb}
header{background:#1f3864;color:#fff;padding:22px 30px}header h1{margin:0;font-size:22px}header .sub{opacity:.8;font-size:13px;margin-top:4px}
.wrap{max-width:1200px;margin:0 auto;padding:24px 30px 60px}.cards{display:flex;gap:16px;flex-wrap:wrap;margin:20px 0}
.card{background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08);flex:1;min-width:150px}
.card .v{font-size:28px;font-weight:700;color:#1f3864}.card .l{font-size:12px;color:#667;text-transform:uppercase;letter-spacing:.04em;margin-top:4px}
.panel{background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin:18px 0}
.panel h2{margin:0 0 12px;font-size:16px}.panel img{width:100%;height:auto;border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:8px 10px;border-bottom:1px solid #eef0f4;text-align:right}
th:first-child,td:first-child{text-align:left}th{cursor:pointer;color:#445;user-select:none;background:#fafbfe}th:hover{color:#1f3864}
tr:hover td{background:#fafbfe}a{color:#2c7fb8;text-decoration:none}.foot{color:#889;font-size:12px;margin-top:24px;line-height:1.5}</style></head><body>
<header><h1>App Portfolio Dashboard</h1><div class="sub">Last updated __NOW__ · built in GitHub Actions from your repos</div></header>
<div class="wrap"><div class="cards">
<div class="card"><div class="v">__N__</div><div class="l">Apps</div></div>
<div class="card"><div class="v">__C__</div><div class="l">Commits</div></div>
<div class="card"><div class="v">__A__</div><div class="l">Active days</div></div>
<div class="card"><div class="v">__CH__</div><div class="l">Lines changed</div></div>
<div class="card"><div class="v" style="font-size:15px">__LO__<br>→ __HI__</div><div class="l">Span</div></div></div>
<div class="panel"><h2>Timeline</h2><img src="data:image/png;base64,__TL__"></div>
__HEAT__
<div class="panel"><h2>All apps <span style="font-weight:400;color:#889;font-size:12px">(click a header to sort)</span></h2>
<table id="t"><thead><tr><th data-k="name">App</th><th data-k="first">Start</th><th data-k="last">Latest</th>
<th data-k="commits">Commits</th><th data-k="active">Active days</th><th data-k="churn">Lines changed</th><th data-k="full">Repo</th></tr></thead><tbody></tbody></table></div>
<div class="foot">Auto-generated in GitHub Actions. Forks, archived repos and vendored libraries are excluded; new repos are picked up automatically.</div></div>
<script>var DATA=__ROWS__;function fmt(n){return n.toLocaleString()}
function render(rows){var b=document.querySelector("#t tbody");b.innerHTML="";
rows.forEach(function(r){var tr=document.createElement("tr");
tr.innerHTML='<td>'+r.name+'</td><td>'+r.first+'</td><td>'+r.last+'</td><td>'+fmt(r.commits)+'</td><td>'+fmt(r.active)+'</td><td>'+fmt(r.churn)+'</td><td style="text-align:left">'+r.full+'</td>';
b.appendChild(tr);});}
var dir={};document.querySelectorAll("#t th").forEach(function(th){th.onclick=function(){var k=th.dataset.k;dir[k]=!dir[k];var s=dir[k]?1:-1;
DATA.sort(function(a,b){var x=a[k],y=b[k];if(typeof x==="string"){return s*x.localeCompare(y)}return s*(x-y)});render(DATA);};});
render(DATA);</script></body></html>"""

if __name__=="__main__":
    build()
