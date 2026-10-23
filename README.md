# jobhunt

Scrapes AI / data engineering job postings and scores each one against **your resume, your target roles, your pay floor and your location preferences** with an explainable match model. Comes with a CLI and a local dashboard.

Tuned out of the box for a sample candidate's search:

| | |
|---|---|
| Preferred roles | AI Engineer, Agentic AI Engineer, Analytics Engineer (AI) |
| Long-shot roles | Data Engineer, Software Engineer (judged on what the JD actually requires) |
| Locations | SF Bay Area, Austin, Remote (US-eligible) |
| Total comp | prefer **$220k+**, minimum **$170k** |
| Freshness | only postings published in the **last 7 days** |
| Visa | optional **H-1B sponsorship** scoring: roles/companies that won't sponsor are scored far lower |

## Quick start

```bash
uv venv --seed .venv && uv pip install --python .venv/bin/python -e ".[dev]"   # or: python -m venv .venv && pip install -e ".[dev]"
source .venv/bin/activate

jobhunt h1b-update   # one-time: download US DOL H-1B filings (~300 MB) and index sponsors
jobhunt run          # scrape -> score (-> LLM review if a key is set) -> print top 15
jobhunt serve        # dashboard on http://127.0.0.1:8765
```

Your resume is read from `resume_path` in `config/preferences.yaml` (default `uploads/resume.pdf`; it is git-ignored). A full scrape of ~140 boards takes about a minute; scoring ~1,700 postings takes ~15 s.

### Commands

| Command | What it does |
|---|---|
| `jobhunt scrape [-s greenhouse -c anthropic]` | Fetch postings, filter, dedupe, store in `data/jobs.db` |
| `jobhunt score` | (Re)score everything. Run after editing any file in `config/` |
| `jobhunt analyze [-n 25]` | LLM deep review of the best matches (needs an API key) |
| `jobhunt run` | scrape + score + (analyze) + show top matches |
| `jobhunt top -n 30 --tier preferred --location bay_area,austin` | Ranked table in the terminal |
| `jobhunt show <id>` | Full explanation of one job's score |
| `jobhunt mark <id> applied` | Track status: new, saved, applied, interviewing, rejected, ignored |
| `jobhunt export -f csv -o matches.csv` | Export to CSV / Markdown |
| `jobhunt h1b-update` | Build/refresh the H-1B sponsor index from US DOL LCA disclosure data |
| `jobhunt check-companies` | Probe every configured careers board; shows dead tokens |
| `jobhunt serve` | Dashboard with filters, score breakdowns and status tracking |

## Where jobs come from

