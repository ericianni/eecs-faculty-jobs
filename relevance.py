"""EECS relevance filter applied to every listing (all sources) before enrichment.

Include terms are built from Oregon State University EECS degree programs and research
areas (https://engineering.oregonstate.edu/EECS/academics and /EECS/research/*), so
listings in those areas are kept even when they sit in another department (e.g.
"Robotics in Mechanical Engineering", "AI for Biology - EECS", "Quantum Computing").
Unrelated fields (astrophysics, chemistry, pharmacy, ...) are dropped.

classify(job) -> (keep: bool, reason: str)
"""
import re

# ---- Strong EECS terms, grouped by OSU EECS program / research area ----
OSU_EECS_AREAS = {
    # Degree programs
    "Computer Science": r"computer science|\bCS\b|\bCSE\b|\bEECS\b|school of computing|computing (and|&) |computing science|computer & communication|computer and communication",
    "Electrical & Computer Engineering": r"electrical|\bECE\b|\bEE\b|\bEET\b|computer engineering|electronics|computer (and|&) electrical",
    "Artificial Intelligence": r"artificial intelligence|machine learning|deep learning|\bAI\b|\bML\b|computer vision|natural language|\bNLP\b|computational linguistics|intelligent systems",
    "Robotics": r"robot|autonomous systems|autonomy|mechatronic|embodied",
    "Cybersecurity": r"cyber|security|cryptograph|privacy|digital forensics",
    "Semiconductors / Materials Science": r"semiconductor|microelectronic|nanoelectronic|\bVLSI\b|materials science",
    # Research areas
    "Communications & Signal Processing": r"signal processing|communications? (engineering|systems|theory)|wireless|information theory|coding theory|\bRF\b|microwave|antenna|electromagnetic|controls?\b|control systems",
    "Computer Graphics & Visualization": r"computer graphics|visuali[sz]ation|image processing|virtual reality|\bGPU\b|game (design|development|programming)",
    "Computer Science Education": r"computing education|computer science education|computational thinking",
    "Data Science & Engineering": r"data science|data engineering|databases?|data mining|big data",
    "Electronic Materials & Devices": r"photonic|optoelectronic|photovoltaic|spintronic|electronic (materials|devices)|nanodevice|sensors?\b",
    "Energy Systems": r"power (systems|electronics|engineering)|smart grid|electric (machines|drives)|energy systems|renewable energy|transportation electrification",
    "Health Engineering": r"bioinformatic|computational biology|neural engineering|bioelectronic|biosensor|medical devices?|biomedical (signal|imaging|AI)|biomedical AI",
    "Integrated Electronics": r"integrated circuits?|circuits?\b|analog|mixed[- ]signal|\bIC design|embedded|hardware",
    "Networking & Computer Systems": r"network(s|ing)\b|computer systems|distributed systems|cloud computing|high[- ]performance computing|\bHPC\b|parallel computing|computer architecture|operating systems|internet of things|\bIoT\b|computer systems",
    "Programming Languages": r"programming|software|compilers?|type systems",
    "Software Engineering & HCI": r"software engineering|human[- ]computer|\bHCI\b|user experience",
    "Theoretical Computer Science": r"algorithms|theory of computation|computational geometry|quantum (computing|computation|information)",
    # Adjacent/applied computing taught in EECS-type units (community colleges, CIS, IT)
    "Information Technology / CIS": r"information technology|\bIT\b|computer information|\bCIS\b|\bCIT\b|computer (and|&) information|information systems|informatics|computing",
}
# Upper-case acronyms (\bIT\b, \bAI\b, \bEE\b ...) are matched case-SENSITIVELY so that the words
# "it", "ai", "ee" in ordinary text don't count (the old FIELD_PATTERNS used re.I for these).
def _split(p):
    """Split an alternation into (case-insensitive words, case-sensitive acronyms like \\bIT\\b)."""
    parts = p.split("|")
    acr = [x for x in parts if re.fullmatch(r"\\b[A-Z][A-Za-z]*\\b", x) and sum(c.isupper() for c in x) >= 2]
    return [x for x in parts if x not in acr], acr
_W, _A = [], []
for _p in OSU_EECS_AREAS.values():
    w, a = _split(_p); _W += w; _A += a
STRONG_WORD_RE = re.compile("|".join(_W), re.I)
STRONG_ACRO_RE = re.compile("|".join(_A))
# Terms that are EECS-adjacent but weak on their own (kept when nothing off-topic is present)
WEAK_RE = re.compile(r"computational|data analytics|analytics|digital|technology|engineering|STEM\b|quantum", re.I)
# Strong terms that are too generic to override an off-topic field on their own.
# e.g. "Health Information Technology" (medical records), "Philosophy of AI", "Social Work, AI".
WEAK_OVERRIDE_RE = re.compile(r"^(it|ai|ml|controls?|sensors?|security|privacy|computing|informatics|information systems|networks?|analog|algorithms|databases?|data science|data mining|big data)$", re.I)

