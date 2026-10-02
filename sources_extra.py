"""Extra sources: university career systems (Workday, UC Recruit, UW Academic HR) and
professional-society boards (CRA Career Center, IEEE Job Site, ACM Career Center).
All public, no login, robots.txt-allowed paths only. Each function returns job dicts
with private _desc/_jsonld keys that fetch_jobs.enrich() consumes for tagging."""
import json, re, sys, html, time
from datetime import datetime

# Strong-EECS universities with a public, robots-allowed Workday career site (verified 2026-10-02).
WORKDAY_UNIS = [
    # (display name, tenant, wd host, site, default state)
    ("Washington State University", "wsu", "wd5", "WSU_Jobs", "WA"),
    ("Oregon State University", "oregonstate", "wd501", "OSU_Careers_Site", "OR"),
    ("Seattle University", "seattleu", "wd108", "SeattleUJobs", "WA"),
    ("University of Chicago", "uchicago", "wd5", "External", "IL"),
    ("Cornell University", "cornell", "wd1", "CornellCareerPage", "NY"),
    ("Northeastern University", "northeastern", "wd1", "careers", "MA"),
    ("Penn State University", "psu", "wd1", "PSU_Academic", "PA"),
    ("University of Texas at Austin", "utaustin", "wd1", "UTstaff", "TX"),
    ("University of Miami", "umiami", "wd1", "UMFaculty", "FL"),
    ("Brigham Young University", "byu", "wd1", "faculty-careers", "UT"),
    ("Santa Clara University", "scu", "wd1", "scu", "CA"),
    ("University of Virginia", "uva", "wd1", "uvajobs", "VA"),
    ("Miami University (Ohio)", "miamioh", "wd5", "miamioh-faculty", "OH"),
    ("University of Louisville", "uofl", "wd1", "UofLCareerSite", "KY"),
    ("Universities of Wisconsin (comprehensives)", "wisconsin", "wd1", "UW_Comprehensives", "WI"),
    ("Metropolitan State University of Denver", "msudenver", "wd1", "MSUDenver", "CO"),
    ("University of Tampa", "utampa", "wd1", "Faculty", "FL"),
    ("LSU New Orleans", "ulsuno", "wd1", "UniversityOfNewOrleans", "LA"),
]
WD_QUERIES = ["computer", "electrical", "professor", "lecturer"]  # kept small: Workday throttles (HTTP 406) bursts

UC_RECRUIT = [  # UC Recruit public "apply" listings (robots.txt only disallows /survey/take/)
    ("UC Berkeley", "aprecruit.berkeley.edu", "Berkeley, CA"),
    ("UCLA", "recruit.apo.ucla.edu", "Los Angeles, CA"),
    ("UC San Diego", "apol-recruit.ucsd.edu", "La Jolla, CA"),
    ("UC Irvine", "recruit.ap.uci.edu", "Irvine, CA"),
    ("UC Davis", "recruit.ucdavis.edu", "Davis, CA"),
    ("UC Santa Cruz", "recruit.ucsc.edu", "Santa Cruz, CA"),
    ("UC Riverside", "aprecruit.ucr.edu", "Riverside, CA"),
]
EECS_UNIT = re.compile(r"comput|electr|elec engr|\bEECS\b|\bECE\b|\bCSE\b|software|informatics|data science|cyber|robotics|information school|school of information|allen school|information technology|engineering & applied", re.I)

def _ctx():
    import fetch_jobs as F
    return F

def _field_from_desc(desc):
    d = (desc or "")[:2000].lower()
    if "electrical and computer engineering" in d or "computer engineering" in d: return "Computer Eng"
    if "computer science" in d or "computing" in d: return "CS"
    if "electrical engineering" in d: return "EE"
    return "Other"

