import pytest

from jobhunt.extract import classify_location, extract_skills, find_flags, html_to_text, parse_salary, years_required


@pytest.mark.parametrize(
    "text,low,high",
    [
        ("The salary range is $180,000 - $250,000 per year.", 180000, 250000),
        ("Compensation: $150K–$200K + equity", 150000, 200000),
        ("pay between $170,000 and $210,000 USD", 170000, 210000),
        ("$85 - $110 per hour", 85 * 2080, 110 * 2080),
        ("Salary: $150k to $190k", 150000, 190000),
    ],
)
def test_salary_ranges(text, low, high):
    s = parse_salary(text)
    assert s.known and (s.low, s.high) == (low, high)


def test_salary_ignores_noise():
    assert not parse_salary("Raised $50 million in Series B, 10,000 customers").known
    assert not parse_salary("").known


def test_salary_picks_location_tier():
    text = "San Francisco Bay Area: $200,000 - $260,000. All other US locations: $170,000 - $220,000."
    s = parse_salary(text, ("san francisco", "bay area"))
    assert (s.low, s.high) == (200000, 260000) and s.multiple
    s2 = parse_salary(text)
    assert s2.high == 260000


@pytest.mark.parametrize(
    "loc,remote,kind",
    [
        ("San Francisco, CA", None, "bay_area"),
        ("New York | Mountain View, CA", None, "bay_area"),
        ("Austin, TX", None, "austin"),
        ("Remote - US", None, "remote_us"),
        ("Remote - Worldwide", True, "remote_us"),
        ("Remote - Europe", True, "non_us"),
        ("London, England, United Kingdom", None, "non_us"),
        ("Toronto, Canada | Vancouver, Canada", None, "non_us"),
        ("Seattle, WA", None, "us_other"),
        ("Chicago, IL", None, "us_other"),
        ("Nowhereville", None, "unknown"),
        ("", True, "remote_us"),
    ],
)
def test_location(loc, remote, kind):
    assert classify_location(loc, remote).kind == kind


def test_dublin_ca_is_bay_area_but_dublin_ie_is_not():
    assert classify_location("Dublin, CA").kind == "bay_area"
    assert classify_location("Dublin, Ireland").kind == "non_us"


def test_skill_extraction_and_sections():
    text = (
        "About the role\nBuild LLM agents with MCP and RAG.\n\nRequirements:\n• 5+ years of Python and SQL\n• Experience with Snowflake\n\n"
        "Nice to have:\n• Kubernetes\n• Fine-tuning LLMs"
    )
    hits = extract_skills("AI Engineer", text)
    assert {"llm", "agents", "mcp", "rag", "python", "sql", "snowflake", "kubernetes", "fine_tuning"} <= set(hits)
    assert hits["python"].section == "required"
    assert hits["kubernetes"].section == "nice"
    assert hits["fine_tuning"].section == "nice"


def test_go_language_detection_is_strict():
    assert "go" in extract_skills("", "Experience with Python, Go, and Rust.")
    assert "go" not in extract_skills("", "We want you to go above and beyond. Let's go!")


def test_years_required():
    assert years_required("5+ years of experience building ML systems")[0] == 5
    assert years_required("3-5 years of data engineering experience")[0] == 3
    assert years_required("no experience mentioned")[0] is None


def test_flags():
    keys = {k for k, _ in find_flags("Active TS/SCI clearance required. This is a 6-month contract position.")}
    assert {"clearance", "contract"} <= keys


def test_html_to_text_handles_escaped_html():
    out = html_to_text("&lt;p&gt;Hello&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;li&gt;Two&lt;/li&gt;&lt;/ul&gt;")
    assert "Hello" in out and "• One" in out and "• Two" in out
