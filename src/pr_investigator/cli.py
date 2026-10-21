"""`pri` command-line interface."""

import asyncio
import platform
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markdown import Markdown

from pr_investigator import ingest
from pr_investigator.config import Settings
from pr_investigator.domain import DataClass, ReviewTarget
from pr_investigator.errors import PRIError
from pr_investigator.evaluation.cases import load_cases
from pr_investigator.evaluation.runner import render_summary, run_eval, validate_case, write_results
from pr_investigator.github import GitHubClient, parse_pr_reference
from pr_investigator.llm.client import LLMClient, ensure_data_allowed
from pr_investigator.llm.factory import build_client
from pr_investigator.llm.guards import RequestBudget
from pr_investigator.review.report import create_run_dir, render_markdown, save_run
from pr_investigator.review.reviewers import AnyReview, Reviewer, check_toolset, run_reviewer
from pr_investigator.tools import Toolset
from pr_investigator.trace import TRACE_FILE, Trace, printable_lines, read_trace, render_trace
from pr_investigator.workspace import Workspace, run_git

app = typer.Typer(help="PR Investigator: an evidence-driven AI reviewer for GitHub PRs.")
eval_app = typer.Typer(help="Run and inspect the evaluation suite.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")

console = Console()
err = Console(stderr=True)

ProviderOpt = Annotated[
    str | None, typer.Option("--provider", help="LLM provider (overrides PRI_LLM_PROVIDER).")
]
ModelOpt = Annotated[str | None, typer.Option("--model", help="Model (overrides PRI_LLM_MODEL).")]
CasesOpt = Annotated[Path, typer.Option("--cases-dir", help="Directory of case manifests.")]
SplitOpt = Annotated[str, typer.Option("--split", help="Which cases: dev, test (held out) or all.")]
ContextOpt = Annotated[
    bool,
    typer.Option(
        "--context",
        help="a1: add the context pack (callers, callees, tests of changed functions) and "
        "mechanical leads to the opening message.",
    ),
]
ToolsOpt = Annotated[
    Toolset,
    typer.Option(
        "--tools",
        help="The agent's tools (a1 and a2): base, or structural (adds find_symbol, "
        "find_references, get_symbol_context, find_tests and git_history).",
    ),
]
FixturesOpt = Annotated[Path, typer.Option("--fixtures-dir", help="Directory of fixtures.")]


def _settings(provider: str | None = None, model: str | None = None) -> Settings:
    overrides: dict[str, Any] = {}
    if provider is not None:
        overrides["llm_provider"] = provider
    if model is not None:
        overrides["llm_model"] = model
    return Settings(**overrides)


def _fail(exc: Exception) -> typer.Exit:
    err.print(f"[bold red]error:[/] {exc}")
    return typer.Exit(code=1)


