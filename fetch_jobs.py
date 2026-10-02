#!/usr/bin/env python3
"""Fetch real EE/CS faculty & teaching postings from public RSS feeds -> jobs.json.
Sources: Chronicle of Higher Ed Jobs and Inside Higher Ed Careers (public keyword RSS + "Working from home"
location feed), plus public Workday career-site JSON for WGU and SNHU. Each posting page is fetched (cached in
./cache) to re-tag modality/rank from the full description.
No logins; polite delay between requests; robots.txt allows /jobsrss/."""
import json, re, time, html, sys, os, hashlib, urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

UA = "Mozilla/5.0 (EECS-faculty-jobs prototype; personal non-commercial use)"
SOURCES = {
    "Chronicle of Higher Education Jobs": "https://jobs.chronicle.com/jobsrss/",
    "Inside Higher Ed Careers": "https://careers.insidehighered.com/jobsrss/",
}
QUERIES = ["computer science", "electrical engineering", "computer engineering",
           "software engineering", "data science", "cybersecurity",
           "computer science lecturer", "computer science instructor",
           "online computer science", "remote computer science", "information technology faculty",
           # remote-focused
           "online adjunct computer science", "remote instructor computer", "online instructor information technology",
           "online cybersecurity faculty", "online electrical engineering", "online data science faculty",
           "online software engineering", "virtual faculty computer", "fully online faculty"]
WFH_LOCATION_ID = "20752010"   # Madgex location id for "Working from home" (both sites)
WORKDAY = [  # public Workday career-site JSON (robots.txt allows; no login)
    ("Western Governors University", "wgu", "wd5", "External"),
    ("Southern New Hampshire University", "snhu", "wd503", "External_Career_Site"),
]
WORKDAY_QUERIES = ["computer science", "information technology", "cybersecurity", "software", "data science",
                   "engineering", "instructor", "faculty", "adjunct"]
CACHE_DIR = "cache"
PAGES = 5
DELAY = 0.6

STATES = {"Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA","Colorado":"CO","Connecticut":"CT","Delaware":"DE","District of Columbia":"DC","Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY"}

FACULTY_RE = re.compile(r"\b(professor|professorship|lecturer|instructor|faculty|teaching|adjunct|educator|chair(ed)?\b|postdoctoral teaching)", re.I)
EXCLUDE_RE = re.compile(r"\b(dean|provost|president|chief|director|coordinator|manager|specialist|analyst|administrator|technician|assistant to|officer|counselor|advisor)\b", re.I)
EXCLUDE_UNLESS_FAC = re.compile(r"\b(dean|provost|president|chief)\b", re.I)
FIELD_PATTERNS = [
    ("Computer Eng", re.compile(r"computer engineering|electrical and computer|ECE\b|embedded|VLSI|computer architecture|hardware", re.I)),
    ("EE", re.compile(r"electrical engineering|electrical & computer|power systems|signal processing|electronics|photonics|semiconductor|circuits|communications engineering|\bEE\b", re.I)),
    ("CS", re.compile(r"computer science|computing|software|data science|cyber ?security|artificial intelligence|machine learning|\bAI\b|informatics|information technology|computer information|programming|robotics|\bCS\b|\bCSE\b|\bIT\b", re.I)),
]

RELATED_TITLE = re.compile(r"engineer|technolog|informatic|information|comput|data|cyber|software|STEM|physics|math|statistic|analytics|digital|game|network", re.I)

def fetch(url):
    return cached_get(url, max_age_h=2)


def rank_of(text):
    t = text.lower()
    if re.search(r"\badjunct\b|part[- ]time|per[- ]course|sessional", t): return "Adjunct"
    if re.search(r"lecturer|instructor|non[- ]tenure|teaching (assistant |associate )?(professor|faculty)|professor of (the )?practice|clinical|visiting|teaching track|career[- ]track|teaching-track", t): return "Instructor/Lecturer/NTT"
    if re.search(r"(associate|full)( or full)? professor|\bfull professor|tenured|endowed|distinguished|chair\b|rank open|open rank|all ranks|any rank", t):
        if re.search(r"assistant", t) and not re.search(r"open rank|rank open|all ranks", t): return "Tenure-track Assistant"
        return "Associate/Full/Tenured"
    if re.search(r"assistant professor|tenure[- ]track", t): return "Tenure-track Assistant"
    if re.search(r"\bprofessor\b", t): return "Associate/Full/Tenured" if "tenure" in t else "Other"
    return "Other"

def modality_of(text):
    t = text.lower()
    if re.search(r"remote(?! sensing)|online|working from home|work from home|virtual|distance (education|learning)|fully online|telework", t): return "Remote/Online"
    if re.search(r"on[- ]campus|in[- ]person|on[- ]site|onsite", t): return "On-campus"
    return "Unknown"

