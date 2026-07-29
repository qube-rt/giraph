"""giraph command-line entrypoint."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from giraph.classify import Classifier
from giraph.config import CONFIG_FILENAME, find_config, load_config, name_rev_patterns, resolve_config
from giraph.gitgraph import GitRepo
from giraph.models import ERROR, GiraphError, Report
from giraph.providers.conventions import CONVENTIONS, get_convention
from giraph.providers.message import MessageProvider
from giraph.render import decisions_table, render_report

log = logging.getLogger("giraph")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ERROR_OUTCOME = 3
EXIT_NOTHING_MATCHED = 4


def _audit_exit_code(report: Report) -> int:
    """Exit code for a completed run — a result nobody can act on is never a success."""
    if report.count(ERROR) > 0:
        return EXIT_ERROR_OUTCOME
    if report.considered_merges == 0:
        log.warning(
            "no merge commits in this window: giraph only understands merge-commit workflows, "
            "so a squash/rebase history classifies nothing"
        )
        return EXIT_NOTHING_MATCHED
    if report.matched_count() == 0:
        log.warning(
            f"none of the {report.considered_merges} merge(s) matched a PR into an accepted ref: "
            "check 'convention', 'accept_target_refs' and 'accept_target_ref_prefixes'"
        )
        return EXIT_NOTHING_MATCHED
    return EXIT_OK


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="giraph",
        description=(
            "Classify git merges as real vs transport noise and render the decisions. "
            "Merge-commit workflows only."
        ),
    )
    parser.add_argument("from_sha", help="Exclusive lower bound (range start).")
    parser.add_argument("to_sha", help="Inclusive upper bound (range end).")
    parser.add_argument(
        "--repo", default=".", help="Path inside the checkout (default: cwd); its repo root is discovered."
    )
    parser.add_argument(
        "--convention",
        choices=sorted(CONVENTIONS),
        default=None,
        help="Merge-message format to parse.",
    )
    parser.add_argument(
        "--eligible-path",
        action="append",
        dest="eligible_paths",
        default=None,
        help="Path prefix that makes a merge eligible (repeatable).",
    )
    parser.add_argument("--config", default=None, help="Path to giraph.toml (default: <repo>/giraph.toml).")
    parser.add_argument("--format", choices=["svg", "dot", "table"], default="svg")
    parser.add_argument(
        "--output", default="", help="Write the graph (DOT/SVG) to this path instead of stdout."
    )
    parser.add_argument("--strict", action="store_true", help="Require <from> on <to>'s first-parent chain.")
    parser.add_argument(
        "--drop-orphan-islands",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Hide graph components with no PR-matched merge (pure no-PR islands).",
    )
    parser.add_argument(
        "--merged-in-arrow",
        choices=["in", "out"],
        default=None,
        help="Direction of the dashed 'merged in' edge: in=into the merge (default), out=to its tip.",
    )
    args = parser.parse_args()

    try:
        repo = GitRepo.discover(Path(args.repo).resolve())
        if repo.is_shallow():
            log.warning(
                f"{repo.root} is a shallow clone: ancestry is truncated, so transport merges can "
                "misclassify; run against a full clone"
            )
        config_path = find_config(repo.root, Path(args.config) if args.config else None)
        log.info(
            f"config: {config_path}"
            if config_path
            else f"config: no {CONFIG_FILENAME} in {repo.root}, using defaults"
        )
        config = resolve_config(
            load_config(config_path),
            convention=args.convention,
            eligible_paths=args.eligible_paths,
            drop_orphan_islands=args.drop_orphan_islands,
            merged_in_arrow=args.merged_in_arrow,
        )
        if args.strict and not repo.is_first_parent_ancestor(args.from_sha, args.to_sha):
            raise GiraphError(f"{args.from_sha} is not on the first-parent path to {args.to_sha}")
        if not args.strict and not repo.is_ancestor(args.from_sha, args.to_sha):
            log.warning(
                f"{args.from_sha} is not an ancestor of {args.to_sha}; the window may be empty or unexpected"
            )
        report = Classifier(
            provider=MessageProvider(
                repo=repo,
                convention=get_convention(config.convention),
                attribute_refs=name_rev_patterns(config),
            ),
            repo=repo,
            eligible_paths=config.eligible_paths,
            accept_refs=config.accept_refs,
            accept_prefixes=config.accept_prefixes,
        ).run(args.from_sha, args.to_sha)
    except GiraphError as exc:
        log.error(f"giraph failed: {exc}")
        sys.exit(EXIT_FAILURE)

    table = decisions_table(report)
    exit_code = _audit_exit_code(report)
    if args.format == "table":
        sys.stdout.write(table + "\n")
        sys.exit(exit_code)

    log.info("\n" + table)
    try:
        payload = render_report(report, repo=repo, config=config, fmt=args.format)
    except GiraphError as exc:
        log.error(f"giraph failed: {exc}")
        sys.exit(EXIT_FAILURE)
    if args.output:
        try:
            Path(args.output).write_text(payload, encoding="utf-8")
        except OSError as exc:
            log.error(f"giraph failed: could not write {args.output}: {exc}")
            sys.exit(EXIT_FAILURE)
        log.info(f"wrote {'SVG' if args.format == 'svg' else 'DOT'} to {args.output}")
    else:
        sys.stdout.write(payload)
    sys.exit(exit_code)
