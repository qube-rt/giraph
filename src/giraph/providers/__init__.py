"""PR-metadata providers, so the classifier stays host-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod

from giraph.models import PullRequest


class PullRequestProvider(ABC):
    """Maps a commit to the pull requests a git host associates with it.

    ``merges_for`` memoizes per sha; subclasses implement ``_fetch``.
    """

    def __init__(self) -> None:
        self._cache: dict[str, list[PullRequest]] = {}

    def merges_for(self, commit_sha: str) -> list[PullRequest]:
        if commit_sha not in self._cache:
            self._cache[commit_sha] = self._fetch(commit_sha)
        return self._cache[commit_sha]

    @abstractmethod
    def _fetch(self, commit_sha: str) -> list[PullRequest]: ...
