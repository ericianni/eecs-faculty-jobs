#!/usr/bin/env python3
"""Community job submissions: Google Form -> published response CSV -> community_jobs.json -> jobs.json.

The form needs no sign-in. Its response sheet is published to the web as CSV, and
.github/workflows/community-intake.yml runs `python3 community.py intake` every hour.

Security: every CSV cell is untrusted. This module only parses text, never runs it, never
interpolates it into shell commands, and only fetches http(s) posting URLs on public IP
addresses (each redirect is re-checked). Rejected rows are logged with a reason and row id
only. Submitted text and URLs are not written to the public log.

CLI:
  python3 community.py intake [--csv-url URL | --csv-file F] [--max-per-run N]   (default URL: $COMMUNITY_FORM_CSV_URL)
  python3 community.py merge    -> prune expired community jobs, merge active ones into jobs.json
"""
import argparse, csv, hashlib, io, ipaddress, json, os, re, socket, sys, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone

import relevance

COMMUNITY_FILE = "community_jobs.json"
JOBS_FILE = "jobs.json"
SOURCE = "Community submission"
TAG = "community-added"
DEFAULT_TTL_DAYS = 90
MAX_DEADLINE_DAYS = 400          # ignore absurd deadlines (treated like no deadline)
MAX_PER_RUN = 20                 # spam guard: at most this many new rows are processed per run (rest wait)
MAX_ACTIVE_PER_HOST = 25         # spam guard: active community jobs per posting host
MAX_RETRIES = 3                  # posting URL unreachable (timeout/DNS): retry on this many runs before rejecting
PROCESSED_FILE = "community_processed.json"
REJECTIONS_LOG = "community_rejections.log"
LIMITS = {"title": 200, "institution": 150, "department": 150, "url": 1000, "location": 150, "deadline": 40, "field": 60, "notes": 1500}
UA = "Mozilla/5.0 (EECS-faculty-jobs community link check; +https://github.com/ericianni/eecs-faculty-jobs)"

# Google Form question titles (CSV header) -> key. Matching is case-insensitive on the start of the header,
# so "Posting URL (required)" or "Application deadline (optional)" also work.
FORM_FIELDS = [("job title", "title"), ("institution", "institution"), ("department", "department"),
               ("posting url", "url"), ("location", "location"), ("application deadline", "deadline"),
               ("field", "field"), ("notes", "notes"), ("timestamp", "timestamp")]
REQUIRED = ("title", "institution", "url")

# Job boards that often block bots (403/406/429/999): a block from these counts as "link OK, unverified".
KNOWN_BOARDS = ("chronicle.com", "insidehighered.com", "higheredjobs.com", "indeed.com", "linkedin.com",
                "myworkdayjobs.com", "workday.com", "interfolio.com", "academicjobsonline.org", "cra.org",
                "ieee.org", "acm.org", "usajobs.gov", "governmentjobs.com", "schooljobs.com", "peopleadmin.com",
                "taleo.net", "icims.com", "ziprecruiter.com", "glassdoor.com", "jobs.ac.uk", "academickeys.com",
                "applytojob.com", "ultipro.com", "ukg.com", "oraclecloud.com", "successfactors.com", "hrmdirect.com")
BLOCK_CODES = {401, 403, 405, 406, 429, 999}

FIELD_MAP = {"Computer Science": "CS", "Artificial Intelligence / Machine Learning": "CS", "Data Science": "CS",
             "Cybersecurity": "CS", "Robotics": "CS", "Information Technology": "CS",
             "Electrical Engineering": "EE", "Semiconductors / Electronics": "EE", "Computer Engineering": "Computer Eng"}


# ---------------- parsing ----------------
def _clean(sub):
    for k, n in LIMITS.items():            # clamp sizes; strip control chars; collapse whitespace
        v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(sub.get(k, "") or ""))[:n].strip()
        sub[k] = v if k == "notes" else re.sub(r"\s+", " ", v)
    return sub

def header_map(headers):
    out = {}
    for i, h in enumerate(headers):
        hl = re.sub(r"\s+", " ", (h or "").strip().lower())
        for prefix, key in FORM_FIELDS:
            if hl.startswith(prefix) and key not in out.values():
                out[i] = key; break
    return out

def row_id(cells):
    """Stable id for a response row (timestamp + all answers)."""
    return hashlib.sha256("\x1f".join(c.strip() for c in cells).encode()).hexdigest()[:16]

