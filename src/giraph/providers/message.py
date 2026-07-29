"""Offline provider: parse merge-commit messages using a convention."""

from __future__ import annotations

import logging

from giraph.gitgraph import GitRepo
from giraph.models import PullRequest, short_sha
from giraph.providers import PullRequestProvider
from giraph.providers.conventions import MergeConvention

log = logging.getLogger("giraph")


class MessageProvider(PullRequestProvider):
    """Reads PR metadata out of each merge's own message.

    ``attribute_refs`` are the name-rev ref patterns a merge whose message names no target may
    be attributed to (see :meth:`GitRepo.spine_branch_for`); without them such a merge is
    reported as having no PR rather than guessed at.
    """

    def __init__(
        self, *, repo: GitRepo, convention: MergeConvention, attribute_refs: tuple[str, ...] = ()
    ) -> None:
        super().__init__()
        self.repo = repo
        self.convention = convention
        self.attribute_refs = attribute_refs

    def _fetch(self, commit_sha: str) -> list[PullRequest]:
        commit_parents = self.repo.parents(commit_sha)
        if len(commit_parents) < 2:
            return []
        parsed = self.convention.parse(self.repo.commit_message(commit_sha))
        if parsed is None:
            return []
        pr_id, target_ref = parsed
        if target_ref is None:
            if not self.attribute_refs:
                return []
            target_ref = self.repo.spine_branch_for(commit_sha, self.attribute_refs)
            if target_ref is None:
                log.warning(
                    f"  {short_sha(commit_sha)} names no merge target and sits on no accepted ref's spine"
                )
                return []
            log.info(f"  {short_sha(commit_sha)} names no merge target; attributed to {target_ref}")
        return [PullRequest(id=pr_id, target_ref=target_ref, source_tip=commit_parents[1])]
