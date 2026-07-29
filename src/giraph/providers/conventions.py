"""Merge-message conventions for the message provider.

Each convention parses a commit message into ``(pr_id, target_ref)`` or ``None``.
A ``target_ref`` of ``None`` means the message names no target and the provider must
resolve one from the graph. Per-host formats live here so the provider stays host-agnostic.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from giraph.models import GiraphError

ParsedMerge = tuple[int | None, str | None]


def _normalize_ref(ref: str) -> str:
    """Strip trailing punctuation, a wrapping quote pair, and a leading ``refs/heads/`` (to a fixpoint)."""
    previous = None
    while ref != previous:
        previous = ref
        ref = ref.rstrip(".,;")
        if len(ref) >= 2 and ref[0] == ref[-1] and ref[0] in "'\"":
            ref = ref[1:-1]
        if ref.startswith("refs/heads/"):
            ref = ref[len("refs/heads/") :]
    return ref


class MergeConvention(ABC):
    @abstractmethod
    def parse(self, message: str) -> ParsedMerge | None:
        """Return ``(pr_id, target_ref)`` for a merge message, or ``None``."""


class PlainConvention(MergeConvention):
    """git's own default merge message: ``Merge branch 'x' into Y`` (local or remote-tracking).

    git omits ``into Y`` when merging onto the default branch, so the bare form parses with
    an unknown target for the provider to attribute from the merge's first-parent spine.
    """

    _BRANCH = re.compile(r"Merge (?:remote-tracking )?branch '[^']+'(?: of \S+)? into (\S+)")
    _BARE = re.compile(r"^Merge (?:remote-tracking )?branch '[^']+'(?: of \S+)?[ \t]*$", re.MULTILINE)

    def parse(self, message: str) -> ParsedMerge | None:
        match = self._BRANCH.search(message)
        if match:
            return (None, _normalize_ref(match.group(1)))
        return (None, None) if self._BARE.search(message) else None


class BitbucketConvention(PlainConvention):
    """Bitbucket Server merges (current, automated release sync, legacy), else plain."""

    _PR_ID = re.compile(r"Pull request #(\d+)")
    _MERGE_IN = re.compile(r"Merge in \S+ from \S+ to (?:refs/heads/)?(\S+)")
    _AUTO = re.compile(r"Automatic merge from \S+ -> (?:refs/heads/)?(\S+)")
    _OLD = re.compile(r"Merge pull request #(\d+) in \S+ from \S+ to (?:refs/heads/)?(\S+)")

    def parse(self, message: str) -> ParsedMerge | None:
        merge_in = self._MERGE_IN.search(message)
        if merge_in:
            pr_id = self._PR_ID.search(message)
            return (int(pr_id.group(1)) if pr_id else None, _normalize_ref(merge_in.group(1)))
        auto = self._AUTO.search(message)
        if auto:
            return (None, _normalize_ref(auto.group(1)))
        old = self._OLD.search(message)
        if old:
            return (int(old.group(1)), _normalize_ref(old.group(2)))
        return super().parse(message)


class GitLabConvention(PlainConvention):
    """GitLab merge requests: quoted target + optional MR-id trailer; else plain."""

    _MR = re.compile(r"Merge branch '[^']+' into '([^']+)'")
    _MR_ID = re.compile(r"See merge request \S+!(\d+)")

    def parse(self, message: str) -> ParsedMerge | None:
        merge = self._MR.search(message)
        if merge:
            mr_id = self._MR_ID.search(message)
            return (int(mr_id.group(1)) if mr_id else None, _normalize_ref(merge.group(1)))
        return super().parse(message)


CONVENTIONS: dict[str, MergeConvention] = {
    "plain": PlainConvention(),
    "bitbucket": BitbucketConvention(),
    "gitlab": GitLabConvention(),
}


def get_convention(name: str) -> MergeConvention:
    try:
        return CONVENTIONS[name]
    except KeyError:
        raise GiraphError(f"unknown convention '{name}'; valid: {', '.join(CONVENTIONS)}") from None
