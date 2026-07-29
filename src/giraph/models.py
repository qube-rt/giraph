"""Shared types and constants for giraph merge classification."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

Classification = Literal["real", "transport", "mixed", "ineligible", "unmatched", "error"]

REAL: Classification = "real"
TRANSPORT: Classification = "transport"
MIXED: Classification = "mixed"
INELIGIBLE: Classification = "ineligible"
UNMATCHED: Classification = "unmatched"
ERROR: Classification = "error"

TRACKED_CLASSES: frozenset[Classification] = frozenset({REAL, TRANSPORT, MIXED, INELIGIBLE})


class GiraphError(RuntimeError):
    pass


@dataclass(frozen=True)
class PullRequest:
    """A merge's associated pull request, as seen by a provider."""

    id: int | None
    target_ref: str
    source_tip: str


@dataclass(frozen=True)
class Outcome:
    """One merge commit's classification."""

    target_sha: str
    classification: Classification
    mainline: str
    incoming: str
    pr_id: int | None = None
    pr_target_ref: str | None = None
    detail: str = ""


@dataclass
class Report:
    """Every merge outcome for a ``from..to`` window."""

    from_sha: str
    to_sha: str
    considered_merges: int = 0
    outcomes: list[Outcome] = field(default_factory=list)

    def count(self, classification: Classification) -> int:
        return sum(1 for outcome in self.outcomes if outcome.classification == classification)

    def matched_count(self) -> int:
        """Merges attributed to a PR into an accepted ref (every class except no-PR and error)."""
        return sum(1 for outcome in self.outcomes if outcome.classification in TRACKED_CLASSES)

    def summary_line(self) -> str:
        return (
            f"real={self.count(REAL)}; "
            f"transport={self.count(TRANSPORT)}; "
            f"edited-transport={self.count(MIXED)}; "
            f"ineligible={self.count(INELIGIBLE)}; "
            f"no-pr={self.count(UNMATCHED)}; "
            f"error={self.count(ERROR)}; "
            f"merges-examined={self.considered_merges}"
        )


def short_sha(sha: str | None) -> str:
    return sha[:12] if sha else "<none>"


def paths_in_scope(paths: Iterable[str], prefixes: tuple[str, ...]) -> list[str]:
    """Paths under one of ``prefixes``; with no prefixes configured every path is in scope."""
    return [path for path in paths if not prefixes or any(path.startswith(p) for p in prefixes)]


def touches_any_prefix(paths: Iterable[str], prefixes: tuple[str, ...]) -> bool:
    return bool(paths_in_scope(paths, prefixes))
