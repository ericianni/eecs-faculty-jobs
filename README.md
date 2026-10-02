# EECS Faculty Job Finder

A small static site that helps EE / CS / Computer Engineering faculty (tenure-track, tenured, lecturers/instructors, adjuncts; on-campus and remote) find current academic job postings.

**Live site:** https://ericianni.github.io/eecs-faculty-jobs/

## How it works
- `fetch_jobs.py` (Python 3 standard library only — no `requirements.txt` needed) collects postings from public, no-login sources and writes `jobs.json`:
  - Chronicle of Higher Education Jobs and Inside Higher Ed Careers (public keyword RSS + "Working from home" feed)
  - `sources_extra.py`: CRA Career Center, IEEE Job Site, ACM Career Center (JSON-LD job pages); university career sites (public Workday JSON for ~18 schools, UC Recruit for 7 UC campuses, UW Academic HR)
- Each posting page is fetched (cached in `cache/`, polite delays, robots.txt respected) and tagged with rank, field, modality (with the matched phrase saved as `modality_evidence`), state and region. Tags are keyword-based guesses — always check the original posting.
- `index.html` is a single vanilla HTML/CSS/JS page that loads `jobs.json`: sortable table, search, rank/field/modality/region/location filters, Remote/Online toggle, and "Search elsewhere" links (Indeed, HigherEdJobs, LinkedIn, Chronicle, Inside Higher Ed).
- URL params: `?remote=1`, `?field=CS`, `?region=Pacific%20Northwest`, `?q=...`.

## Daily refresh
`.github/workflows/refresh.yml` runs every day at 13:17 UTC (~6:17 AM Pacific) and on demand (Actions → "Refresh job listings" → Run workflow). It commits `jobs.json` only if it changed. Failed sources are logged and skipped; if the new listing count is under 50% of the previous one, the old `jobs.json` is kept and the run fails.

## Run locally
```
python3 fetch_jobs.py
python3 -m http.server 8080   # then open http://localhost:8080/
```
