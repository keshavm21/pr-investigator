"""Run traces: an append-only JSONL log of what a review did, written as it happens.

Each line is one event: `{"t": <UTC time>, "event": <name>, "scope": <suspect id or "review">,
...data}`. Every event is flushed when it is written, so a crash leaves a readable trace up to
that point. Traces hold repository content and model output, so they are run data: they live
under `.pri/runs/` (gitignored) or, for evals, in the results folder you choose to commit.

`render_trace` turns a trace back into a readable timeline for `pri show`. Everything in a trace
may be untrusted, so control characters are escaped before anything reaches the terminal.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import IO, Any, Self

from pydantic import BaseModel

from pr_investigator.agent.ledger import printable

TRACE_FILE = "trace.jsonl"


class Trace:
    """Writes events to a JSONL file, or nowhere when created without a path."""

    def __init__(self, path: Path | None = None) -> None:
        self._file: IO[str] | None = path.open("w", encoding="utf-8") if path else None
        self._owner = True
        self.scope: str | None = None

    def scoped(self, scope: str) -> "Trace":
        """The same trace, with `scope` on every event (such as a suspect id)."""
        child = Trace()
        child._file, child._owner, child.scope = self._file, False, scope
        return child

    def emit(self, event: str, **data: Any) -> None:
        if self._file is None:
            return
        record: dict[str, Any] = {"t": datetime.now(UTC).isoformat(timespec="milliseconds")}
        record["event"] = event
        if self.scope is not None:
            record["scope"] = self.scope
        record.update(data)
        self._file.write(json.dumps(record, default=_jsonable, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None and self._owner:
            self._file.close()
        self._file = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


@contextmanager
def errors_traced(trace: Trace) -> Iterator[None]:
    """Record any exception as an `error` event, then let it propagate."""
    try:
        yield
    except Exception as exc:
        trace.emit("error", type=type(exc).__name__, message=str(exc))
        raise


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"can't write {type(value).__name__} to a trace")


def read_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def render_trace(events: list[dict[str, Any]], *, full: bool = False) -> str:
    """A readable timeline: the run, the plan, each investigation's turns, then the findings.
    With `full`, model text, opening messages and complete tool output are included."""
    out: list[str] = []
    current_scope: str | None = None
    for event in events:
        kind = event.get("event")
        scope = event.get("scope")
        if scope is not None and scope != current_scope and kind != "investigation_start":
            current_scope = scope
            out += ["", f"== {scope} =="]
        if kind == "run_start":
            out += _run_start(event)
        elif kind == "plan":
            out += _plan(event)
        elif kind == "investigation_start":
            current_scope = scope
            suspect = event.get("suspect", {})
            out += ["", f"== {scope}: {suspect.get('title', '')} =="]
            out.append(f"  at {_location(suspect)} · {suspect.get('category', '')}")
        elif kind == "opening":
            if full:
                out += ["  opening message:", *_indent(event.get("text", ""), 4)]
        elif kind == "reminder":
            out.append(f"  note to the model: {_first_line(event.get('text', ''))}")
            if full:
                out += _indent(event.get("text", ""), 4)
        elif kind == "model_turn":
            out += _model_turn(event, full)
        elif kind == "submission":
            if event.get("accepted"):
                out.append("    submission accepted")
            else:
                out.append("    submission rejected:")
                out += [f"      - {error}" for error in event.get("errors", [])]
        elif kind == "tool_result":
            out += _tool_result(event, full)
        elif kind == "loop_end":
            out.append(
                f"  ended: {event.get('cause')} after {event.get('turns')} turns, "
                f"{event.get('tool_calls')} tool calls"
                + (f" ({event['detail']})" if event.get("detail") else "")
            )
        elif kind == "investigation_end":
            outcome = f"  outcome: {event.get('status')}"
            if event.get("finding_id"):
                outcome += f", reported as {event['finding_id']}"
            if event.get("duplicate_of"):
                outcome += f", same problem as {event['duplicate_of']}"
            out.append(outcome)
            if event.get("claim"):
                out.append(f"    {event['claim']}")
        elif kind == "run_end":
            out += _run_end(event)
        elif kind == "error":
            out += ["", f"ERROR ({event.get('type')}): {event.get('message')}"]
    return "\n".join(printable_lines(out)).strip() + "\n"


def printable_lines(lines: list[str]) -> list[str]:
    """Escape control characters line by line (newlines inside a line included)."""
    return [printable(part) for line in lines for part in line.split("\n")]


def _run_start(event: dict[str, Any]) -> list[str]:
    target = event.get("target", {})
    prompts = ", ".join(event.get("prompts", {}))
    return [
        f"Run: {event.get('reviewer')} review of {target.get('title', '')!r}",
        f"Source: {target.get('source', '')}",
        f"Commits: base {str(target.get('base_sha', ''))[:12]}, "
        f"head {str(target.get('head_sha', ''))[:12]}",
        f"Model: {event.get('provider')} / {event.get('model')} · prompts: {prompts}",
    ]


def _plan(event: dict[str, Any]) -> list[str]:
    cached = ", from cache" if event.get("cached") else ""
    lines = ["", f"Plan ({event.get('attempts')} requests{cached}): {event.get('summary', '')}"]
    for suspect in event.get("suspects", []):
        lines.append(
            f"  {suspect.get('id')} [{suspect.get('category')}, priority {suspect.get('priority')}]"
            f" {_location(suspect)}: {suspect.get('title')}"
        )
    for dropped in event.get("dropped", []):
        lines.append(f"  dropped: {dropped['suspect'].get('title')} ({dropped.get('reason')})")
    return lines


def _model_turn(event: dict[str, Any], full: bool) -> list[str]:
    usage = event.get("usage", {})
    tokens = f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out"
    cached = ", cached" if event.get("cached") else ""
    calls = event.get("tool_calls", [])
    stop = event.get("stop_reason")
    what = ", ".join(_call(c) for c in calls) if calls else f"no tool call ({stop})"
    if event.get("stop_detail"):
        what += f" [{event['stop_detail']}]"
    lines = [f"  turn {event.get('turn')}: {what}  [{tokens}{cached}]"]
    text = event.get("text", "")
    if text:
        lines += _indent(text, 4) if full else [f"    says: {_first_line(text)}"]
    return lines


def _call(call: dict[str, Any]) -> str:
    arguments = call.get("arguments", {})
    if call.get("name") in ("submit_verdict", "report_findings"):
        if "status" in arguments:
            return f"{call['name']}(status={arguments.get('status')})"
        return f"{call['name']}({len(arguments.get('findings', []))} findings)"
    shown = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f"{call.get('name')}({shown[:160]})"


def _tool_result(event: dict[str, Any], full: bool) -> list[str]:
    text = event.get("text", "")
    if full:
        return [f"    {event.get('tool')} result:", *_indent(text, 6)]
    if event.get("observation_ids"):
        return [f"    -> {_first_line(text)}"]
    if event.get("tool") in ("submit_verdict", "report_findings"):
        return []  # shown by the submission event
    lines = [
        line
        for line in text.split("\n")
        if line and not line.startswith(("<untrusted-", "</untrusted-"))
    ]
    label = "error" if event.get("is_error") else "result"
    return [f"    -> {label}: {lines[0][:200] if lines else ''}"]


def _run_end(event: dict[str, Any]) -> list[str]:
    usage = event.get("usage", {})
    lines = [
        "",
        "== Result ==",
        f"Summary: {event.get('summary', '')}",
        f"Requests: {event.get('requests')} ({event.get('cached_requests')} from cache) · "
        f"tokens: {usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out",
    ]
    findings = event.get("findings", [])
    if not findings:
        lines.append("No findings.")
    for finding in findings:
        anchor = finding.get("anchor", {})
        lines += [
            "",
            f"{finding.get('id')} [{finding.get('severity')}] {finding.get('title')}",
            f"  {_location(anchor)} · {finding.get('category')} · "
            f"confidence {finding.get('confidence')}",
            *_indent(finding.get("explanation", ""), 2),
        ]
        for item in finding.get("evidence", []):
            if item.get("role") == "absence":
                lines.append(f"  absence: {item.get('query')} found nothing: {item.get('note')}")
                continue
            lines.append(
                f"  {item.get('role')} {item.get('path')}:{item.get('start_line')}-"
                f"{item.get('end_line')} ({item.get('ref')}): {item.get('note')}"
            )
            lines += [f"    | {line}" for line in (item.get("snippet") or "").split("\n")]
    return lines


def _location(item: dict[str, Any]) -> str:
    where = f"{item.get('path')}:{item.get('start_line')}"
    if item.get("end_line") != item.get("start_line"):
        where += f"-{item.get('end_line')}"
    if item.get("side") == "LEFT":
        where += " (deleted lines)"
    return where


def _first_line(text: str) -> str:
    line = text.strip().split("\n", 1)[0] if text.strip() else ""
    return line if len(line) <= 200 else line[:200] + "..."


def _indent(text: str, width: int) -> list[str]:
    return [" " * width + line for line in text.split("\n")]