def field_of(text):
    for name, pat in FIELD_PATTERNS:
        if pat.search(text): return name
    return None

def parse_location(desc_lines):
    loc = desc_lines[-1] if desc_lines else ""
    loc = re.sub(r"\s*\((US|USA)\)\s*$", "", loc).strip()
    state = ""
    if re.search(r"District of Columbia|Washington,? D\.?C\.?\b", loc): state = "DC"
    else:
        for full, ab in STATES.items():
            if re.search(r"\b%s\b" % re.escape(full), loc): state = ab
    if not state:
        m = re.search(r"(?:,|\s)\s*([A-Z]{2})\b(?!.*[A-Z]{2}\b)", loc)
        if m and m.group(1) in STATES.values(): state = m.group(1)
    if not state and re.search(r"working from home|remote|online", loc, re.I): state = "Remote"
    return loc, state or "Other/Unknown"

def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------- full-page enrichment ----------
def cached_get(url, data=None, headers=None, max_age_h=72):
    """GET/POST with on-disk cache + polite delay on real network hits."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha1((url + (data.decode() if data else "")).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path, "rb") as f: return f.read()
    h = {"User-Agent": UA}; h.update(headers or {})
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=30) as r: body = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code in (406, 429, 500, 502, 503) and attempt < 2: time.sleep(10 * (attempt + 1)); continue
            raise
    with open(path, "wb") as f: f.write(body)
    time.sleep(DELAY)
    return body

def strip_html(s):
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s or "", flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", s))

def madgex_detail(url):
    """Return (description_text, jsonld_dict) from a Chronicle/IHE job page."""
    page = cached_get(url).decode("utf-8", "ignore")
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try: d = json.loads(m.group(1), strict=False)
        except Exception: continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return strip_html(d.get("description", "")), d
    return "", {}

# Phrases that mention "online" but say nothing about teaching modality
NOISE_RE = re.compile(r"(apply|applications?|submit\w*|complete\w*|upload\w*|register|posted|found|available|accessible|access(ed)?)\s+(\w+\s+){0,4}online"
                      r"|online\s+(application|applicant|portal|system|form|submission|at\b|via\b|through\b)|online-only application|\bonline at\b|onlinejobs|www\.\S+"
                      r"|not (be )?eligible for (routine )?remote work|remote work (option|arrangement|is not|policy|for positions)\w*|approved for remote work"
                      r"|(programs?|degrees?|campus|master'?s?)\s+(\w+\s+){0,3}fully[- ]online|(online and )?remote teaching tools|telework friendly:? no|not (a )?(remote|telework)\w*", re.I)
STRONG_REMOTE = re.compile(r"fully[- ]online(?![- ](\w+ ){0,2}(campus|programs?|degrees?|master|graduate|options?|university|college))|fully[- ]remote|100\s?% (remote|online)|remote (position|role|opportunit\w+|faculty|instructor|adjunct)"
                           r"|(position|role|job) is (fully |entirely |100% )?(remote|virtual|online)|work(ing)? from home|(can|may|will) work remotely"
                           r"|teach\w* (\w+ ){0,3}(exclusively|entirely|fully|only) online|online[- ]only (course|teaching|instruction)"
                           r"|this is a (\w+ )?(remote|virtual|online) (position|role)|remote/virtual|virtual (position|faculty|instructor)", re.I)
WEAK_REMOTE = re.compile(r"\bonline (course|class|program|degree|instruction|teaching|learning|environment|modality|format|section)s?\b|asynchronous"
                         r"|distance (education|learning)|virtual(ly)? (classroom|instruction|teaching)", re.I)
ONCAMPUS = re.compile(r"on[- ]campus|in[- ]person|on[- ]site|onsite|face[- ]to[- ]face|physically present|reside within|relocat\w+|commut(e|ing) to"
                      r"|research (program|laboratory|lab)|start[- ]?up (package|funds)|laboratory space", re.I)

def modality_full(title, location, desc, jsonld=None):
    """Return (modality, evidence) using title, location, structured data and full description."""
    loc_type = json.dumps((jsonld or {}).get("jobLocationType", "")) + json.dumps((jsonld or {}).get("applicantLocationRequirements", ""))
    if "TELECOMMUTE" in loc_type.upper(): return "Remote/Online", "jobLocationType=TELECOMMUTE"
    head = f"{title} | {location}"
    m = re.search(r"\b(remote(?! sensing)|online|virtual|working from home|work from home)\b", head, re.I)
    if m: return "Remote/Online", f"title/location: '{m.group(0)}'"
    if re.search(r"\b(on[- ]?site|on[- ]campus|in[- ]person)\b", head, re.I): return "On-campus", "title/location says on-site"
    text = NOISE_RE.sub(" ", desc or "")
    sr = STRONG_REMOTE.search(text)
    oc = ONCAMPUS.findall(text)
    if sr and not re.search(r"in[- ]person|on[- ]campus|on[- ]site|face[- ]to[- ]face|hybrid", text[max(0, sr.start()-150): sr.end()+150], re.I):
        return "Remote/Online", f"description: '{sr.group(0)}'"
    if oc:
        ev = sorted({(x if isinstance(x, str) else x[0]).lower() for x in re.findall(ONCAMPUS.pattern, text, re.I)} - {""})
        mm = ONCAMPUS.search(text)
        if re.search(r"research|start|laboratory|relocat", mm.group(0), re.I):
            return "On-campus", f"inferred (research-active/relocation role): '{mm.group(0)}'"
        return "On-campus", f"description: '{mm.group(0)}'"
    weak = {w.group(0).lower() for w in WEAK_REMOTE.finditer(text)}
    if len(weak) >= 2: return "Remote/Online", "description: " + ", ".join(sorted(weak)[:3])
    return "Unknown", ("no modality cues in full description" if desc else "no full description available")

def workday_jobs():
    out = []
    for inst, tenant, wd, site in WORKDAY:
        base = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
        paths = {}
        for q in WORKDAY_QUERIES:
            for off in range(0, 100, 20):
                try:
                    d = json.loads(cached_get(base + "/jobs", data=json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}).encode(),
                                              headers={"Content-Type": "application/json", "Accept": "application/json"}, max_age_h=12))
                except Exception as e:
                    print(f"WARN workday {tenant} {q}: {e}", file=sys.stderr); break
                for jp in d.get("jobPostings", []): paths[jp["externalPath"]] = jp
                if off + 20 >= d.get("total", 0): break
        kept = 0
        for path, jp in paths.items():
            title = jp["title"]
            if not FACULTY_RE.search(title) or EXCLUDE_UNLESS_FAC.search(title) or not field_of(title): continue
            try: info = json.loads(cached_get(base + path, headers={"Accept": "application/json"}))["jobPostingInfo"]
            except Exception as e:
                print(f"WARN workday detail {path}: {e}", file=sys.stderr); continue
            desc = strip_html(info.get("jobDescription", ""))
            loc = info.get("location", jp.get("locationsText", ""))
            locs, state = parse_location([loc])
            out.append({"title": title, "institution": inst, "location": loc, "state": state,
                        "posted": info.get("startDate", "")[:10], "source": f"{inst} careers (Workday)",
                        "sources": [f"{inst} careers (Workday)"], "url": f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{path}",
                        "alt_urls": [], "rank": rank_of(title + " " + desc[:2000]), "field": field_of(title),
                        "_desc": desc, "_jsonld": {"jobLocationType": "TELECOMMUTE"} if info.get("remoteType", "").lower().startswith("remote") else {},
                        "snippet": re.sub(r"\s+", " ", desc)[:300]})
            kept += 1
        print(f"Workday {inst}: {len(paths)} postings scanned, {kept} EE/CS faculty kept", file=sys.stderr)
    return out

STATE_ABBR = set(STATES.values())
REGIONS = {"Pacific Northwest": "WA OR ID AK", "West": "CA NV UT CO WY MT HI", "Southwest": "AZ NM TX OK",
           "Midwest": "ND SD NE KS MN IA MO WI IL IN MI OH", "Southeast": "AR LA MS AL GA FL SC NC TN KY VA WV",
           "Northeast/Mid-Atlantic": "ME NH VT MA RI CT NY NJ PA DE MD DC"}
def region_of(state):
    if state == "Remote": return "Remote"
    for r, sts in REGIONS.items():
        if state in sts.split(): return r
    return "International/Other"
def enrich(jobs):
    for i, j in enumerate(jobs):
        desc, ld = j.pop("_desc", None), j.pop("_jsonld", None)
        if desc is None:
            try: desc, ld = madgex_detail(j["url"])
            except Exception as e:
                print(f"WARN detail {j['url']}: {e}", file=sys.stderr); desc, ld = "", {}
        j["modality"], j["modality_evidence"] = modality_full(j["title"], j["location"], desc, ld)
        j["has_full_text"] = bool(desc)
        r = rank_of(j["title"])                       # title is most reliable
        if r == "Other" and desc: r = rank_of(j["title"] + " " + desc[:3000])
        j["rank"] = r
        if j["state"] in ("Other/Unknown", "") and ld:  # fill state from structured address
            locs = ld.get("jobLocation") or []
            locs = locs if isinstance(locs, list) else [locs]
            for L in locs:
                reg = ((L or {}).get("address") or {}).get("addressRegion", "")
                if reg in STATE_ABBR: j["state"] = reg; break
                if reg in STATES: j["state"] = STATES[reg]; break
        j["region"] = region_of(j["state"])
        if i % 50 == 0: print(f"enriched {i}/{len(jobs)}", file=sys.stderr)

def main():
    jobs, seen = [], {}
    for src, base in SOURCES.items():
        for q in QUERIES + ["__WFH__"]:
            for p in range(1, PAGES + 1):
                params = {"LocationId": WFH_LOCATION_ID, "page": p} if q == "__WFH__" else {"keywords": q, "countrycode": "US", "page": p}
                url = base + "?" + urllib.parse.urlencode(params)
                try:
                    root = ET.fromstring(fetch(url))
                except Exception as e:
                    print(f"WARN {src} '{q}' p{p}: {e}", file=sys.stderr); break
                items = root.findall("./channel/item")
                if not items: break
                for it in items:
                    raw_title = html.unescape(it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").strip()
                    desc = html.unescape(it.findtext("description") or "")
                    lines = [l.strip() for l in desc.splitlines() if l.strip()]
                    inst, _, title = raw_title.partition(": ")
                    if not title: title, inst = raw_title, ""
                    full = f"{title} {inst} {desc}"
                    if not FACULTY_RE.search(title): continue
                    if EXCLUDE_RE.search(title) and not re.search(r"professor|lecturer|instructor|faculty", title, re.I): continue
                    if EXCLUDE_UNLESS_FAC.search(title): continue
                    field = field_of(title) or ("Other" if field_of(full) and RELATED_TITLE.search(title) else None)  # title-based; desc-only match => Other/related
                    if not field: continue
                    try: posted = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).date().isoformat()
                    except Exception: posted = ""
                    loc, state = parse_location(lines)
                    clean_url = re.sub(r"\?TrackID.*$", "", link)
                    key = norm(inst) + "|" + norm(title)
                    if key in seen:
                        j = seen[key]
                        if src not in j["sources"]:
                            j["sources"].append(src); j["alt_urls"].append(clean_url)
                        continue
                    j = {"title": title, "institution": inst, "location": loc, "state": state,
                         "posted": posted, "source": src, "sources": [src], "url": clean_url, "alt_urls": [],
                         "rank": rank_of(title + " " + desc), "field": field,
                         "modality": modality_of(title + " " + desc),
                         "snippet": " ".join(lines[1:-1])[:300]}
                    seen[key] = j; jobs.append(j)
                # (polite delay is applied inside cached_get on real network hits)
            print(f"{src} | {q}: total so far {len(jobs)}", file=sys.stderr)
    import sources_extra
    extra, extra_stats = sources_extra.all_extra()
    for j in workday_jobs() + extra:
        key = norm(j["institution"]) + "|" + norm(j["title"])
        alt = norm(j["title"]) + "|" + norm(j["institution"])[:12]
        dup = seen.get(key) or next((x for x in jobs if norm(x["title"]) == norm(j["title"]) and norm(x["institution"])[:12] == norm(j["institution"])[:12]), None)
        if dup:
            if j["source"] not in dup["sources"]: dup["sources"].append(j["source"]); dup["alt_urls"].append(j["url"])
            continue
        seen[key] = j; jobs.append(j)
    enrich(jobs)
    jobs.sort(key=lambda j: j["posted"], reverse=True)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "sources": {**SOURCES, "CRA Career Center": "https://careercenter.cra.org/jobs", "IEEE Job Site": "https://jobs.ieee.org", "ACM Career Center": "https://jobs.acm.org", "University career sites (Workday, UC Recruit, UW Academic HR)": "https://ap.washington.edu/ahr/academic-jobs/"}, "count": len(jobs), "jobs": jobs}
    # Safety guard: never replace a good file with a drastically smaller result
    # (e.g. sources blocked from CI runners). Keep the old file and fail loudly.
    try:
        with open("jobs.json") as f: prev = json.load(f).get("count", 0)
    except Exception:
        prev = 0
    min_ratio = float(os.environ.get("MIN_KEEP_RATIO", "0.5"))
    if prev and len(jobs) < min_ratio * prev:
        print(f"ERROR: new count {len(jobs)} is under {min_ratio:.0%} of previous {prev}; keeping old jobs.json", file=sys.stderr)
        with open("jobs.new.json", "w") as f: json.dump(out, f, indent=1)
        sys.exit(2)
    with open("jobs.json", "w") as f: json.dump(out, f, indent=1)
    from collections import Counter
    print("extra source stats", extra_stats); print("count", len(jobs)); print(Counter(j["region"] for j in jobs)); print(Counter(j["source"] for j in jobs))
    print(Counter(j["rank"] for j in jobs)); print(Counter(j["modality"] for j in jobs)); print(Counter(j["field"] for j in jobs))

if __name__ == "__main__": main()
