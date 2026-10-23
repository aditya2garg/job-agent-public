"""Turn raw posting text into structured signals: salary, location, seniority, years, skills, flags."""

from __future__ import annotations

import bisect
import html
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .models import Job
from .taxonomy import SKILLS

# --------------------------------------------------------------------------- text


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    if "<" not in raw and "&lt;" in raw:
        raw = html.unescape(raw)
    elif "&lt;" in raw and "<div" not in raw and "<p" not in raw:
        raw = html.unescape(raw)
    soup = BeautifulSoup(raw, "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr"]):
        tag.append("\n")
    text = soup.get_text()
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- salary


@dataclass
class Salary:
    low: float | None = None
    high: float | None = None
    raw: str | None = None
    multiple: bool = False
    source: str = "none"  # structured | text | none

    @property
    def known(self) -> bool:
        return self.low is not None and self.high is not None

    @property
    def mid(self) -> float | None:
        return (self.low + self.high) / 2 if self.known else None


_NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
_RANGE_RE = re.compile(
    r"(?:USD\s*)?\$\s?(?P<a>" + _NUM + r")\s?(?P<ak>[kK]\b)?\s*(?:USD)?\s*(?:-|–|—|to|and|through)\s*(?:USD\s*)?\$?\s?(?P<b>"
    + _NUM
    + r")\s?(?P<bk>[kK]\b)?(?P<tail>[^\n]{0,24})",
    re.IGNORECASE,
)
_HOURLY_RE = re.compile(r"/\s?(hr|hour)|per hour|hourly", re.IGNORECASE)
_UPTO_RE = re.compile(r"up to\s*\$\s?(?P<a>" + _NUM + r")\s?(?P<ak>[kK]\b)?", re.IGNORECASE)


def _to_num(num: str, k: str | None) -> float:
    v = float(num.replace(",", ""))
    if k:
        v *= 1000
    return v


def parse_salary(text: str, location_hints: tuple[str, ...] = ()) -> Salary:
    """Find the pay range in free text. With several ranges (geo tiers), prefer the one whose
    surrounding context mentions a preferred location, else the highest."""
    if not text:
        return Salary()
    found: list[tuple[float, float, str, str]] = []
    for m in _RANGE_RE.finditer(text):
        a, b = _to_num(m["a"], m["ak"]), _to_num(m["b"], m["bk"])
        if m["ak"] and not m["bk"] and b < 1000:  # "$150k - 200"
            b *= 1000
        tail = m["tail"] or ""
        window = text[max(0, m.start() - 40) : m.end() + 24]
        hourly = bool(_HOURLY_RE.search(tail) or _HOURLY_RE.search(window))
        if hourly:
            a, b = a * 2080, b * 2080
        if a > b:
            a, b = b, a
        if not (40_000 <= a <= 1_500_000 and 40_000 <= b <= 1_500_000):
            continue
        if b > a * 3.2:  # "$100 - $2,000,000" style noise
            continue
        context = text[max(0, m.start() - 180) : m.start()].lower()
        found.append((a, b, m.group(0).strip(), context))
    if not found:
        return Salary()
    deduped: dict[tuple[float, float], tuple[float, float, str, str]] = {}
    for item in found:
        deduped.setdefault((item[0], item[1]), item)
    items = list(deduped.values())
    pick = None
    if len(items) > 1 and location_hints:
        for it in items:
            if any(h in it[3] for h in location_hints):
                pick = it
                break
    if pick is None:
        pick = max(items, key=lambda it: it[1])
    return Salary(pick[0], pick[1], pick[2], multiple=len(items) > 1, source="text")


# --------------------------------------------------------------------------- location