- **Company career boards** via the public Greenhouse, Lever and Ashby JSON APIs (`config/companies.yaml`, ~140 AI-forward employers verified live: Anthropic, OpenAI, Databricks, Snowflake, Cursor, Perplexity, Scale, Cohere, Harvey, Glean, Stripe, Datadog, ...). These feeds include full descriptions and usually structured pay-transparency ranges, so scoring is based on the real posting. Add a company by adding `{ats, token, name}`; use `check-companies` to validate.
- **Big tech / FAANG-level** (`bigtech:` in `config/companies.yaml`): Amazon (amazon.jobs), Microsoft and Netflix (Eightfold), NVIDIA, Salesforce and Adobe (Workday) via their public JSON APIs; Apple via its jobs API; **Meta, Google and Uber** have no usable public API, so they are pulled from LinkedIn's public guest search filtered to that company and to the past 7 days. LinkedIn throttles, so that part is slower and best-effort.
- **Aggregators**: Remotive, RemoteOK, Himalayas, and the latest *Ask HN: Who is hiring?* threads.
- **Optional (off by default)**: LinkedIn / Indeed / Glassdoor / Google via [python-jobspy](https://github.com/speedyapply/JobSpy): `uv pip install -e ".[jobspy]"` then `jobhunt scrape -s jobspy`. Those sites rate-limit and block aggressively, so treat it as best effort.

Before storing, postings are filtered by title (excluded: sales, GTM-only, research scientist/PhD, directors, interns, security, hardware, ...), age (**<= 7 days**, based on the publish date; postings with no verifiable date are dropped, and anything older is retired on the next `score`), and US eligibility. Cross-source duplicates are collapsed, preferring the company's own board.

## How the match score works

`final = 20% role + 26% skills + 16% compensation + 13% location + 10% H-1B sponsorship + 8% seniority + 7% resume similarity`, then **hard gates** clamp it. Every number is visible in the dashboard / `jobhunt show`.

1. **Role fit (0-100)**: title is matched against the roles in `config/preferences.yaml` (preferred 80-98, adjacent 48-76, long-shot 38-46). Preferred/adjacent roles get a boost if the JD really is LLM/agent heavy ("Analytics Engineer (AI)"). **Long-shot roles are scored on skill requirements**: a "Software Engineer" JD that is mostly LLM/agents/data pipelines climbs to ~75; a generic Java/Go backend role stays near 35. Soft penalties apply to GTM-flavored, customer-facing, security-domain and infra-heavy titles.
2. **Skills (0-100)**: ~90 skills are detected in the description (`jobhunt/taxonomy.py`). Each mention is weighted by market importance and by section (requirements x1.5, nice-to-have x0.5, boilerplate x0.35) and multiplied by *your* honest proficiency for that skill (`config/profile.yaml`: 1.0 production expert ... 0 none). Hard required gaps (e.g. PyTorch, Java, Go) apply a multiplicative penalty; thin postings are shrunk toward neutral.
3. **Compensation**: pay is taken from structured ATS data when present, else parsed from the text (hourly annualised; geo-tiered ranges pick the tier for the job's location). Postings that mention equity/bonus get a configurable x1.10 total-comp uplift. `>= $220k` scores 100, `$170k` scores 60, `$120k` scores 0. No pay posted scores a neutral 50 and is flagged "verify".
4. **Location**: Bay Area / Austin / US-remote = 100; other US = 25; unclear = 40; non-US = 0 (and capped).
5. **Seniority**: title level (senior best; staff/principal, manager, junior penalised) blended with the "N+ years" requirement versus your relevant years (about 2 in AI engineering, 6+ in data).
6. **H-1B sponsorship** (on by default, `sponsorship.needs_sponsorship`): three independent signals. (a) *The posting text*: "unable to sponsor", "without sponsorship now or in the future", "US citizens only" and similar => score 0 and the job is capped at 30; an explicit offer to sponsor => 100. (b) *The company in general*: if >= 50% of its own postings (3+ seen) refuse sponsorship it is treated as a non-sponsor => capped at 35. (c) *Filing history*: certified H-1B LCAs for the employer in recent US Department of Labor data (`jobhunt h1b-update`; 3 latest quarters, tech-role filings counted separately). Established sponsors score ~95; employers with **no certified filings** are capped at 68 (never "Apply now") unless the posting says it sponsors. Add overrides or entity-name aliases in `config/sponsorship.yaml`.
7. **Resume similarity**: TF-IDF cosine between your resume PDF and the posting, converted to a percentile within the scraped batch.

**Gates**: excluded role -> max 20; non-US -> 25; posting refuses sponsorship -> 30; company doesn't sponsor -> 35; no H-1B history -> 68; clearance/citizenship -> 40; PhD required -> 55; contract -> 60; posted pay below your $170k floor -> 58.

**Verdicts**: >= 84 *Apply now*, >= 74 *Apply*, >= 62 *Stretch*, else *Skip* (thresholds in `preferences.yaml`).

Each job also gets **resume talking points** (your own bullets mapped to the JD's themes), the matched vs. missing skills, pros/cons and red flags.

### Optional LLM deep review

Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (env or `.env`, see `.env.example`), then `jobhunt analyze`. For your top N heuristic matches it reads the full posting against your profile and returns a critical second opinion: fit score, "role reality check", gaps with mitigations, resume tweaks, interview prep. Results are cached per posting; the final score blends 60% heuristic / 40% LLM (LLM can raise a score by at most 12 points and never above a hard gate). Models are configurable in `preferences.yaml` or via `JOBHUNT_ANTHROPIC_MODEL` / `JOBHUNT_OPENAI_MODEL`. The HTTP wiring is unit-tested against a mock server; it has not been run against a live key in this repo.

## Tuning it

Everything is YAML, no code changes needed:

- `config/profile.yaml`: your skill proficiencies and resume proof points. **Review these**: they were derived from your resume by hand and drive the skill score. If you have used something more (or less) than recorded, change the number.
- `config/preferences.yaml`: roles + base scores, excluded titles, soft penalties, salary thresholds, location scores, dimension weights, verdict thresholds.
- `config/companies.yaml`: boards to scan.
- `jobhunt/taxonomy.py`: skill detection regexes and weights.

## Known limits

- Pay shown is mostly **base salary** from the posting; total comp (equity, bonus) is approximated with the multiplier. Many postings list no pay at all.
- Skill detection is regex-based: it won't understand paraphrases, and the "required vs nice-to-have" split is heuristic (section headings and phrases like "a plus").
- Remote eligibility is inferred from location text and phrases; "Remote" with no region is assumed US-eligible, so double-check.
- Companies on Workday / custom ATS (Indeed, Google, Meta, Snowflake's own site, ...) aren't covered, other than via the optional JobSpy source.
- H-1B history is a *signal, not a guarantee*: LCA filings prove an employer sponsors in general, not that a specific team will for a specific role, and DOL uses legal entity names (add aliases in `config/sponsorship.yaml` if a company is mismatched). The text check only sees what the posting says.
- Closed postings are detected only for fully-scraped boards (they drop out of the ranking after the next scrape).

## Development

```bash
pytest          # 62 tests: salary / location / skill extraction, scoring behaviour, LLM client shapes
```

Layout: `jobhunt/sources/` (fetchers) -> `scrape.py` (filter + dedupe + store) -> `scoring.py` (model) -> `pipeline.py` -> `queries.py` -> `cli.py` / `web/` (FastAPI + static UI). Data lives in `data/jobs.db` (SQLite, git-ignored).