def parse_csv(text):
    """Parse the published response CSV -> list of (row_id, submission dict). Raises ValueError on a bad header."""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows: return []
    hm = header_map(rows[0])
    missing = [k for k in ("title", "institution", "url") if k not in hm.values()]
    if missing: raise ValueError(f"CSV header is missing column(s) for {missing}; got {rows[0][:10]}")
    out = []
    for cells in rows[1:]:
        if not any(c.strip() for c in cells): continue
        sub = {key: (cells[i] if i < len(cells) else "") for i, key in hm.items()}
        out.append((row_id(cells), _clean(sub) | {"timestamp": sub.get("timestamp", "")[:40]}))
    return out

def parse_deadline(s):
    """Return a date or None. Accepts YYYY-MM-DD, MM/DD/YYYY, 'December 1, 2026', '1 Dec 2026'."""
    s = (s or "").strip().rstrip(".")
    if not s or re.fullmatch(r"(?i)(open until filled|until filled|open|rolling|n/?a|none|tbd|-+)", s): return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: pass
    raise ValueError(f"couldn't read the deadline '{s}'. Use YYYY-MM-DD, or leave it blank.")


# ---------------- normalization / dedup ----------------
TRACKING = re.compile(r"^(utm_\w+|trackid|source|src|ref|referrer|gclid|fbclid|mc_\w+|_hs\w+|campaign|cmpid|jobpipeline|codes)$", re.I)

def normalize_url(url):
    """Canonical form for duplicate detection: https, lower host w/o www, no fragment/tracking params/trailing slash."""
    try: p = urllib.parse.urlsplit((url or "").strip())
    except ValueError: return (url or "").strip().lower()
    host = (p.hostname or "").lower()
    if host.startswith("www."): host = host[4:]
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = re.sub(r"/+$", "", p.path) or ""
    path = re.sub(r"(?i)\?trackid.*$", "", path)
    return urllib.parse.urlunsplit(("https", host, path, urllib.parse.urlencode(sorted(q)), ""))

def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def _inst_key(s):
    s = re.sub(r"\(.*?\)", "", s or "")             # drop "(Dept)" suffixes
    s = re.sub(r"(?i)\b(the|at|of)\b", "", s)
    return norm(s)

def job_keys(j):
    urls = {normalize_url(u) for u in [j.get("url", "")] + list(j.get("alt_urls") or []) if u}
    return urls, (norm(j.get("title")), _inst_key(j.get("institution")))

def same_job(a, b):
    ua, ta = job_keys(a); ub, tb = job_keys(b)
    if ua & ub: return True
    if not (ta[0] and ta[0] == tb[0] and ta[1] and tb[1]): return False
    # same institution, allowing one name to contain the other ("UC San Diego" vs "UC San Diego Jacobs School").
    # A shared prefix alone isn't enough: "Indiana University East" is not "Indiana University South Bend".
    short, long_ = sorted((ta[1], tb[1]), key=len)
    return short == long_ or (len(short) >= 8 and long_.startswith(short))

def find_duplicate(job, *pools):
    for pool in pools:
        for other in pool:
            if same_job(job, other): return other
    return None


# ---------------- validation ----------------
def _host_is_public(host):
    try: infos = socket.getaddrinfo(host, None)
    except socket.gaierror: return False, "the posting URL couldn't be loaded (host name doesn't resolve)"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global: return False, "the URL points to a private or internal address"
    return True, ""

