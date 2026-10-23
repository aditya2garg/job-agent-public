from jobhunt.models import Job
from jobhunt.roles import classify_role
from jobhunt.scoring import Context, analyze, combine, score_jobs

AI_JD = """About the role
We are building production agentic systems on top of LLMs.

Requirements:
• 3+ years of experience with Python and SQL
• Hands-on with LLM agents, tool calling, MCP and RAG pipelines
• Experience with Snowflake and Airflow
• Familiar with Kubernetes and AWS

Nice to have:
• Fine-tuning
Salary: $200,000 - $260,000 plus equity.
"""

JAVA_JD = """Requirements:
• 7+ years of Java and Spring Boot
• Strong C++ and Go experience
• Distributed systems, Kafka, gRPC, microservices
Salary: $210,000 - $260,000
"""


def mk(title, desc, loc="San Francisco, CA", **kw):
    return Job(source="greenhouse", company="Acme", title=title, url=f"https://x/{title}", description=desc, location=loc, **kw)


def run(job):
    ctx = Context.load()
    return combine(analyze(job, ctx), 60.0, ctx)


def test_role_classification():
    assert classify_role("Senior Agentic AI Engineer").name == "Agentic AI Engineer"
    assert classify_role("Staff AI Engineer, Platform").tier == "preferred"
    assert classify_role("Analytics Engineer").name == "Analytics Engineer"
    assert classify_role("Data Engineer II").tier == "longshot"
    assert classify_role("Senior Software Engineer, Backend").tier == "longshot"
    assert classify_role("Account Executive, AI") is None
    assert classify_role("Research Scientist, LLMs") is None
    assert classify_role("Director of Engineering") is None
    assert classify_role("Machine Learning Engineer Intern") is None


def test_ideal_job_scores_high():
    r = run(mk("Senior AI Engineer", AI_JD))
    assert r["final"] >= 84 and r["verdict"] == "Apply now"
    assert r["breakdown"]["compensation"] == 100 and r["breakdown"]["location"] == 100


def test_java_backend_longshot_scores_low():
    r = run(mk("Senior Software Engineer, Backend", JAVA_JD))
    ideal = run(mk("Senior AI Engineer", AI_JD))
    assert r["final"] < ideal["final"] - 20
    assert r["verdict"] in ("Skip", "Stretch")
    assert r["details"]["skills"]["critical_gaps"]


def test_longshot_with_ai_content_is_promoted():
    generic = run(mk("Software Engineer", "Requirements:\n• Python\n• REST APIs\n• Agile"))
    ai_heavy = run(mk("Software Engineer", AI_JD))
    assert ai_heavy["breakdown"]["role"] > generic["breakdown"]["role"] + 20


def test_non_us_is_capped():
    r = run(mk("Senior AI Engineer", AI_JD, loc="London, United Kingdom"))
    assert r["final"] <= 25 and "outside the US" in r["details"]["caps"]


def test_salary_below_floor_is_capped():
    r = run(mk("Senior AI Engineer", AI_JD.replace("$200,000 - $260,000", "$110,000 - $140,000")))
    assert r["final"] <= 58


def test_unknown_salary_is_neutral_and_flagged():
    r = run(mk("Senior AI Engineer", AI_JD.replace("Salary: $200,000 - $260,000 plus equity.", "")))
    assert r["breakdown"]["compensation"] == 50
    assert any("Pay not disclosed" in c for c in r["details"]["cons"])


def test_clearance_cap():
    r = run(mk("Senior AI Engineer", AI_JD + "\nActive TS/SCI clearance required."))
    assert r["final"] <= 40


def test_structured_salary_beats_text():
    j = mk("Senior AI Engineer", AI_JD, salary_min=100000, salary_max=120000)
    assert run(j)["details"]["salary"]["source"] == "structured"


def test_semantic_scores_are_percentiles():
    jobs = [mk("Senior AI Engineer", AI_JD), mk("Senior Software Engineer", JAVA_JD), mk("Data Engineer", "Build Airflow and Snowflake pipelines with Python and SQL.")]
    out = score_jobs(jobs)
    assert all(0 <= o["breakdown"]["semantic"] <= 100 for o in out)
    assert out[0]["breakdown"]["semantic"] > out[1]["breakdown"]["semantic"]


def test_evidence_matching_surfaces_relevant_resume_points():
    r = run(mk("Senior AI Engineer", AI_JD))
    ids = {e["id"] for e in r["details"]["evidence"]}
    assert "agentic_platform" in ids