BAY_AREA = [
    "san francisco", "south san francisco", "bay area", "palo alto", "mountain view", "sunnyvale", "san jose",
    "santa clara", "menlo park", "redwood city", "san mateo", "oakland", "berkeley", "fremont", "cupertino",
    "los altos", "foster city", "emeryville", "burlingame", "milpitas", "los gatos", "campbell", "pleasanton",
    "san carlos", "belmont, ca", "hayward", "walnut creek", "alameda", "mill valley", "sausalito", "san bruno",
    "daly city", "brisbane, ca", "san rafael", "petaluma", "dublin, ca", "livermore", "santa cruz", "half moon bay",
]
AUSTIN = ["austin", "round rock", "cedar park", "pflugerville", "georgetown, tx", "leander, tx", "san marcos, tx"]
NON_US = [
    "london", "united kingdom", "uk", "dublin", "ireland", "toronto", "canada", "vancouver", "montreal", "ottawa",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "gurgaon", "chennai", "singapore",
    "tokyo", "japan", "seoul", "korea", "china", "beijing", "shanghai", "hong kong", "taiwan", "taipei", "sydney",
    "melbourne", "australia", "new zealand", "auckland", "berlin", "germany", "munich", "paris", "france", "amsterdam",
    "netherlands", "madrid", "spain", "barcelona", "lisbon", "portugal", "italy", "milan", "zurich", "switzerland",
    "stockholm", "sweden", "copenhagen", "denmark", "oslo", "norway", "helsinki", "finland", "warsaw", "poland",
    "prague", "czech", "vienna", "austria", "budapest", "bucharest", "romania", "tel aviv", "israel", "dubai", "uae",
    "emea", "apac", "latam", "europe", "brazil", "sao paulo", "são paulo", "mexico", "argentina", "colombia", "chile",
    "philippines", "manila", "vietnam", "indonesia", "jakarta", "malaysia", "thailand", "bangkok", "pakistan",
    "nigeria", "lagos", "south africa", "cape town", "kenya", "egypt", "turkey", "istanbul", "ukraine", "kyiv",
    "belgium", "brussels", "luxembourg", "greece", "athens", "estonia", "latvia", "lithuania", "serbia", "croatia",
]
US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
US_CITIES = [
    "new york", "nyc", "brooklyn", "seattle", "bellevue", "boston", "cambridge, ma", "chicago", "los angeles",
    "san diego", "denver", "boulder", "atlanta", "miami", "dallas", "houston", "washington, dc", "portland",
    "salt lake", "raleigh", "durham", "pittsburgh", "philadelphia", "phoenix", "nashville", "minneapolis",
    "detroit", "columbus", "united states", "usa", "u.s.", "us-", "nationwide",
]
_US_STATE_RE = re.compile(r",\s*(" + "|".join(US_STATES) + r")\b")
_US_ELIGIBLE_REMOTE = re.compile(
    r"\b(us|u\.s\.|usa|united states|americas|north america|us-based|worldwide|anywhere|global)\b", re.IGNORECASE
)


@dataclass
class LocationInfo:
    kind: str  # bay_area | austin | remote_us | us_other | non_us | unknown
    remote: bool
    matched: str = ""
    detail: str = ""
    all_kinds: list[str] = field(default_factory=list)

    @property
    def us_eligible(self) -> bool:
        return self.kind != "non_us"


_KIND_RANK = {"bay_area": 0, "austin": 1, "remote_us": 2, "us_other": 3, "unknown": 4, "non_us": 5}