def _job(F, title, inst, loc, posted, source, url, desc, jsonld=None, state=None):
    _, st = F.parse_location([loc])
    if state and st in ("Other/Unknown", ""): st = state
    return {"title": title, "institution": inst, "location": loc, "state": st, "posted": posted,
            "source": source, "sources": [source], "url": url, "alt_urls": [],
            "rank": F.rank_of(title), "field": F.field_of(title) or _field_from_desc(desc),
            "_desc": desc, "_jsonld": jsonld or {}, "snippet": re.sub(r"\s+", " ", desc or "")[:300]}

def _is_target(F, title, unit=""):
    if not F.FACULTY_RE.search(title) or F.EXCLUDE_UNLESS_FAC.search(title): return False
    if re.search(r"\b(postdoc|post-doc|postdoctoral|research (associate|scientist)|staff|student)\b", title, re.I) and not re.search(r"teaching", title, re.I): return False
    return bool(F.field_of(title) or (unit and EECS_UNIT.search(unit) and F.field_of(unit)))

# ---------------- Workday (university career sites) ----------------
def workday_unis():
    F = _ctx(); out = []; stats = {}
    for name, t, wd, site, st in WORKDAY_UNIS:
        base = f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}"; paths = {}
        try:
            for q in WD_QUERIES:
                for off in range(0, 60, 20):
                    d = json.loads(F.cached_get(base + "/jobs", data=json.dumps({"appliedFacets": {}, "limit": 20, "offset": off, "searchText": q}).encode(),
                                                headers={"Content-Type": "application/json", "Accept": "application/json"}, max_age_h=12))
                    for jp in d.get("jobPostings", []): paths[jp["externalPath"]] = jp
                    if off + 20 >= d.get("total", 0): break
        except Exception as e:
            print(f"WARN workday {t}: {e}", file=sys.stderr); stats[name] = f"error: {e}"; continue
        kept = 0
        for path, jp in paths.items():
            if not _is_target(F, jp["title"]): continue
            time.sleep(0.5)
            try: info = json.loads(F.cached_get(base + path, headers={"Accept": "application/json"}))["jobPostingInfo"]
            except Exception as e: print(f"WARN workday detail {t}{path}: {e}", file=sys.stderr); continue
            desc = F.strip_html(info.get("jobDescription", ""))
            loc = info.get("location") or jp.get("locationsText", "")
            ld = {"jobLocationType": "TELECOMMUTE"} if str(info.get("remoteType", "")).lower().startswith("remote") else {}
            out.append(_job(F, jp["title"], name, loc, (info.get("startDate") or "")[:10], f"{name} (Workday)",
                            f"https://{t}.{wd}.myworkdayjobs.com/en-US/{site}{path}", desc, ld, st)); kept += 1
        stats[name] = kept
        print(f"Workday {name}: scanned {len(paths)}, kept {kept}", file=sys.stderr)
    return out, stats

# ---------------- UC Recruit ----------------
def uc_recruit():
    F = _ctx(); out = []; stats = {}
    for name, host, loc in UC_RECRUIT:
        try: page = F.cached_get(f"https://{host}/apply", max_age_h=12).decode("utf-8", "ignore")
        except Exception as e:
            print(f"WARN UC {host}: {e}", file=sys.stderr); stats[name] = f"error: {e}"; continue
        kept = 0
        for tb in re.finditer(r'<tbody[^>]*data-section="([^"]*)"[^>]*>(.*?)</tbody>', page, re.S):
            unit = html.unescape(tb.group(1))
            for row in re.finditer(r'<tr id="(JPF\d+)"[^>]*>(.*?)</tr>', tb.group(2), re.S):
                jpf, cells = row.group(1), row.group(2)
                m = re.search(r"<div class='name'>\s*(.*?)\s*</div>", cells, re.S)
                title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
                if not title or not _is_target(F, title, unit): continue
                if not (F.field_of(title) or EECS_UNIT.search(unit)): continue
                od = re.search(r"Open\s+([A-Z][a-z]{2} \d{1,2}, \d{4})", re.sub(r"<[^>]+>", " ", cells))
                posted = datetime.strptime(od.group(1), "%b %d, %Y").date().isoformat() if od else ""
                url = f"https://{host}/{jpf}"
                try: desc = F.strip_html(F.cached_get(url).decode("utf-8", "ignore"))
                except Exception as e: print(f"WARN UC detail {url}: {e}", file=sys.stderr); desc = ""
                desc = re.sub(r"\s+", " ", desc)
                i = desc.find("Position overview"); desc = desc[i:] if i > 0 else desc
                j = _job(F, title, name, loc, posted, "UC Recruit", url, desc, state="CA")
                if j["field"] == "Other" and F.field_of(unit): j["field"] = F.field_of(unit)
                out.append(j); kept += 1
        stats[name] = kept
        print(f"UC Recruit {name}: kept {kept}", file=sys.stderr)
    return out, stats

