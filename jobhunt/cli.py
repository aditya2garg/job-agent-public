from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

warnings.filterwarnings("ignore")
app = typer.Typer(add_completion=False, help="Scrape AI/data jobs and score them against your resume.")
console = Console()

VERDICT_STYLE = {"Apply now": "bold green", "Apply": "green", "Stretch": "yellow", "Skip": "dim"}


def _quiet_pdf_logs() -> None:
    import logging

    logging.getLogger("pypdf").setLevel(logging.ERROR)


@app.command()
def scrape(
    sources: Optional[list[str]] = typer.Option(None, "--source", "-s", help="ats, greenhouse, lever, ashby, remotive, remoteok, himalayas, hn, jobspy"),
    company: Optional[list[str]] = typer.Option(None, "--company", "-c", help="Only these companies (name or token)"),
) -> None:
    """Fetch postings from ATS boards + aggregators, filter, and store them."""
    from .scrape import run_scrape

    with console.status("Scraping...") as status:
        stats = run_scrape(sources, company, progress=lambda m: status.update(f"Scraping... {m}"))
    t = Table(title="Scrape summary", show_header=False)
    for k in ("boards_ok", "boards_empty", "fetched", "dropped_role", "dropped_location", "dropped_age", "dropped_duplicate", "kept", "new", "updated", "closed"):
        t.add_row(k.replace("_", " "), str(getattr(stats, k)))
    t.add_row("per source", ", ".join(f"{k}:{v}" for k, v in stats.per_source.items()))
    console.print(t)
    for f in stats.boards_failed:
        console.print(f"[red]failed[/red] {f}")


@app.command()
def score() -> None:
    """(Re)score every stored posting against your profile + preferences."""
    _quiet_pdf_logs()
    from .pipeline import score_all

    with console.status("Scoring..."):
        res = score_all()
    console.print(f"Scored [bold]{res['scored']}[/bold] postings ({res['llm_blended']} blended with LLM analysis).")


@app.command()
def analyze(
    top: Optional[int] = typer.Option(None, "--top", "-n", help="How many of the best heuristic matches to deep-review"),
    force: bool = typer.Option(False, help="Re-run even if cached"),
) -> None:
    """LLM deep review of the best matches (needs GEMINI_API_KEY)."""
    from .llm import analyze_top, provider
    from .pipeline import score_all

    prov = provider()
    if not prov:
        console.print("[yellow]No GEMINI_API_KEY in the environment; skipping LLM review.[/yellow]")
        raise typer.Exit(1)
    console.print(f"Using [bold]{prov[0]}:{prov[1]}[/bold]")
    with console.status("Reviewing top matches..."):
        res = analyze_top(top, force, progress=lambda m: console.print(f"  {m}"))
    console.print(res)
    score_all()


@app.command()
def run(llm: bool = typer.Option(True, help="Run the LLM review if an API key is available")) -> None:
    """scrape -> score (-> LLM review -> re-score)."""
    _quiet_pdf_logs()
    from .llm import provider
    from .pipeline import score_all
    from .scrape import run_scrape

    with console.status("Scraping...") as st:
        stats = run_scrape(progress=lambda m: st.update(f"Scraping... {m}"))
    console.print(f"Scraped: kept {stats.kept} of {stats.fetched} ({stats.new} new)")
    with console.status("Scoring..."):
        score_all()
    if llm and provider():
        analyze(None, False)
    top(15, 0, None, None, False)


@app.command()
def top(
    n: int = typer.Option(25, "--n", "-n"),
    min_score: float = typer.Option(0, "--min-score"),
    tier: Optional[str] = typer.Option(None, help="preferred | adjacent | longshot"),
    location: Optional[str] = typer.Option(None, help="comma list: bay_area,austin,remote_us"),
    all_: bool = typer.Option(False, "--all", help="Include ignored/rejected"),
) -> None:
    """Show the best matches."""
    from .queries import ranked

    rows, total = ranked(min_score=min_score, tier=tier, location_kind=location, include_hidden=all_, limit=n)
    t = Table(title=f"Top {len(rows)} of {total} scored postings", show_lines=False)
    for col in ("id", "score", "verdict", "company", "title", "where", "pay"):
        t.add_column(col, overflow="fold", no_wrap=col in ("id", "score"))
    for r in rows:
        pay = f"${r['salary_low'] / 1000:.0f}-{r['salary_high'] / 1000:.0f}k" if r["salary_known"] else "?"
        t.add_row(r["id"], f"{r['score']:.0f}", f"[{VERDICT_STYLE[r['verdict']]}]{r['verdict']}[/]", r["company"], r["title"], r["location_label"], pay)
    console.print(t)
    console.print("Details: [bold]jobhunt show <id>[/bold]   Dashboard: [bold]jobhunt serve[/bold]")


@app.command()
def show(job_id: str) -> None:
    """Full explanation of one job's score."""
    from .report import full_report

    console.print(full_report(job_id), highlight=False, markup=False)


@app.command()
def mark(job_id: str, status: str, notes: Optional[str] = None) -> None:
    """Set a job's status: new | saved | applied | interviewing | rejected | ignored."""
    from .queries import set_status

    set_status(job_id, status, notes)
    console.print(f"{job_id} -> {status}")


@app.command()
def export(
    fmt: str = typer.Option("md", "--format", "-f", help="md | csv"),
    out: Optional[Path] = typer.Option(None, "--out", "-o"),
    n: int = typer.Option(100, "--n", "-n"),
    min_score: float = typer.Option(60, "--min-score"),
) -> None:
    """Write the ranked list to Markdown or CSV."""
    from .queries import ranked
    from .report import to_csv, to_markdown

    rows, _ = ranked(min_score=min_score, limit=n)
    text = to_csv(rows) if fmt == "csv" else to_markdown(rows, n)
    if out:
        out.write_text(text)
        console.print(f"Wrote {len(rows)} rows to {out}")
    else:
        console.print(text, highlight=False, markup=False)


@app.command("h1b-update")
def h1b_update(files: Optional[list[str]] = typer.Option(None, "--file", "-f", help="DOL file ids, e.g. FY2026_Q1 (default: latest 3 quarters)")) -> None:
    """Download US DOL LCA disclosure data and build the per-employer H-1B sponsorship index."""
    from . import sponsorship

    paths = sponsorship.download(files, progress=lambda m: console.print(m))
    with console.status("Indexing certified H-1B LCAs (a few minutes)...") as st:
        idx = sponsorship.build_index(paths, progress=lambda m: st.update(f"Indexing... {m}"))
    emp = idx["employers"]
    console.print(f"Indexed {len(emp):,} employers with certified H-1B LCAs from {', '.join(idx['files'])}.")


@app.command("check-companies")
def check_companies_cmd() -> None:
    """Probe every configured careers board and report dead tokens."""
    from .scrape import check_companies

    with console.status("Probing boards..."):
        rows = check_companies()
    t = Table()
    for c in ("company", "ats", "token", "status", "open", "relevant"):
        t.add_column(c)
    for r in sorted(rows, key=lambda r: (r["status"] == "ok", -r["relevant"])):
        t.add_row(r["name"], r["ats"], r["token"], r["status"] if r["status"] != "ok" else "[green]ok[/green]", str(r["jobs"]), str(r["relevant"]))
    console.print(t)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    """Start the local dashboard."""
    import uvicorn

    console.print(f"Dashboard: [bold]http://{host}:{port}[/bold]")
    uvicorn.run("jobhunt.web.app:app", host=host, port=port, reload=reload, log_level="warning")


if __name__ == "__main__":
    app()