def validate_url_syntax(url):
    try: p = urllib.parse.urlsplit(url)
    except ValueError: return "the posting URL isn't a valid URL"
    if p.scheme not in ("http", "https"): return "the posting URL must start with http:// or https://"
    if not p.hostname or "." not in p.hostname: return "the posting URL has no valid host name"
    if p.username or p.password: return "the posting URL can't include a username or password"
    try: ipaddress.ip_address(p.hostname); return "please link to a host name, not a raw IP address"
    except ValueError: pass
    return None

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        err = validate_url_syntax(newurl)
        if err: raise urllib.error.URLError(f"redirected to a bad URL ({err})")
        ok, why = _host_is_public(urllib.parse.urlsplit(newurl).hostname)
        if not ok: raise urllib.error.URLError(f"redirect blocked: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def http_status(url, timeout=20):
    """Return (final_status, final_url). Raises URLError on network failure."""
    opener = urllib.request.build_opener(_SafeRedirect)
    last = None
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        try:
            with opener.open(req, timeout=timeout) as r:
                if method == "GET": r.read(65536)
                return r.status, r.geturl()
        except urllib.error.HTTPError as e:
            last = (e.code, url)
            if method == "HEAD" and e.code in (400, 403, 404, 405, 406, 429, 500, 501, 999): continue  # many sites reject HEAD
            return last
    return last

def check_url(url, status_fn=http_status, resolve_fn=_host_is_public):
    """(ok, note). 2xx/3xx ok; bot-blocks from known boards are ok with a note."""
    err = validate_url_syntax(url)
    if err: return False, err
    host = urllib.parse.urlsplit(url).hostname.lower()
    ok, why = resolve_fn(host)
    if not ok: return False, why
    try: code, _final = status_fn(url)
    except Exception as e: return False, f"the posting URL couldn't be loaded ({type(e).__name__}: {str(e)[:120]})"
    if 200 <= code < 400: return True, ""
    known = any(host == b or host.endswith("." + b) for b in KNOWN_BOARDS)
    if code in BLOCK_CODES and known:
        return True, f"{host} blocks automated checks (HTTP {code}), so the link wasn't verified"
    if code in (404, 410): return False, f"the posting URL returns HTTP {code} (not found). The posting may have been taken down."
    return False, f"the posting URL returned HTTP {code}"


# ---------------- job construction / expiry ----------------
def expires_on(deadline, submitted):
    if deadline and deadline <= submitted + timedelta(days=MAX_DEADLINE_DAYS): return deadline
    return submitted + timedelta(days=DEFAULT_TTL_DAYS)

def to_job(sub, submission_id, today):
    import fetch_jobs as F
    dept = sub.get("department", "")
    inst = sub["institution"] + (f" ({dept})" if dept else "")
    loc = sub.get("location", "") or ""
    _, state = F.parse_location([loc]) if loc else ("", "Other/Unknown")
    deadline = parse_deadline(sub.get("deadline", ""))
    notes = sub.get("notes", "")
    text = f"{sub['title']} {dept} {loc} {notes}"
    field = F.field_of(f"{sub['title']} {dept}") or FIELD_MAP.get(sub.get("field", ""), "Other")
    modality = F.modality_of(f"{sub['title']} {loc}")
    if modality == "Unknown" and notes: modality = F.modality_of(notes)
    snippet = (f"{sub.get('field', '')}. " if sub.get("field") else "") + re.sub(r"\s+", " ", notes)
    return {"title": sub["title"], "institution": inst, "location": loc, "state": state, "region": F.region_of(state),
            "posted": today.isoformat(), "source": SOURCE, "sources": [SOURCE], "url": sub["url"], "alt_urls": [],
            "rank": F.rank_of(text), "field": field, "modality": modality,
            "modality_evidence": "community submission (location/notes)" if modality != "Unknown" else "",
            "has_full_text": False, "snippet": snippet[:300].strip(),
            "community": True, "tags": [TAG], "submitted": today.isoformat(), "submission_id": submission_id,
            "deadline": deadline.isoformat() if deadline else "",
            "expires": expires_on(deadline, today).isoformat()}

def is_active(j, today):
    try: return date.fromisoformat(j["expires"]) >= today
    except (KeyError, ValueError): return False


# ---------------- files ----------------
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return default

def load_community(path=COMMUNITY_FILE):
    return load_json(path, {"jobs": []}).get("jobs", [])

def save_community(jobs, path=COMMUNITY_FILE):
    with open(path, "w") as f:
        json.dump({"description": "Community-submitted jobs (via the Google Form). Merged into jobs.json; expire on deadline or 90 days.",
                   "jobs": jobs}, f, indent=1)
        f.write("\n")

def scraped_only(jobs):
    return [j for j in jobs if not j.get("community")]

def merge(scraped, community, today):
    """Return (merged_jobs, active_community, pruned_count, shown_count). Expired community jobs are pruned; a community job that is also
    scraped is hidden (the scraped listing wins and stays untagged) but kept in the file until it expires."""
    active = [c for c in community if is_active(c, today)]
    pruned = len(community) - len(active)
    shown = [c for c in active if not find_duplicate(c, scraped)]
    merged = sorted(scraped + shown, key=lambda j: j.get("posted", ""), reverse=True)
    return merged, active, pruned, len(shown)

def merge_files(jobs_path=JOBS_FILE, community_path=COMMUNITY_FILE, today=None):
    today = today or datetime.now(timezone.utc).date()
    data = load_json(jobs_path, None)
    if data is None: return None
    scraped = scraped_only(data.get("jobs", []))
    merged, active, pruned, shown = merge(scraped, load_community(community_path), today)
    data["jobs"] = merged; data["count"] = len(merged)
    data["scraped_count"] = len(scraped); data["community_count"] = shown
    data.setdefault("sources", {})[SOURCE] = "https://ericianni.github.io/eecs-faculty-jobs/#submit"
    with open(jobs_path, "w") as f: json.dump(data, f, indent=1)
    if pruned: save_community(active, community_path)
    return {"shown": shown, "active": len(active), "pruned": pruned, "scraped": len(scraped)}


# ---------------- submission pipeline ----------------
class Rejected(Exception):
    def __init__(self, reason, code): super().__init__(reason); self.reason, self.code = reason, code

SPAM_RE = re.compile(r"\b(casino|viagra|cialis|porn|xxx|escort|bitcoin|crypto(currency)?|forex|loan|payday|seo services|"
                     r"backlinks?|betting|onlyfans|telegram|whatsapp|click here|buy now|free money|weight loss)\b", re.I)

def junk_reason(sub):
    """Cheap spam/junk checks on the text fields. Returns a reason or None."""
    t, inst = sub.get("title", ""), sub.get("institution", "")
    for name, v in (("job title", t), ("institution", inst)):
        if len(re.findall(r"[A-Za-z]", v)) < 3: return f"{name} is too short or has no letters"
        if re.search(r"https?://|www\.|<[a-z/!]", v, re.I): return f"{name} contains a link or HTML"
    if len(t.split()) > 30: return "job title is too long"
    blob = " ".join(sub.get(k, "") for k in ("title", "institution", "department", "notes"))
    if SPAM_RE.search(blob): return f"spam keyword '{SPAM_RE.search(blob).group(0)}'"
    if len(re.findall(r"https?://", sub.get("notes", ""))) > 3: return "too many links in notes"
    if re.search(r"(.)\1{9,}", blob): return "repeated characters"
    return None

def evaluate(sub, submission_id, scraped, community, today, url_checker=check_url):
    """Validate one submission. Returns (job, note); raises Rejected(reason, code)."""
    missing = [k for k in REQUIRED if not sub.get(k)]
    if missing: raise Rejected("missing required field(s): " + ", ".join(missing), "invalid")
    err = validate_url_syntax(sub["url"])
    if err: raise Rejected(err, "invalid")
    jr = junk_reason(sub)
    if jr: raise Rejected(jr, "spam")
    try: job = to_job(sub, submission_id, today)
    except ValueError as e: raise Rejected(str(e), "invalid")
    if job["deadline"] and date.fromisoformat(job["deadline"]) < today:
        raise Rejected(f"application deadline {job['deadline']} has already passed", "invalid")
    ok, why = relevance.classify(job)
    if not ok: raise Rejected(f"not an EECS position ({why})", "off-topic")
    active = [c for c in community if is_active(c, today)]
    dup = find_duplicate(job, scraped, active)
    if dup:
        where = "an earlier community submission" if dup.get("community") else (dup.get("source") or "a scraped source")
        raise Rejected(f"duplicate of \"{dup['title'][:80]}\" at {dup['institution'][:60]} ({where})", "duplicate")
    host = (urllib.parse.urlsplit(job["url"]).hostname or "").lower()
    if sum(1 for c in active if (urllib.parse.urlsplit(c["url"]).hostname or "").lower() == host) >= MAX_ACTIVE_PER_HOST:
        raise Rejected(f"too many active community jobs from {host}", "spam")
    ok, note = url_checker(job["url"])
    if not ok: raise Rejected(note, "unreachable" if note.startswith("the posting URL couldn't be loaded") else "bad-link")
    if note: job["link_note"] = note
    return job, note

def fetch_csv(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")

def intake(csv_text, today=None, max_per_run=MAX_PER_RUN, url_checker=check_url,
           jobs_path=JOBS_FILE, community_path=COMMUNITY_FILE, processed_path=PROCESSED_FILE, log_path=REJECTIONS_LOG):
    """Process new CSV rows. Returns a summary dict; updates the data files in place."""
    today = today or datetime.now(timezone.utc).date()
    processed = load_json(processed_path, {})
    community = load_community(community_path)
    scraped = scraped_only(load_json(jobs_path, {"jobs": []}).get("jobs", []))
    rows = parse_csv(csv_text)
    # new = never seen, or a transient "unreachable" failure with retries left
    new = [(rid, sub) for rid, sub in rows if rid not in processed
           or (processed[rid].get("status") == "retry" and processed[rid].get("attempts", 0) < MAX_RETRIES)]
    batch, deferred = new[:max_per_run], new[max_per_run:]
    added, rejected, retrying = [], [], []
    for rid, sub in batch:
        try:
            job, note = evaluate(sub, rid, scraped, community, today, url_checker)
        except Rejected as r:
            if r.code == "unreachable":
                n = processed.get(rid, {}).get("attempts", 0) + 1
                if n < MAX_RETRIES:     # network hiccup: try again next run, don't log yet
                    processed[rid] = {"status": "retry", "attempts": n, "on": today.isoformat()}
                    retrying.append(rid); continue
            rejected.append({"id": rid, "timestamp": sub.get("timestamp", ""), "code": r.code, "reason": r.reason})
            processed[rid] = {"status": "rejected", "code": r.code, "on": today.isoformat()}
            continue
        community.append(job)
        added.append({"id": rid, "title": job["title"], "institution": job["institution"], "expires": job["expires"], "note": note})
        processed[rid] = {"status": "added", "on": today.isoformat()}
    if added: save_community(community, community_path)
    with open(processed_path, "w") as f: json.dump(processed, f, indent=1, sort_keys=True); f.write("\n")
    if rejected:
        with open(log_path, "a") as f:
            for r in rejected:   # reason + ids only; submitted text/URLs stay out of the public repo
                f.write(f"{today.isoformat()}\trow={r['id']}\tform_ts={r['timestamp']}\t{r['code']}\t{r['reason']}\n")
    stats = merge_files(jobs_path, community_path, today)
    return {"rows": len(rows), "new": len(new), "processed": len(batch), "deferred": len(deferred), "retrying": len(retrying),
            "added": added, "rejected": rejected, "merge": stats}

def summary_markdown(res):
    lines = [f"## Community intake", "",
             f"Rows in sheet: {res['rows']} · new: {res['new']} · processed this run: {res['processed']} · "
             f"deferred to next run (cap): {res['deferred']} · unreachable, will retry: {res.get('retrying', 0)}", "",
             f"### Added ({len(res['added'])})"]
    lines += [f"- {a['title'][:100]} @ {a['institution'][:60]} (expires {a['expires']})" + (f" — {a['note']}" if a["note"] else "")
              for a in res["added"]] or ["- none"]
    lines += ["", f"### Rejected ({len(res['rejected'])})"]
    lines += [f"- `{r['id']}` form timestamp {r['timestamp'] or '?'}: **{r['code']}**: {r['reason']}" for r in res["rejected"]] or ["- none"]
    if res.get("merge"): lines += ["", f"jobs.json: {res['merge']}"]
    return "\n".join(lines) + "\n"

def cmd_intake(args):
    if args.csv_file:
        text = open(args.csv_file, encoding="utf-8-sig").read()
    else:
        url = args.csv_url or os.environ.get("COMMUNITY_FORM_CSV_URL", "")
        if not url:
            print("COMMUNITY_FORM_CSV_URL is not set; nothing to do."); return 0
        if validate_url_syntax(url): print("COMMUNITY_FORM_CSV_URL is not a valid http(s) URL", file=sys.stderr); return 2
        text = fetch_csv(url)
    res = intake(text, max_per_run=args.max_per_run)
    md = summary_markdown(res)
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f: f.write(md)
    return 0

def cmd_merge(_args):
    print(json.dumps(merge_files()))
    return 0

def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    i = sp.add_parser("intake"); i.add_argument("--csv-url"); i.add_argument("--csv-file")
    i.add_argument("--max-per-run", type=int, default=MAX_PER_RUN); i.set_defaults(fn=cmd_intake)
    m = sp.add_parser("merge"); m.set_defaults(fn=cmd_merge)
    a = ap.parse_args(argv)
    return a.fn(a)

if __name__ == "__main__": sys.exit(main())