@app.command()
def review(
    target: Annotated[str, typer.Argument(help="PR URL, OWNER/REPO#N, or a local repo path.")],
    base: Annotated[str | None, typer.Option(help="Base ref (local repositories only).")] = None,
    head: Annotated[str | None, typer.Option(help="Head ref (local repositories only).")] = None,
    title: Annotated[str | None, typer.Option(help="PR title (local repositories only).")] = None,
    description: Annotated[str, typer.Option(help="PR description (local only).")] = "",
    data_class: Annotated[
        DataClass,
        typer.Option(
            "--data-class",
            help="Who may see a local repository's code. 'private' blocks providers that may "
            "train on inputs, such as Gemini's free tier.",
        ),
    ] = DataClass.PRIVATE,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    reviewer: Annotated[
        Reviewer,
        typer.Option(
            "--reviewer",
            help="a2 (default): planner, then one investigation per suspect. a1: one agent "
            "loop over the whole PR. b0: one call on the diff (the Phase 1 baseline). b1: "
            "one call on the diff plus the context pack and leads.",
        ),
    ] = Reviewer.A2,
    tools: ToolsOpt = Toolset.BASE,
    context: ContextOpt = False,
    max_requests: Annotated[
        int, typer.Option(min=1, help="Cap on real provider requests (cache hits are free).")
    ] = 30,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Review a pull request with the agentic reviewer A2 (default), A1, or the baseline B0."""
    run_dir: Path | None = None
    try:
        settings = _settings(provider, model)
        check_toolset(reviewer, tools, context)
        client = build_client(settings, budget=RequestBudget(max_requests))
        review_target, workspace = _ingest(
            target, base, head, title, description, data_class, client, settings
        )
        run_dir = create_run_dir(settings.runs_dir)
        result = asyncio.run(
            _run_reviewer(
                reviewer, tools, context, review_target, workspace, client, settings, run_dir
            )
        )
        save_run(result, run_dir)
    except PRIError as exc:
        if run_dir is not None and (run_dir / TRACE_FILE).is_file():
            err.print(f"[dim]Partial trace: pri show {run_dir}[/]")
        raise _fail(exc) from exc
    if as_json:
        console.print_json(result.model_dump_json())
    else:
        console.print(Markdown(render_markdown(result)))
        console.print(f"\n[dim]Saved to {run_dir}[/]")
        if reviewer is not Reviewer.B0:
            console.print(f"[dim]Timeline: pri show {run_dir}[/]")


async def _run_reviewer(
    reviewer: Reviewer,
    tools: Toolset,
    context: bool,
    target: ReviewTarget,
    workspace: Workspace,
    client: LLMClient,
    settings: Settings,
    run_dir: Path,
) -> AnyReview:
    with Trace(run_dir / TRACE_FILE if reviewer.agentic else None) as trace:
        return await run_reviewer(
            reviewer,
            target,
            workspace,
            workspace.diff(),
            client,
            max_diff_chars=settings.max_diff_chars,
            trace=trace,
            toolset=tools,
            index_db=settings.index_db,
            context=context,
        )


@app.command()
def show(
    run: Annotated[str, typer.Argument(help="A run id, run directory or trace file.")],
    full: Annotated[
        bool, typer.Option("--full", help="Include model text and complete tool output.")
    ] = False,
) -> None:
    """Show what a review did: its plan, each investigation's turns, and its findings."""
    try:
        path = _resolve_run(run, _settings())
    except PRIError as exc:
        raise _fail(exc) from exc
    trace = path if path.is_file() else path / TRACE_FILE
    if trace.is_file():
        typer.echo(render_trace(read_trace(trace), full=full), nl=False)
    elif (path / "review.md").is_file():  # B0 runs have no trace
        text = (path / "review.md").read_text(encoding="utf-8")
        typer.echo("\n".join(printable_lines([text])))
    else:
        raise _fail(PRIError(f"{path} has no trace or review"))


def _resolve_run(run: str, settings: Settings) -> Path:
    for candidate in (Path(run).expanduser(), settings.runs_dir / run):
        if candidate.exists():
            return candidate
    raise PRIError(f"no run, run directory or trace found for {run!r}")


def _ingest(
    target: str,
    base: str | None,
    head: str | None,
    title: str | None,
    description: str,
    data_class: DataClass,
    client: LLMClient,
    settings: Settings,
) -> tuple[ReviewTarget, Workspace]:
    path = Path(target).expanduser()
    if base is not None or head is not None or path.is_dir():
        if base is None or head is None:
            raise PRIError("local reviews need both --base and --head")
        ensure_data_allowed(data_class, client.capabilities, client.provider)
        return ingest.from_local(
            path,
            base,
            head,
            title=title or f"{head} into {base}",
            description=description,
            data_class=data_class,
            workspaces_dir=settings.workspaces_dir,
        )
    token = settings.github_token.get_secret_value() if settings.github_token else None
    github = GitHubClient(token)
    try:
        info = github.get_pull_request(parse_pr_reference(target))
    finally:
        github.close()
    # Check before fetching, so private code never reaches the machine for a provider that
    # may not see it.
    ensure_data_allowed(ingest.data_class_for(info), client.capabilities, client.provider)
    return ingest.from_github(info, settings.workspaces_dir)


@app.command()
def doctor() -> None:
    """Check the local setup without calling any API."""
    settings = _settings()
    checks: list[tuple[str, str]] = [("python", platform.python_version())]
    try:
        checks.append(("git", run_git(["--version"]).decode().strip()))
    except PRIError as exc:
        checks.append(("git", f"MISSING ({exc})"))
    checks.append(("ripgrep", shutil.which("rg") or "MISSING (needed by search_code)"))
    checks.append(("provider / model", f"{settings.llm_provider} / {settings.llm_model}"))
    if settings.llm_provider == "gemini":
        checks.append(("GEMINI_API_KEY", "set" if settings.gemini_api_key else "NOT SET"))
        checks.append(("gemini tier", settings.gemini_tier))
    checks.append(("paid providers allowed", str(settings.allow_paid_providers)))
    checks.append(("requests per minute", str(settings.llm_requests_per_minute)))
    checks.append(("response cache", f"{settings.llm_cache_mode.value} at {settings.cache_dir}"))
    checks.append(("GITHUB_TOKEN", "set" if settings.github_token else "not set (60 req/hour)"))
    width = max(len(name) for name, _ in checks)
    for name, value in checks:
        console.print(f"{name:<{width}}  {value}")


@eval_app.command("list")
def eval_list(cases_dir: CasesOpt = Path("evals/cases"), split: SplitOpt = "all") -> None:
    """List evaluation cases."""
    try:
        cases = load_cases(cases_dir, split=split)
    except PRIError as exc:
        raise _fail(exc) from exc
    for case in cases:
        console.print(
            f"{case.id:<44} {case.split:<5} {case.kind.value:<7} {case.fixture:<18} {case.title}"
        )


@eval_app.command("validate")
def eval_validate(
    cases_dir: CasesOpt = Path("evals/cases"), fixtures_dir: FixturesOpt = Path("evals/fixtures")
) -> None:
    """Check every case end to end without calling an LLM."""
    try:
        cases = load_cases(cases_dir)
        with tempfile.TemporaryDirectory(prefix="pri-validate-") as tmp:
            for case in cases:
                validate_case(case, fixtures_dir, Path(tmp))
                console.print(f"[green]ok[/]  {case.id}")
    except PRIError as exc:
        raise _fail(exc) from exc
    console.print(f"{len(cases)} cases valid.")


@eval_app.command("run")
def eval_run(
    case: Annotated[
        list[str] | None, typer.Option("--case", help="Run only these case ids (repeatable).")
    ] = None,
    samples: Annotated[int, typer.Option(min=1, help="Reviews per case.")] = 1,
    judge: Annotated[
        bool, typer.Option(help="Use the LLM judge; --no-judge matches on location only.")
    ] = True,
    max_requests: Annotated[
        int, typer.Option(min=1, help="Cap on real provider requests (cache hits are free).")
    ] = 50,
    output: Annotated[Path | None, typer.Option(help="Results directory.")] = None,
    cases_dir: CasesOpt = Path("evals/cases"),
    fixtures_dir: FixturesOpt = Path("evals/fixtures"),
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    reviewer: Annotated[
        Reviewer, typer.Option("--reviewer", help="Which reviewer to evaluate: b0, b1, a2 or a1.")
    ] = Reviewer.B0,
    traces: Annotated[
        bool, typer.Option(help="Write a trace per case to <results>/traces (a1 and a2 only).")
    ] = True,
    split: SplitOpt = "all",
    tools: ToolsOpt = Toolset.BASE,
    context: ContextOpt = False,
) -> None:
    """Run a reviewer over the evaluation cases and score it."""
    try:
        settings = _settings(provider, model)
        check_toolset(reviewer, tools, context)
        cases = load_cases(cases_dir, only=case, split=split)
        budget = RequestBudget(max_requests)
        client = build_client(settings, budget=budget)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        safe_model = client.model.replace("/", "_")
        suffix = ("" if tools is Toolset.BASE else f"-{tools.value}") + ("-ctx" if context else "")
        out_dir = output or Path("evals/results") / f"{stamp}-{reviewer.value}{suffix}-{safe_model}"
        info, scores, summary = asyncio.run(
            run_eval(
                cases,
                fixtures_dir=fixtures_dir,
                client=client,
                judge_client=client if judge else None,
                samples=samples,
                max_diff_chars=settings.max_diff_chars,
                progress=lambda message: console.print(f"[dim]{message}[/]"),
                reviewer=reviewer,
                traces_dir=out_dir / "traces" if traces else None,
                split=split,
                toolset=tools,
                index_db=settings.index_db,
                context=context,
            )
        )
    except PRIError as exc:
        raise _fail(exc) from exc
    info.provider_requests = budget.used
    write_results(out_dir, info, scores, summary)
    console.print(Markdown(render_summary(info, scores, summary)))
    console.print(f"[dim]{budget.used} real provider requests. Results in {out_dir}[/]")


if __name__ == "__main__":  # pragma: no cover
    app()
