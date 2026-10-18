"""Run traces: written as events happen, and rendered safely for `pri show`."""

import json
from pathlib import Path

import pytest

from pr_investigator.domain import Side
from pr_investigator.trace import Trace, errors_traced, read_trace, render_trace


def test_events_are_flushed_as_they_are_written(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    trace = Trace(path)
    trace.emit("run_start", reviewer="a2", side=Side.LEFT, path=tmp_path)
    # Readable before the trace is closed, as after a crash.
    (event,) = [json.loads(line) for line in path.read_text().splitlines()]
    assert event["event"] == "run_start" and event["side"] == "LEFT"
    assert event["path"] == str(tmp_path) and "t" in event and "scope" not in event
    scoped = trace.scoped("S1")
    scoped.emit("opening", text="hi")
    scoped.close()  # a scoped view never closes the shared file
    trace.emit("run_end")
    trace.close()
    assert [(e["event"], e.get("scope")) for e in read_trace(path)] == [
        ("run_start", None),
        ("opening", "S1"),
        ("run_end", None),
    ]


def test_a_trace_without_a_path_writes_nothing(tmp_path: Path) -> None:
    trace = Trace()
    trace.emit("anything", value=object())  # not even serialized
    assert list(tmp_path.iterdir()) == []


def test_errors_are_recorded_then_raised(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    with Trace(path) as trace, pytest.raises(RuntimeError), errors_traced(trace):
        raise RuntimeError("boom")
    (event,) = read_trace(path)
    assert (event["event"], event["type"], event["message"]) == ("error", "RuntimeError", "boom")


def test_rendering_escapes_control_characters() -> None:
    events = [
        {
            "event": "model_turn",
            "scope": "S1",
            "turn": 1,
            "text": "\x1b[31mred\x1b[0m",
            "tool_calls": [],
        },
        {"event": "error", "type": "X", "message": "a\u202eb"},
    ]
    text = render_trace(events)
    assert "\x1b" not in text and "\\x1b[31mred" in text
    assert "\u202e" not in text and "a\\u202eb" in text
