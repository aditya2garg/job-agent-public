import datetime as dt

import pytest

from jobhunt import sponsorship as sp
from jobhunt.extract import sponsorship_stance
from jobhunt.models import Job
from jobhunt.scoring import Context, analyze, combine, company_policies
from jobhunt.scrape import ScrapeStats, keep_job

JD = """Requirements:
• 4+ years of Python and SQL
• Experience with LLM agents, MCP and RAG
Salary: $210,000 - $260,000
"""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We are unable to sponsor visas for this role.", "no"),
        ("Applicants must be authorized to work in the US without sponsorship now or in the future.", "no"),
        ("Visa sponsorship is not available for this position.", "no"),
        ("We do not offer visa sponsorship at this time", "no"),
        ("Must be a U.S. citizen.", "no"),
        ("We will sponsor H-1B visas for the right candidate.", "yes"),
        ("We offer visa sponsorship and relocation.", "yes"),
        ("Must be legally authorized to work in the United States.", "unknown"),
        ("Do you now or in the future require visa sponsorship?", "unknown"),
        ("We love building agents.", "unknown"),
    ],
)
def test_stance(text, expected):
    assert sponsorship_stance(text)[0] == expected


def test_no_beats_yes():
    assert sponsorship_stance("We sponsor visas for senior roles. This role does not offer sponsorship.")[0] == "no"


def test_normalize_and_matching(monkeypatch):
    assert sp.normalize("Stripe, Inc.") == "stripe"
    assert sp.normalize("OPENAI OPCO, LLC") == "openai"
    index = {"stripe": [120, 90], "stripe payments europe": [5, 0], "meta platforms": [3000, 2800], "tiny co": [0, 0]}
    monkeypatch.setattr(sp, "load_index", lambda: index)
    sp.company_history.cache_clear()
    assert sp.company_history("Stripe")["tech"] == 90
    assert sp.company_history("Meta")["certified"] == 3000  # alias in config/sponsorship.yaml
    assert sp.company_history("Totally Unknown Startup")["certified"] == 0
    sp.company_history.cache_clear()


def test_history_score_tiers():
    cfg = {"strong_history": 50, "some_history": 10}
    base = {"available": True, "certified": 0, "tech": 0}
    assert sp.history_score({**base, "certified": 500, "tech": 300}, cfg)[0] >= 90
    assert 80 <= sp.history_score({**base, "certified": 40, "tech": 15}, cfg)[0] < 90
    assert sp.history_score(base, cfg)[0] < 30
    assert sp.history_score({"available": False, "certified": 0, "tech": 0}, cfg)[0] == 55


def _job(desc=JD, company="Acme AI", **kw):
    posted = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    return Job(source="greenhouse", company=company, title="Senior AI Engineer", url="https://x/1", description=desc, location="San Francisco, CA", posted_at=posted, **kw)


def _score(job, history, policy=None):
    ctx = Context.load()
    a = analyze(job, ctx)
    a["details"]["sponsorship_raw"]["history"] = history
    return combine(a, 70.0, ctx, policy)


SPONSOR = {"available": True, "certified": 400, "tech": 300}
NONE = {"available": True, "certified": 0, "tech": 0}


def test_established_sponsor_scores_high():
    r = _score(_job(), SPONSOR)
    assert r["final"] >= 84 and r["breakdown"]["sponsorship"] >= 90


def test_posting_that_refuses_sponsorship_is_crushed():
    ok = _score(_job(), SPONSOR)
    no = _score(_job(JD + "\nWe are unable to sponsor work visas."), SPONSOR)
    assert no["final"] <= 30 and no["verdict"] == "Skip"
    assert ok["final"] - no["final"] > 50
    assert "posting says no visa sponsorship" in no["details"]["caps"]


def test_company_wide_no_sponsorship_policy_is_capped():
    r = _score(_job(), SPONSOR, {"no": True, "share": 0.8, "n": 10})
    assert r["final"] <= 35 and r["breakdown"]["sponsorship"] <= 5


def test_no_history_caps_below_apply_threshold():
    r = _score(_job(), NONE)
    assert r["final"] <= 68 and r["verdict"] != "Apply now"
    assert any("No H-1B" in c for c in r["details"]["cons"])


def test_explicit_yes_overrides_missing_history():
    r = _score(_job(JD + "\nWe sponsor H-1B visas."), NONE)
    assert r["final"] > 68 and r["breakdown"]["sponsorship"] == 100


def test_policy_threshold():
    ctx = Context.load()
    jobs = [_job(company="A") for _ in range(4)]
    analyses = []
    for i, j in enumerate(jobs):
        a = analyze(j, ctx)
        a["details"]["sponsorship_raw"].update(stance="no" if i < 3 else "unknown", evidence="we do not sponsor" if i < 3 else "")
        analyses.append(a)
    assert company_policies(jobs, analyses, ctx)["A"]["no"] is True
    assert company_policies(jobs, analyses, ctx, {"A": (10, 1)})["A"]["no"] is False


def test_freshness_filter():
    stats = ScrapeStats()
    fresh = _job()
    old = _job()
    old.posted_at = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=9)).isoformat()
    undated = _job()
    undated.posted_at = None
    assert keep_job(fresh, stats, 7, True)
    assert not keep_job(old, stats, 7, True) and stats.dropped_age == 1
    assert not keep_job(undated, stats, 7, True) and stats.dropped_undated == 1