# ---------------- University of Washington Academic HR ----------------
def uw_academic():
    F = _ctx(); out = []
    try: page = F.cached_get("https://ap.washington.edu/ahr/academic-jobs/", max_age_h=12).decode("utf-8", "ignore")
    except Exception as e:
        print(f"WARN UW: {e}", file=sys.stderr); return [], {"University of Washington": f"error: {e}"}
    for li in re.finditer(r'<li class="ap-job-item"([^>]*)>(.*?)</li>', page, re.S):
        attrs, body = li.group(1), li.group(2)
        g = lambda k: html.unescape((re.search(k + r'="([^"]*)"', attrs) or [None, ""])[1])
        title, dept, date = g("data-title"), g("data-dept"), g("data-date")
        if not _is_target(F, title, dept): continue
        if not (F.field_of(title) and not re.search(r"nursing|health", dept, re.I) or re.search(r"computer|electrical|computing|allen school", dept, re.I)): continue
        loc = html.unescape((re.search(r'ap-job-location[^>]*>([^<]*)', body) or [None, "Seattle, WA"])[1])
        det = re.search(r'href="(https://ap\.washington\.edu/ahr/position-details/\?job_id=\d+)"', body)
        url = det.group(1) if det else "https://ap.washington.edu/ahr/academic-jobs/"
        try: desc = re.sub(r"\s+", " ", F.strip_html(F.cached_get(url).decode("utf-8", "ignore"))) if det else ""
        except Exception: desc = ""
        j = _job(F, title, "University of Washington" + (f" ({dept})" if dept else ""), loc, date[:10], "UW Academic HR", url, desc, state="WA")
        if j["field"] == "Other" and F.field_of(dept): j["field"] = F.field_of(dept)
        out.append(j)
    print(f"UW Academic HR: kept {len(out)}", file=sys.stderr)
    return out, {"University of Washington": len(out)}

# ---------------- JSON-LD job pages (CRA / IEEE / ACM) ----------------
def jsonld_job(F, url):
    page = F.cached_get(url).decode("utf-8", "ignore")
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try: d = json.loads(m.group(1), strict=False)
        except Exception: continue
        items = d.get("@graph", [d]) if isinstance(d, dict) else d
        for it in items if isinstance(items, list) else [items]:
            if isinstance(it, dict) and it.get("@type") == "JobPosting": return it
    return None

def _ld_to_job(F, ld, url, source):
    title = html.unescape(ld.get("title", "")).strip()
    org = ld.get("hiringOrganization") or {}
    inst = html.unescape(org.get("name", "") if isinstance(org, dict) else str(org))
    locs = ld.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    addr = ((locs[0] or {}).get("address") or {}) if locs else {}
    if isinstance(addr, str): addr = {"addressLocality": addr}
    loc = ", ".join(x for x in [addr.get("addressLocality", ""), addr.get("addressRegion", ""), addr.get("addressCountry", "") if isinstance(addr.get("addressCountry", ""), str) else ""] if x)
    if "TELECOMMUTE" in json.dumps(ld.get("jobLocationType", "")).upper() and not loc: loc = "Remote"
    j = _job(F, title, inst, loc, (ld.get("datePosted") or "")[:10], source, url, F.strip_html(ld.get("description", "")), ld)
    reg = addr.get("addressRegion", "")
    if j["state"] == "Other/Unknown" and reg in F.STATES: j["state"] = F.STATES[reg]
    if j["state"] == "Other/Unknown" and reg in F.STATE_ABBR: j["state"] = reg
    return j