def _has(term: str, hay: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", hay) is not None


def _classify_part(part: str, remote_flag: bool) -> tuple[str, bool]:
    p = part.lower().strip()
    is_remote = remote_flag or bool(re.search(r"\bremote|work from home|anywhere|distributed\b", p))
    if any(_has(t, p) for t in BAY_AREA) or re.search(r"\bsf\b|\bsfo\b", p):
        return "bay_area", is_remote
    if any(_has(t, p) for t in AUSTIN):
        return "austin", is_remote
    non_us = any(_has(t, p) for t in NON_US)
    us_signal = bool(_US_ELIGIBLE_REMOTE.search(p)) or bool(_US_STATE_RE.search(part)) or any(c in p for c in US_CITIES)
    if is_remote:
        if non_us and not us_signal:
            return "non_us", True
        return "remote_us", True
    if non_us and not us_signal:
        return "non_us", False
    if us_signal:
        return "us_other", False
    return "unknown", False


def classify_location(location: str, remote: bool | None = None, description: str = "") -> LocationInfo:
    loc = (location or "").strip()
    parts = [p for p in re.split(r"\s*(?:\||;|\n|\bor\b|/(?=\s))\s*", loc) if p.strip()] or [loc]
    # "San Francisco, CA, New York, NY" style lists: also split on state suffixes
    if len(parts) == 1 and len(_US_STATE_RE.findall(loc)) > 1:
        parts = [p.strip() for p in re.split(r"(?<=\b[A-Z]{2}),\s*", loc) if p.strip()]
    results = [(_classify_part(p, bool(remote)), p) for p in parts]
    kinds = [r[0][0] for r in results]
    best = min(results, key=lambda r: _KIND_RANK[r[0][0]])
    kind, is_remote = best[0]
    matched = best[1]
    head = description[:1500].lower() if description else ""

    if kind == "unknown" and remote:
        kind, is_remote = "remote_us", True
    if kind in ("unknown", "us_other") and re.search(r"fully remote|100% remote|remote[- ]first|work from anywhere", head):
        if not re.search(r"remote.{0,40}(canada|uk\b|emea|europe|india|latam)", head):
            kind, is_remote = "remote_us", True
    if kind == "remote_us" and description:
        restricted = re.search(
            r"(must|need to|required to) (be|reside|live|located) (in|within) (the )?(canada|uk|united kingdom|europe|eu|india|germany|australia|emea|brazil)",
            head,
        )
        if restricted:
            kind = "non_us"
    detail = ""
    if is_remote and kind in ("bay_area", "austin"):
        detail = "remote or hybrid option listed"
    return LocationInfo(kind=kind, remote=is_remote, matched=matched, detail=detail, all_kinds=kinds)


# --------------------------------------------------------------------------- seniority / years

_YEARS_RE = re.compile(
    r"(?P<n>\d{1,2})\s?\+?\s*(?:-\s*\d{1,2}\s*)?(?:or more\s+)?(?:years?|yrs?)(?:'?\s+of)?(?P<ctx>[^.\n]{0,90})", re.IGNORECASE
)


def years_required(text: str) -> tuple[float | None, str]:
    """Largest 'N+ years' requirement and a short family label (ai | data | software | general)."""
    best: tuple[float, str] | None = None
    for m in _YEARS_RE.finditer(text or ""):
        n = float(m["n"])
        if n > 20 or n < 1:
            continue
        ctx = m["ctx"].lower()
        window = text[max(0, m.start() - 60) : m.end()].lower()
        if re.search(r"\b(llm|ai\b|machine learning|ml\b|generative|nlp|deep learning)", ctx + window[-80:]):
            fam = "ai"
        elif re.search(r"data|sql|etl|analytics|warehouse|pipeline", ctx):
            fam = "data"
        elif re.search(r"software|engineer|develop|backend|programming|python", ctx):
            fam = "software"
        else:
            fam = "general"
        # Take the largest requirement; ties broken in favour of more specific families.
        if best is None or n > best[0]:
            best = (n, fam)
    return (best[0], best[1]) if best else (None, "general")


NEGATIVE_FLAGS: list[tuple[str, str, str]] = [
    ("clearance", r"security clearance|ts/sci|top secret|secret clearance|active clearance|dod clearance", "Requires a US security clearance"),
    ("citizenship", r"u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|citizens? only|\bitar\b", "US citizenship required"),
    ("phd", r"\bph\.?d\.?\b (required|is required|in (computer|machine|statistics|physics|ai))|doctorate (required|in)|requires a ph\.?d", "PhD required or strongly preferred"),
    ("contract", r"\bcontract(or)?\b (position|role|basis)|\b(6|12|3)[- ]month contract|\bC2C\b|corp[- ]to[- ]corp|1099|staffing|\bw2 contract", "Contract / staffing arrangement"),
    ("onsite5", r"5 days (a|per) week (in|at) (the )?office|fully on-?site|in-?office five days|onsite 5 days", "On-site 5 days/week"),
    ("relocation", r"relocation (is )?required|must relocate", "Relocation required"),
    ("travel", r"(\d{2,3})% travel|travel up to (\d{2,3})%", "Significant travel"),
    ("visa", r"(no|not) (able to )?(offer|provide|sponsor)[^.\n]{0,25}(visa|sponsorship)|without (the )?need for (visa )?sponsorship|unable to sponsor", "No visa sponsorship"),
]


def find_flags(text: str) -> list[tuple[str, str]]:
    low = (text or "").lower()
    out: list[tuple[str, str]] = []
    for key, pattern, msg in NEGATIVE_FLAGS:
        if re.search(pattern, low, re.IGNORECASE):
            out.append((key, msg))
    return out


# --------------------------------------------------------------------------- skills

_NICE_HDR = re.compile(
    r"nice[- ]to[- ]have|bonus( points)?|preferred( qualifications| skills)?|a plus|plus:|ideally|extra credit|"
    r"would be (great|nice)|it'?s a plus|good to have|desirable|even better|stand out",
    re.IGNORECASE,
)
_REQ_HDR = re.compile(
    r"requirements?|qualifications?|what you('|’)?ll bring|what we('|’)?re looking for|who you are|must[- ]have|"
    r"minimum|you have|you('|’)?ve|about you|your background|skills (and|&) experience|experience:|you will have|you bring",
    re.IGNORECASE,
)
_RESP_HDR = re.compile(
    r"responsibilit|what you('|’)?ll do|the role|in this role|day[- ]to[- ]day|about the (role|job|team)|your impact|what you will|you will",
    re.IGNORECASE,
)
_COMPANY_HDR = re.compile(r"benefits|perks|about us|who we are|our (mission|values)|compensation|equal opportunity|why join", re.IGNORECASE)

SECTION_MULT = {"required": 1.5, "general": 1.0, "nice": 0.5, "company": 0.35}


@dataclass
class SkillHit:
    id: str
    mentions: int
    in_title: bool
    mult: float  # strongest section multiplier among mentions
    section: str


def _sections(text: str) -> list[tuple[int, str]]:
    """Walk the text line by line, tracking whether we're in requirements / nice-to-have / etc.
    Returns (start_offset, mode) pairs, one per non-empty line."""
    mode = "general"
    out: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        start = pos
        pos += len(line) + 1
        s = line.strip()
        if not s:
            continue
        is_heading = len(s) < 70 and not s.startswith("•") and (s.endswith(":") or s.isupper() or len(s.split()) <= 7)
        if is_heading:
            if _NICE_HDR.search(s):
                mode = "nice"
            elif _COMPANY_HDR.search(s):
                mode = "company"
            elif _REQ_HDR.search(s):
                mode = "required"
            elif _RESP_HDR.search(s):
                mode = "general"
            out.append((start, mode))
            continue
        line_mode = mode
        if len(s) < 220 and _NICE_HDR.search(s):
            line_mode = "nice"
        out.append((start, line_mode))
    return out


def extract_skills(title: str, text: str) -> dict[str, SkillHit]:
    hits: dict[str, SkillHit] = {}
    text = text or ""
    sections = _sections(text)
    starts = [st for st, _ in sections]
    for skill in SKILLS:
        in_title = bool(skill.pattern.search(title or ""))
        count, best_mult, best_section = 0, 0.0, "general"
        for m in skill.pattern.finditer(text):
            count += 1
            mode = sections[max(0, bisect.bisect_right(starts, m.start()) - 1)][1] if sections else "general"
            mult = SECTION_MULT[mode]
            if mult > best_mult:
                best_mult, best_section = mult, mode
        if count or in_title:
            if in_title:
                best_mult, best_section = max(best_mult, 1.5), "required"
            hits[skill.id] = SkillHit(skill.id, count, in_title, best_mult or 1.0, best_section)
    return hits


def job_location_hints(job: Job) -> tuple[str, ...]:
    info = classify_location(job.location, job.remote)
    if info.kind == "bay_area":
        return ("san francisco", "bay area", "california", "zone 1", "tier 1", "sf ")
    if info.kind == "austin":
        return ("austin", "texas", "zone 2", "tier 2")
    return ("remote", "united states", "u.s.", "us ")
