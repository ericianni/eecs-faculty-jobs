# EECS Faculty Job Finder

A small static site that helps EE / CS / Computer Engineering faculty (tenure-track, tenured, lecturers/instructors, adjuncts; on-campus and remote) find current academic job postings.

**Live site:** https://ericianni.github.io/eecs-faculty-jobs/

## How it works
- `fetch_jobs.py` (Python 3 standard library only — no `requirements.txt` needed) collects postings from public, no-login sources and writes `jobs.json`:
  - Chronicle of Higher Education Jobs and Inside Higher Ed Careers (public keyword RSS + "Working from home" feed)
  - `sources_extra.py`: CRA Career Center, IEEE Job Site, ACM Career Center (JSON-LD job pages); university career sites (public Workday JSON for ~18 schools, UC Recruit for 7 UC campuses, UW Academic HR)
- Each posting page is fetched (cached in `cache/`, polite delays, robots.txt respected) and tagged with rank, field, modality (with the matched phrase saved as `modality_evidence`), state and region. Tags are keyword-based guesses — always check the original posting.
- `index.html` is a single vanilla HTML/CSS/JS page that loads `jobs.json`: sortable table, search, rank/field/modality/region/location filters, Remote/Online toggle, and "Search elsewhere" links (Indeed, HigherEdJobs, LinkedIn, Chronicle, Inside Higher Ed).
- URL params: `?remote=1`, `?field=CS`, `?region=Pacific%20Northwest`, `?q=...`, `?community=1` (Community only), `?variant=B` / `?variant=C` ("Search elsewhere" layout: B = text box + quick-pick chips, the default; C = follows the page search/filters with Edit/Re-sync; the "Try layout" switch sets this too).
- Community submissions are pinned above every sort under a "Community submissions" label, with a tinted row, a left accent bar and a **★ Community** badge.

## Relevance filter
`relevance.py` runs on every listing (all sources) before enrichment. Include terms are grouped by Oregon State EECS degree programs and research areas (AI, CS, Cybersecurity, ECE, Materials Science, Robotics, Semiconductors; Communications & Signal Processing, Graphics & Visualization, CS Education, Data Science & Engineering, Electronic Materials & Devices, Energy Systems, Health Engineering, Integrated Electronics, Networking & Computer Systems, Programming Languages, SE & HCI, Theory). Listings in clearly non-EECS fields (astrophysics, chemistry, pharmacy, health information technology, ...) are dropped; science/engineering neighbours (math, statistics, physics, biology, mechanical/civil engineering, ...) are dropped unless the title also names an EECS area (e.g. "Robotics in Mechanical Engineering", "Statistics and Data Science" stay). Dry run: `python3 check_relevance.py`; tests: `python3 -m unittest test_relevance`.

## Community submissions
Anyone can submit a job with the Google Form linked from the **Submit a job** button (set in `site-config.js`). No sign-in is needed. Every hour, `.github/workflows/community-intake.yml` reads the form's published response CSV (repo secret `COMMUNITY_FORM_CSV_URL`), validates new rows with `community.py` (link loads, `relevance.py`, duplicate check, spam guards), and adds passing ones to `community_jobs.json` and `jobs.json`, shown with a **community-added** tag. They expire on the deadline, or after 90 days. If the scraper later finds the same job, the scraped listing replaces it. Setup steps: [docs/COMMUNITY_FORM.md](docs/COMMUNITY_FORM.md). Tests: `python3 -m unittest test_community`.

## Weekday refresh
`.github/workflows/refresh.yml` runs Monday–Friday at 5:17 PM Pacific (cron `17 17 * * 1-5` with `timezone: America/Los_Angeles`, so it tracks PDT/PST automatically) and on demand (Actions → "Refresh job listings" → Run workflow). It commits `jobs.json` only if it changed. Failed sources are logged and skipped; if the new listing count is under 50% of the previous one, the old `jobs.json` is kept and the run fails.

## Run locally
```
python3 fetch_jobs.py
python3 -m http.server 8080   # then open http://localhost:8080/
```