# Fields that are never EECS (drop unless a real, specific EECS term is present)
HARD_OFF_RE = re.compile(
    r"astro|astronom|cosmolog|chemi|pharma|nursing|\bnurse|fire science|social work|philosoph|journalism|anesthes|medicine\b|"
    r"health information technology|health (information|informatics) management|\bHIT\b|dental|veterinar|kinesiolog|"
    r"accounting|marketing|finance\b|music|theat(er|re)|literature|spanish|religio|theolog|law\b|criminal|"
    r"government|political|public policy|policy\b|africana|sociology|psycholog|education leadership|culinary|cosmetolog|"
    r"youth information|cultural heritage|library science|archiv", re.I)
# Science/engineering neighbours: drop unless an EECS term (incl. AI/ML/data science) is also present
SOFT_OFF_RE = re.compile(
    r"physics|biology|biomedical|clinical|life science|biochem|molecular|genetic|ecolog|geolog|geograph|geospatial|\bGIS\b|GIScience|"
    r"mathematic|\bmath\b|statistic|biostatistic|actuarial|"
    r"mechanical engineering|civil engineering|construction|industrial engineering|chemical engineering|biomedical engineering|"
    r"aerospace|nuclear|environmental engineering|biomaterial|biomechanic|natural sciences|"
    r"business analytics|business information|management sciences|operations\b|\bbusiness\b", re.I)
# Exceptions where an off-topic word is part of an EECS-ish phrase
OFF_EXCEPT_RE = re.compile(r"engineering physics|applied physics and electrical|computational biology|computational physics|"
                           r"mathematics (and|&) computer science|math(ematics)? (and|&) computer|computer science and mathematics|"
                           r"engineering technology", re.I)

EECS_BOARDS = {"CRA Career Center", "IEEE Job Site", "ACM Career Center"}


# Phrases containing an EECS word that are NOT EECS (medical records coding, etc.)
NOT_EECS_PHRASES = re.compile(r"health information (technology|management)|health informatics|\bHIT\b", re.I)

def _strong_terms(text):
    text = NOT_EECS_PHRASES.sub(" ", text)
    return {m.group(0).strip().lower() for r in (STRONG_WORD_RE, STRONG_ACRO_RE) for m in r.finditer(text)}

def area_of(text):
    """First OSU EECS area whose terms match (for reporting)."""
    text = NOT_EECS_PHRASES.sub(" ", text)
    for area, pat in OSU_EECS_AREAS.items():
        w, a = _split(pat)
        if (w and re.search("|".join(w), text, re.I)) or (a and re.search("|".join(a), text)): return area
    return None



def classify(job):
    title = job.get("title", "")
    inst = job.get("institution", "")
    head = f"{title} | {inst}"                          # institution can carry a dept, e.g. "UW (Mechanical Engineering)"
    snippet = job.get("snippet", "") or ""
    strong = _strong_terms(head)
    specific = {t for t in strong if not WEAK_OVERRIDE_RE.match(t)}
    off_src = OFF_EXCEPT_RE.sub(" ", head)
    hard, soft = HARD_OFF_RE.search(off_src), SOFT_OFF_RE.search(off_src)
    if hard:
        if specific: return True, f"keep: off-topic '{hard.group(0)}' but EECS area {area_of(head)} ({sorted(specific)[0]})"
        return False, f"drop: non-EECS field '{hard.group(0)}'"
    if soft:
        if strong: return True, f"keep (borderline): '{soft.group(0)}' + EECS area {area_of(head)} ({sorted(strong)[0]})"
        return False, f"drop: non-EECS field '{soft.group(0)}' with no EECS area term"
    if strong: return True, f"keep: EECS area {area_of(head)}"
    # Generic title ("Assistant Professor", "Lecturer - 2026-27 AY"): look at snippet
    s_off = HARD_OFF_RE.search(OFF_EXCEPT_RE.sub(" ", snippet)) or SOFT_OFF_RE.search(OFF_EXCEPT_RE.sub(" ", snippet))
    s_strong = _strong_terms(snippet)
    if s_strong and not s_off: return True, f"keep: generic title, snippet says {area_of(snippet)}"
    if s_off and not (s_strong - {"ai", "it"}): return False, f"drop: generic title, snippet says '{s_off.group(0)}'"
    if job.get("source") in EECS_BOARDS or job.get("source") == "UC Recruit" or job.get("field") not in (None, "Other"):
        return True, "keep: generic title from EECS-specific source/unit"
    if re.search(r"engineering", head, re.I): return True, "keep (borderline): generic engineering"
    if WEAK_RE.search(head): return True, f"keep (borderline): weak term '{WEAK_RE.search(head).group(0)}'"
    return False, "drop: no EECS evidence"


def filter_jobs(jobs, log=None):
    kept, dropped = [], []
    for j in jobs:
        ok, why = classify(j)
        (kept if ok else dropped).append(j)
        if not ok and log: log(f"RELEVANCE {why}: {j['title'][:90]} @ {j['institution'][:50]}")
    return kept, dropped
