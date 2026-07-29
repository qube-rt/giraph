"""Classify each merge in a ``from..to`` window as real vs transport.

Order: ``unmatched`` first; then transport/mixed vs real from the merge graph; an
intrinsic (``real``) merge that touched no eligible paths is downgraded to ``ineligible``.
"""

from __future__ import annotations

import logging

from giraph.gitgraph import GitRepo
from giraph.models import (
    ERROR,
    INELIGIBLE,
    MIXED,
    REAL,
    TRANSPORT,
    UNMATCHED,
    Classification,
    GiraphError,
    Outcome,
    PullRequest,
    Report,
    paths_in_scope,
    short_sha,
    touches_any_prefix,
)
from giraph.providers import PullRequestProvider

log = logging.getLogger("giraph")


class Classifier:
    def __init__(
        self,
        *,
        provider: PullRequestProvider,
        repo: GitRepo,
        eligible_paths: tuple[str, ...],
        accept_refs: tuple[str, ...],
        accept_prefixes: tuple[str, ...],
    ) -> None:
        self.provider = provider
        self.repo = repo
        self.eligible_paths = eligible_paths
        self.accept_refs = accept_refs
        self.accept_prefixes = accept_prefixes

    def _accepted(self, target_ref: str) -> bool:
        return target_ref in self.accept_refs or any(target_ref.startswith(p) for p in self.accept_prefixes)

    def _terminating_pr(self, prs: list[PullRequest], incoming: str) -> PullRequest | None:
        """Return the accepted-ref PR whose source tip is this merge's incoming parent."""
        keyed: dict[int, PullRequest] = {}
        unkeyed: list[PullRequest] = []
        for pr in prs:
            if pr.source_tip != incoming or not self._accepted(pr.target_ref):
                continue
            if pr.id is None:
                unkeyed.append(pr)
            else:
                keyed.setdefault(pr.id, pr)
        if len(keyed) > 1:
            ids = ", ".join(f"PR#{pr.id}->{pr.target_ref}" for pr in keyed.values())
            raise GiraphError(f"multiple PRs match incoming {short_sha(incoming)}: {ids}")
        if keyed:
            return next(iter(keyed.values()))
        return unkeyed[0] if unkeyed else None

    def _incoming_side_terminators(self, mainline: str, incoming: str) -> list[tuple[str, PullRequest]]:
        terminators: list[tuple[str, PullRequest]] = []
        for sha, commit_parents in self.repo.commits_with_parents(mainline, incoming):
            if len(commit_parents) < 2:
                continue
            pr = self._terminating_pr(self.provider.merges_for(sha), commit_parents[1])
            if pr:
                terminators.append((sha, pr))
        return terminators

    def _classify_transport(
        self, target_sha: str, mainline: str, incoming: str
    ) -> tuple[Classification, str]:
        terminators = self._incoming_side_terminators(mainline, incoming)
        if not terminators:
            return REAL, ""
        details = ", ".join(f"{short_sha(sha)}:PR#{pr.id}->{pr.target_ref}" for sha, pr in terminators)
        resolution = paths_in_scope(self.repo.merge_introduced_paths(target_sha), self.eligible_paths)
        if resolution:
            touched = ", ".join(resolution)
            return MIXED, f"incoming side carries {details}; merge resolution touched {touched}"
        return TRANSPORT, f"incoming side carries {details}"

    def run(self, from_sha: str, to_sha: str) -> Report:
        report = Report(from_sha=from_sha, to_sha=to_sha)
        merges = [(sha, ps) for sha, ps in self.repo.commits_with_parents(from_sha, to_sha) if len(ps) >= 2]
        report.considered_merges = len(merges)
        log.info(f"considering {len(merges)} merge commit(s) in ({short_sha(from_sha)}..{short_sha(to_sha)}]")

        for sha, commit_parents in merges:
            mainline, incoming = commit_parents[0], commit_parents[1]
            log.info(
                f"----- {short_sha(sha)} base={short_sha(mainline)} merged-in={short_sha(incoming)} -----"
            )
            if len(commit_parents) > 2:
                detail = f"octopus merge ({len(commit_parents)} parents) unsupported"
                log.error(f"  ERROR {short_sha(sha)} - {detail}")
                report.outcomes.append(Outcome(sha, ERROR, mainline, incoming, detail=detail))
                continue
            try:
                prs = self.provider.merges_for(sha)
                pr = self._terminating_pr(prs, incoming)
                if not pr:
                    parsed = next((p for p in prs if p.source_tip == incoming), None)
                    report.outcomes.append(
                        Outcome(
                            sha,
                            UNMATCHED,
                            mainline,
                            incoming,
                            pr_target_ref=parsed.target_ref if parsed else None,
                        )
                    )
                    continue
                classification, detail = self._classify_transport(sha, mainline, incoming)
                if (
                    classification == REAL
                    and self.eligible_paths
                    and not touches_any_prefix(self.repo.changed_paths(mainline, sha), self.eligible_paths)
                ):
                    classification, detail = INELIGIBLE, ""
            except GiraphError as exc:
                detail = f"{type(exc).__name__}: {exc}"
                log.error(f"  ERROR {short_sha(sha)} - {detail}")
                report.outcomes.append(Outcome(sha, ERROR, mainline, incoming, detail=detail))
                continue
            report.outcomes.append(
                Outcome(sha, classification, mainline, incoming, pr.id, pr.target_ref, detail)
            )

        return report
