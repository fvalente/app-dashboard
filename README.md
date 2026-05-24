# App Portfolio Dashboard

A self-hosted GitHub Action that auto-discovers **all** your repositories, mines their git history, and emails you a private "what have I been shipping" dashboard on a schedule. The whole report renders **inline in the email body** — no attachment to open, no site to host.

Unlike the usual GitHub-stats tools (profile cards, README badges, SVG widgets), this isn't a public badge you embed for others. It's a periodic, private digest of your own portfolio: a cross-repo timeline, per-repo weekly rhythm, a weekday × hour "punch card" of when you actually code, and auto-written insights — delivered to your inbox.

## What's in the email

- **Work patterns (last 12 months)** — an auto-generated Insights block (peak hour, most active weekday, night-owl %, weekend %, longest daily streak, busiest week, top repo by commits/churn) and a **punch card** heatmap (weekday × hour-of-day) showing when you commit.
- **Last 30 days** — summary cards, a timeline (first→latest commit per repo), and commits-per-app.
- **Last 12 months** — summary cards, a **per-repo weekly-activity heatmap** (each repo's whole rhythm, not just endpoints), commits-per-app, and a table of lifetime totals.

All commit times use each commit's own recorded local timezone, i.e. when you were actually working.

## How it works

A scheduled GitHub Action runs `build_ci.py`, which:

1. Lists your repos via the GitHub API (skipping forks, archived repos, and vendored-library owners).
2. Clones each with full history and computes metrics from `git log` (commits, active days, churn, and per-commit week / weekday / hour).
3. Renders a self-contained, **email-safe** `output/dashboard.html` — no JavaScript, no base64 images, no inline SVG; charts are plain inline-styled / `bgcolor` HTML tables so they survive Gmail's sanitizer and stay under its ~102 KB clipping limit. It also writes `output/data.csv`.
4. The workflow emails that HTML as the message body (`html_body`), with `dashboard.html` + `data.csv` attached as a fallback.

The repo is safe to keep **public**: the workflow only triggers on `schedule` and manual `workflow_dispatch`, never on pull requests, so your secrets are never exposed to untrusted runs. Logs print only aggregate counts — never repo names.

## Prerequisites

- A GitHub account and the ability to create a fine-grained personal access token.
- A Gmail account with an **App Password** (requires 2-Step Verification). A normal login password will not work for SMTP.

## Setup

1. **Get the code into your account.** Fork this repo, or create your own from these files.

2. **Create a fine-grained PAT** (GitHub → Settings → Developer settings → Fine-grained tokens) with read access to the repositories you want included:
   - Repository access: All repositories (or select specific ones)
   - Permissions: **Contents: Read-only** and **Metadata: Read-only**

3. **Create a Gmail App Password** (Google Account → Security → App passwords). Copy the 16-character value.

4. **Add repository secrets** (repo → Settings → Secrets and variables → Actions → *Secrets*):

   | Secret | Value |
   | --- | --- |
   | `REPOS_TOKEN` | the fine-grained PAT from step 2 |
   | `MAIL_USERNAME` | your Gmail address |
   | `MAIL_PASSWORD` | the Gmail **App Password** from step 3 |
   | `MAIL_TO` | where to send the dashboard (can be the same Gmail) |

5. **(Optional) Add repository variables** (same screen → *Variables*):

   | Variable | Purpose |
   | --- | --- |
   | `EXTRA_REPOS` | comma-separated `owner/name` to force-include (e.g. collaborator repos) |
   | `EXCLUDE_REPOS` | comma-separated `owner/name` to skip |

   To include shared/org repos, also change `GH_AFFIL` in `.github/workflows/dashboard.yml` from `owner` to e.g. `owner,collaborator,organization_member`.

6. **Enable Actions** for the repo, then run it once manually: Actions tab → **Build app dashboard** → **Run workflow**. Check your inbox.

## Schedule

Defined by the `cron` in `.github/workflows/dashboard.yml` (defaults to Mon & Thu 08:00). **GitHub cron is UTC** — adjust for your timezone. Note the email itself uses each commit's local timezone for the time-of-day charts; only the *run schedule* is UTC.

## Delivery alternative: push to a private repo

If you'd rather browse the dashboard on GitHub than receive an email, the workflow contains a commented-out step that pushes `output/` to a separate private repo. Create that repo, give the token `Contents: write` on it, then swap the email step for that block.

## Customization

- **Time windows** — edit `render_html()` in `build_ci.py` (currently last 30 days + last 12 months).
- **Vendored-library owners** to exclude — `VENDOR_OWNERS` in `build_ci.py`.
- **Colors / layout** — the palette constants and helper functions near the top of the rendering section.

## Output files

- `output/dashboard.html` — the email-safe dashboard (also the email body).
- `output/data.csv` — lifetime per-repo figures (app, first/last commit, commits, active days, insertions, deletions, churn, repo).

## Requirements

None beyond the Python standard library — `requirements.txt` is intentionally empty. (Charts are plain HTML/CSS, so the old matplotlib/numpy dependencies were removed.)

## License

No license file is included yet. If you want others to reuse this, add one (e.g. MIT) via GitHub's "Add file → Create new file → `LICENSE`" template picker.
