"""The bounded agent loop (D4): the model investigates with read-only tools until it submits.

One loop runs one investigation, in a fresh conversation. Each turn sends one request through
`LLMClient`, appends the reply unchanged, runs up to `max_tool_calls_per_turn` of the requested
tools and sends their results back together. History is append-only: nothing already sent is
edited. The loop ends when:

- a call to the terminal tool passes its checks (`submitted`);
- the turn budget runs out without one (`turn_budget`);
- the provider refuses (`refused`) or cuts a reply off at the output limit (`max_tokens`);
- the provider rejects the conversation's request (`error`).

Quota, request-cap, cache-miss, data-policy and persistent server errors aren't outcomes of one
investigation: they propagate and abort the run.

The model is never forced to call a tool. A reply without one gets a plain-text reminder, and the
last turn is announced before it is used. A submission is checked against the ledger as it
stood before the submitting turn, so it can only cite what the model has already been shown.
Repository content reaches the model only inside tool results, wrapped as untrusted data
(docs/security.md §5.1).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pr_investigator.agent.ledger import ObservationLedger, printable
from pr_investigator.diff import ParsedDiff
from pr_investigator.domain import DataClass
from pr_investigator.llm.client import LLMClient
from pr_investigator.llm.prompts import load_prompt
from pr_investigator.llm.types import (
    Effort,
    LLMRequest,
    LLMRequestError,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)
from pr_investigator.review.baseline import untrusted
from pr_investigator.tools import (
    READ_ONLY_TOOLS,
    Observation,
    Tool,
    ToolContext,
    run_tool,
    tool_specs,
)
from pr_investigator.workspace import Workspace

PROMPT_NAME, PROMPT_VERSION = "agent_loop", 1


class StopCause(StrEnum):
    SUBMITTED = "submitted"  # the terminal tool accepted a submission
    TURN_BUDGET = "turn_budget"  # no accepted submission within the turn budget
    REFUSED = "refused"  # the provider declined to answer
    MAX_TOKENS = "max_tokens"  # a reply was cut off at the output token limit
    ERROR = "error"  # the provider rejected the request


@dataclass
class Submission[T]:
    """A terminal tool's answer to one submission: the accepted value, or errors for the model."""

    value: T | None = None
    errors: list[str] = field(default_factory=list)


class TerminalTool[T](Protocol):
    """The tool that ends a loop, such as submit_verdict. It validates and checks submissions."""

    @property
    def spec(self) -> ToolSpec: ...

    def check(self, arguments: dict[str, Any], ledger: ObservationLedger) -> Submission[T]: ...


@dataclass(frozen=True)
class LoopConfig:
    stage: str
    data_class: DataClass
    max_turns: int  # model requests, D25: 6 per suspect
    max_tool_calls_per_turn: int = 3
    max_tool_output_chars: int = 4_000
    effort: Effort = Effort.MEDIUM
    sample: int = 0

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_tool_calls_per_turn < 1:
            raise ValueError("max_turns and max_tool_calls_per_turn must be at least 1")


@dataclass
class LoopResult[T]:
    cause: StopCause
    submission: T | None  # the accepted submission; set only when cause is SUBMITTED
    detail: str | None  # the provider's reason for a refusal or error
    turns: int  # model requests made
    cached_turns: int  # of those, answered from the response cache
    tool_calls: int  # tool calls executed, failed ones included (not those refused as over-limit)
    usage: Usage
    rejections: list[list[str]]  # the errors sent back for each rejected submission
    messages: list[Message]
    ledger: ObservationLedger