def cra_career_center():
    F = _ctx(); out = []
    try: sm = F.cached_get("https://careercenter.cra.org/sitemap_active_jobs.xml", max_age_h=12).decode()
    except Exception as e: print(f"WARN CRA: {e}", file=sys.stderr); return [], {"CRA Career Center": f"error: {e}"}
    urls = re.findall(r"<loc>(https://careercenter\.cra\.org/job/[^<]+)</loc>", sm)
    for u in urls:
        slug = u.rsplit("/", 1)[-1].replace("-", " ")
        if not F.FACULTY_RE.search(slug): continue
        try: ld = jsonld_job(F, u)
        except Exception as e: print(f"WARN CRA {u}: {e}", file=sys.stderr); continue
        if not ld: continue
        j = _ld_to_job(F, ld, u, "CRA Career Center")
        if j["field"] == "Other" and not re.search(r"comput|electr|data|cyber|software|AI\b|robot", j["title"] + " " + j["_desc"][:600], re.I): continue
        if not F.FACULTY_RE.search(j["title"]) or F.EXCLUDE_UNLESS_FAC.search(j["title"]): continue
        out.append(j)
    print(f"CRA Career Center: {len(urls)} active ads, kept {len(out)}", file=sys.stderr)
    return out, {"CRA Career Center": len(out)}

def ym_board(name, host, keywords=("professor", "lecturer", "faculty", "instructor"), pages=3):
    """IEEE Job Site / ACM Career Center (YM Careers): keyword result pages -> JSON-LD job pages."""
    F = _ctx(); out = []; links = set()
    for kw in keywords:
        for p in range(1, pages + 1):
            try: page = F.cached_get(f"https://{host}/jobs/results/keyword/{kw}?page={p}", max_age_h=12).decode("utf-8", "ignore")
            except Exception as e: print(f"WARN {name} {kw} p{p}: {e}", file=sys.stderr); break
            found = set(re.findall(r'href="(/job/[^"]+/\d+/)"', page))
            if not found - links: break
            links |= found
    for path in sorted(links):
        slug = path.split("/")[2].replace("-", " ")
        if not F.FACULTY_RE.search(slug): continue
        u = f"https://{host}{path}"
        try: ld = jsonld_job(F, u)
        except Exception as e: print(f"WARN {name} {u}: {e}", file=sys.stderr); continue
        if not ld: continue
        j = _ld_to_job(F, ld, u, name)
        if not _is_target(F, j["title"]) and not (F.FACULTY_RE.search(j["title"]) and re.search(r"comput|electr|software|cyber|data science", j["_desc"][:800], re.I)): continue
        if j["field"] == "Other" and not F.RELATED_TITLE.search(j["title"]) and not re.search(r"comput|electr", j["_desc"][:800], re.I): continue
        out.append(j)
    print(f"{name}: {len(links)} links, kept {len(out)}", file=sys.stderr)
    return out, {name: len(out)}

def all_extra():
    jobs, stats = [], {}
    for fn in (workday_unis, uc_recruit, uw_academic, cra_career_center,
               lambda: ym_board("IEEE Job Site", "jobs.ieee.org"), lambda: ym_board("ACM Career Center", "jobs.acm.org")):
        try: j, s = fn()
        except Exception as e:
            print(f"WARN extra source failed: {e}", file=sys.stderr); continue
        jobs += j; stats.update(s)
    return jobs, stats
