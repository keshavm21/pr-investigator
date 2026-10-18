"""The observation ledger: everything one investigation has been shown, under stable ids.

Each observation a tool returns gets the next id (O1, O2, ...). The ids are listed above the
tool output, so the model can cite them as evidence. The evidence check accepts only ids that
are in this ledger. One ledger belongs to exactly one investigation.
"""

from collections.abc import Iterable

from pr_investigator.tools.base import Observation


class ObservationLedger:
    def __init__(self) -> None:
        self._observations: dict[str, Observation] = {}

    def record(self, observations: Iterable[Observation]) -> list[str]:
        """Store observations and return their new ids, in order."""
        ids: list[str] = []
        for observation in observations:
            observation_id = f"O{len(self._observations) + 1}"
            self._observations[observation_id] = observation
            ids.append(observation_id)
        return ids

    def get(self, observation_id: str) -> Observation | None:
        return self._observations.get(observation_id.strip())

    def items(self) -> list[tuple[str, Observation]]:
        return list(self._observations.items())

    def __len__(self) -> int:
        return len(self._observations)

    def annotate(self, content: str, ids: list[str]) -> str:
        """Put the citable ids above a tool's output."""
        if not ids:
            return content
        labels = "; ".join(describe(i, self._observations[i]) for i in ids)
        return f"[Observations you can cite: {labels}]\n{content}"


def describe(observation_id: str, observation: Observation) -> str:
    # Paths and queries are untrusted and this text sits outside the untrusted block.
    if observation.kind == "search":
        return printable(
            f"{observation_id} = search {observation.query} ({observation.match_count} matches)"
        )
    return printable(
        f"{observation_id} = {observation.path} lines "
        f"{observation.start_line}-{observation.end_line} ({observation.ref})"
    )


def printable(text: str) -> str:
    """Escape control and invisible formatting characters (newlines, bidi overrides, ...), so
    untrusted names can't start new lines or hide text where they're quoted."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode() for c in text)