class AgentLoop[T]:
    def __init__(
        self,
        client: LLMClient,
        workspace: Workspace,
        diff: ParsedDiff,
        terminal: TerminalTool[T],
        config: LoopConfig,
        *,
        tools: tuple[Tool, ...] = READ_ONLY_TOOLS,
    ) -> None:
        if terminal.spec.name in {t.name for t in tools}:
            raise ValueError(f"terminal tool {terminal.spec.name!r} clashes with a repository tool")
        self.client = client
        self.terminal = terminal
        self.config = config
        self.tools = tools
        self.ctx = ToolContext(
            workspace=workspace, diff=diff, max_output_chars=config.max_tool_output_chars
        )
        self.ledger = ObservationLedger()
        self.messages: list[Message] = []
        self.turns = 0
        self.cached_turns = 0
        self.tool_calls = 0
        self.usage = Usage()
        self.rejections: list[list[str]] = []
        self._texts = load_prompt(PROMPT_NAME, PROMPT_VERSION).sections()

    def present(self, label: str, content: str, observations: list[Observation]) -> str:
        """Record what `content` shows and wrap it as untrusted data, with its observation ids
        listed above it. Used for tool results and for content in the opening message."""
        ids = self.ledger.record(observations)
        return self.ledger.annotate(untrusted(label, content), ids)

    async def run(self, system: str, opening: str) -> LoopResult[T]:
        if self.messages:
            raise RuntimeError("an AgentLoop runs only once")
        self.messages.append(Message.user(opening))
        specs = [*tool_specs(self.tools), self.terminal.spec]
        # A note owed to the model about its previous reply: (prompt section, detail).
        reminder: tuple[str, str] | None = None
        while self.turns < self.config.max_turns:
            last_turn = self.turns == self.config.max_turns - 1
            notes: list[str] = []
            # On the last turn, the last-turn note replaces the plain no-tool-call reminder.
            if reminder is not None and not (last_turn and reminder[0] == "no_tool_call"):
                notes.append(self._text(reminder[0], detail=reminder[1]))
            if last_turn:
                notes.append(self._text("last_turn"))
            if notes:
                self.messages.append(Message.user("\n\n".join(notes)))

            request = LLMRequest(
                stage=self.config.stage,
                data_class=self.config.data_class,
                system=system,
                messages=list(self.messages),
                tools=specs,
                effort=self.config.effort,
                sample=self.config.sample,
            )
            try:
                response = await self.client.generate(request)
            except LLMRequestError as exc:
                return self._finish(StopCause.ERROR, detail=str(exc))
            self._count(response)
            self.messages.append(response.to_message())

            if response.stop_reason is StopReason.REFUSED:
                return self._finish(StopCause.REFUSED, detail=response.stop_detail)
            if response.stop_reason is StopReason.MAX_TOKENS:
                return self._finish(StopCause.MAX_TOKENS)
            reminder = None
            if response.tool_calls:
                accepted = self._answer(response.tool_calls)
                if accepted is not None:
                    return self._finish(StopCause.SUBMITTED, submission=accepted)
            elif response.stop_reason is StopReason.ERROR:
                detail = printable(response.stop_detail or "the reply was empty")
                reminder = ("unusable_reply", detail)
            else:
                reminder = ("no_tool_call", "")
        return self._finish(StopCause.TURN_BUDGET)

    def _answer(self, calls: list[ToolCall]) -> T | None:
        """Answer every call of one turn, in order. Returns the accepted submission, if any.

        The submission is checked first, against what earlier turns showed. Repository tools
        run only if it was rejected (or absent), up to the per-turn limit.
        """
        terminal = self.terminal.spec.name
        replies: list[Message | None] = [None] * len(calls)
        accepted: T | None = None
        submitted = False
        for index, call in enumerate(calls):
            if call.name != terminal:
                continue
            if submitted:
                replies[index] = self._not_run(call, "duplicate_submission")
                continue
            submitted = True
            check = self.terminal.check(call.arguments, self.ledger)
            if check.value is not None and not check.errors:
                accepted = check.value
                replies[index] = Message.tool_result(call, self._text("submission_accepted"))
            else:
                errors = check.errors or ["the submission was rejected"]
                self.rejections.append(errors)
                listed = "\n".join(f"- {printable(error)}" for error in errors)
                text = self._text("submission_rejected", errors=listed)
                replies[index] = Message.tool_result(call, text, is_error=True)

        ran = 0
        for index, call in enumerate(calls):
            if replies[index] is not None:
                continue
            if accepted is not None:
                replies[index] = self._not_run(call, "not_run_ended")
            elif ran >= self.config.max_tool_calls_per_turn:
                replies[index] = self._not_run(call, "not_run_limit")
            else:
                ran += 1
                replies[index] = self._run_tool(call)
        self.messages.extend(reply for reply in replies if reply is not None)
        return accepted

    def _run_tool(self, call: ToolCall) -> Message:
        self.tool_calls += 1
        result = run_tool(call.name, call.arguments, self.ctx, self.tools)
        known = any(t.name == call.name for t in self.tools)
        label = call.name if known else "unknown-tool"  # the name is model output
        if result.is_error:
            limit = self.config.max_tool_output_chars
            content = result.content
            if len(content) > limit:
                content = content[:limit] + " [cut short]"
            return Message.tool_result(call, untrusted(label, content), is_error=True)
        return Message.tool_result(call, self.present(label, result.content, result.observations))

    def _not_run(self, call: ToolCall, reason: str) -> Message:
        return Message.tool_result(call, self._text(reason), is_error=True)

    def _text(self, name: str, **values: str) -> str:
        return self._texts[name].format(
            terminal=self.terminal.spec.name,
            limit=self.config.max_tool_calls_per_turn,
            **values,
        )

    def _count(self, response: LLMResponse) -> None:
        self.turns += 1
        self.cached_turns += response.cached
        self.usage = self.usage + response.usage

    def _finish(
        self, cause: StopCause, *, submission: T | None = None, detail: str | None = None
    ) -> LoopResult[T]:
        return LoopResult(
            cause=cause,
            submission=submission,
            detail=detail,
            turns=self.turns,
            cached_turns=self.cached_turns,
            tool_calls=self.tool_calls,
            usage=self.usage,
            rejections=self.rejections,
            messages=self.messages,
            ledger=self.ledger,
        )
