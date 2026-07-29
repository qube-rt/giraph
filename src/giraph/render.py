"""Render a classification :class:`Report` as a Graphviz DOT graph (or SVG) + a decision table."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence

from giraph.config import Config, name_rev_patterns
from giraph.gitgraph import GitRepo
from giraph.models import (
    ERROR,
    INELIGIBLE,
    MIXED,
    REAL,
    TRACKED_CLASSES,
    TRANSPORT,
    UNMATCHED,
    Classification,
    GiraphError,
    Report,
    short_sha,
)

# class -> (fill, font, node label, legend description)
CLASS_STYLE: dict[Classification, tuple[str, str, str, str]] = {
    REAL: ("#1a7f37", "white", "real", "introduces new in-scope changes"),
    TRANSPORT: ("#57606a", "white", "transport", "every PR it carries was already merged elsewhere"),
    MIXED: ("#bc4c00", "white", "edited transport", "transport that also edited in-scope files"),
    INELIGIBLE: ("#d4a72c", "black", "ineligible", "skipped by path filter (no watched paths)"),
    UNMATCHED: ("#d0d7de", "black", "no PR", "merge introduced without a pull request"),
    ERROR: ("#cf222e", "white", "error", "could not classify"),
}
_CONTEXT_FILL = "#f6f8fa"
_CONTEXT_FONT = "#57606a"
_CONTEXT_LEGEND = "context: a parent commit outside the window, not classified"
_REAL_EDGE = "#1a7f37"
_MAINLINE_EDGE = "#57606a"
_INCOMING_EDGE = "#8250df"

DOT_TIMEOUT = 60
_TABLE_CELL_MAX = 60


def _strip_control(value: str) -> str:
    """Replace control characters with spaces (1:1, so column widths are preserved)."""
    return "".join(" " if ord(ch) < 0x20 or ord(ch) == 0x7F else ch for ch in value)


def _dot_escape(value: str) -> str:
    """Escape a label component for a double-quoted DOT string (backslash before quotes)."""
    return _strip_control(value).replace("\\", "\\\\").replace('"', '\\"')


def _node_label(
    classification: Classification,
    sha: str,
    pr_id: int | None,
    pr_target_ref: str | None,
    branch: str | None = None,
) -> str:
    parts = [short_sha(sha), CLASS_STYLE[classification][2]]
    if pr_id is not None:
        parts.append(f"PR#{pr_id} -> {pr_target_ref}")
    elif pr_target_ref:
        parts.append(f"-> {pr_target_ref}")
    elif branch:
        parts.append(branch)
    return "\\n".join(_dot_escape(part) for part in parts)


def context_shas(report: Report, parents_by_sha: Mapping[str, Sequence[str]]) -> set[str]:
    """Parent shas not themselves evaluated (rendered as context ellipse nodes)."""
    evaluated = {outcome.target_sha for outcome in report.outcomes}
    return {p for parents in parents_by_sha.values() for p in parents if p not in evaluated}


def _drawn_components(report: Report, parents_by_sha: Mapping[str, Sequence[str]]) -> list[set[str]]:
    """Connected components of the drawn graph (each merge linked to its two parents)."""
    adj: dict[str, set[str]] = {}

    def link(a: str, b: str) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for outcome in report.outcomes:
        adj.setdefault(outcome.target_sha, set())
        parents = list(parents_by_sha.get(outcome.target_sha, []))
        if parents:
            link(outcome.target_sha, parents[0])
        if len(parents) > 1:
            link(parents[1], outcome.target_sha)

    seen: set[str] = set()
    components: list[set[str]] = []
    for node in adj:
        if node in seen:
            continue
        stack, comp = [node], set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.add(cur)
            stack.extend(adj[cur] - seen)
        components.append(comp)
    return components


def orphan_islands(report: Report, parents_by_sha: Mapping[str, Sequence[str]]) -> list[set[str]]:
    """Drawn components containing no PR-matched merge (no real/transport/edited-transport/ineligible)."""
    cls = {outcome.target_sha: outcome.classification for outcome in report.outcomes}
    islands = []
    for comp in _drawn_components(report, parents_by_sha):
        evaluated = [sha for sha in comp if sha in cls]
        if evaluated and not any(cls[sha] in TRACKED_CLASSES for sha in evaluated):
            islands.append(comp)
    return islands


def to_dot(
    report: Report,
    *,
    parents_by_sha: Mapping[str, Sequence[str]],
    branches: Mapping[str, str] | None = None,
    drop_orphan_islands: bool = False,
    merged_in_arrow: str = "in",
) -> str:
    """Return a DOT digraph of the report's classification.

    ``drop_orphan_islands`` hides drawn components with no PR-matched merge (pure no-PR islands).
    ``merged_in_arrow`` points the dashed edge ``"in"`` (into the merge) or ``"out"`` (to its tip).
    """
    branches = branches or {}
    islands = orphan_islands(report, parents_by_sha) if drop_orphan_islands else []
    hidden: set[str] = set().union(*islands) if islands else set()
    hidden_merges = sum(1 for outcome in report.outcomes if outcome.target_sha in hidden)
    note = f"  ({hidden_merges} merges in {len(islands)} orphan islands hidden)" if hidden else ""
    frm = _dot_escape(short_sha(report.from_sha))
    to = _dot_escape(short_sha(report.to_sha))
    lines: list[str] = ["digraph giraph {", "  rankdir=TB;"]
    lines.append(
        f'  graph [fontname="monospace", labelloc="t", '
        f'label="giraph window ({frm}..{to}]  '
        f'merges examined={report.considered_merges}{note}"];'
    )
    lines.append('  node [shape=box, style="filled,rounded", fontname="monospace", fontsize=10];')
    lines.append('  edge [fontname="monospace", fontsize=8];')

    for outcome in report.outcomes:
        if outcome.target_sha in hidden:
            continue
        fill, font, _, _ = CLASS_STYLE[outcome.classification]
        label = _node_label(
            outcome.classification,
            outcome.target_sha,
            outcome.pr_id,
            outcome.pr_target_ref,
            branches.get(outcome.target_sha),
        )
        lines.append(f'  "{outcome.target_sha}" [label="{label}", fillcolor="{fill}", fontcolor="{font}"];')

    drawn_context = sorted(context_shas(report, parents_by_sha) - hidden)
    for sha in drawn_context:
        parts = [_dot_escape(short_sha(sha))]
        branch = branches.get(sha)
        if branch:
            parts.append(_dot_escape(branch))
        label = "\\n".join(parts)
        lines.append(
            f'  "{sha}" [label="{label}", shape=ellipse, style="filled,dashed", '
            f'fillcolor="{_CONTEXT_FILL}", fontcolor="{_CONTEXT_FONT}"];'
        )

    for outcome in report.outcomes:
        if outcome.target_sha in hidden:
            continue
        parent_list = list(parents_by_sha.get(outcome.target_sha, []))
        if parent_list:
            if outcome.classification == REAL:
                lines.append(
                    f'  "{outcome.target_sha}" -> "{parent_list[0]}" '
                    f'[color="{_REAL_EDGE}", penwidth=2.0, label="base"];'
                )
            else:
                lines.append(
                    f'  "{outcome.target_sha}" -> "{parent_list[0]}" '
                    f'[color="{_MAINLINE_EDGE}", label="base"];'
                )
        if len(parent_list) > 1:
            tail, head = (
                (parent_list[1], outcome.target_sha)
                if merged_in_arrow == "in"
                else (outcome.target_sha, parent_list[1])
            )
            lines.append(
                f'  "{tail}" -> "{head}" [style=dashed, color="{_INCOMING_EDGE}", label="merged in"];'
            )

    lines.append("  subgraph cluster_legend {")
    lines.append('    label="legend"; fontname="monospace"; style=dashed; color="#8b949e";')
    visible_counts: dict[Classification, int] = {}
    for outcome in report.outcomes:
        if outcome.target_sha not in hidden:
            visible_counts[outcome.classification] = visible_counts.get(outcome.classification, 0) + 1
    shown = [classification for classification in CLASS_STYLE if visible_counts.get(classification)]
    for classification in shown:
        fill, font, name, description = CLASS_STYLE[classification]
        lines.append(
            f'    "legend_{classification}" [label="{name}: {description}", '
            f'fillcolor="{fill}", fontcolor="{font}"];'
        )
    if drawn_context:
        lines.append(
            f'    "legend_context" [label="{_CONTEXT_LEGEND}", shape=ellipse, style="filled,dashed", '
            f'fillcolor="{_CONTEXT_FILL}", fontcolor="{_CONTEXT_FONT}"];'
        )
    merged_note = (
        "merged in arrow points to the commit that merged the change in"
        if merged_in_arrow == "in"
        else "merged in arrow points to the commit that was merged in"
    )
    lines.append(
        '    "legend_edge_base" [label="base arrow points to the first-parent of a commit", '
        "shape=plaintext, style=solid];"
    )
    lines.append(f'    "legend_edge_merged" [label="{merged_note}", shape=plaintext, style=solid];')
    chain = (
        [f"legend_{classification}" for classification in shown]
        + (["legend_context"] if drawn_context else [])
        + ["legend_edge_base", "legend_edge_merged"]
    )
    for prev, curr in zip(chain, chain[1:], strict=False):
        lines.append(f'    "{prev}" -> "{curr}" [style=invis];')
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def decisions_table(report: Report) -> str:
    """Return an aligned text table of every decision + the summary line."""
    header = ("CLASS", "MERGE", "BASE", "PR", "TARGET", "DETAIL")
    rows = [header]
    for outcome in report.outcomes:
        rows.append(
            (
                CLASS_STYLE[outcome.classification][2],
                short_sha(outcome.target_sha),
                short_sha(outcome.mainline),
                f"#{outcome.pr_id}" if outcome.pr_id is not None else "-",
                _strip_control(outcome.pr_target_ref or "-")[:_TABLE_CELL_MAX],
                _strip_control(outcome.detail or "")[:_TABLE_CELL_MAX],
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    body = "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)
    return f"{body}\n\n{report.summary_line()}"


def to_svg(dot: str) -> str:
    """Render DOT to SVG via the graphviz ``dot`` binary."""
    if shutil.which("dot") is None:
        raise GiraphError("graphviz 'dot' not found on PATH; install graphviz to render SVG")
    try:
        result = subprocess.run(
            ["dot", "-Tsvg"],
            input=dot,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=DOT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise GiraphError(f"dot timed out after {DOT_TIMEOUT}s") from exc
    except subprocess.CalledProcessError as exc:
        raise GiraphError(f"dot failed: {exc.stderr.strip()}") from exc
    return result.stdout


def render_report(report: Report, *, repo: GitRepo, config: Config, fmt: str = "svg") -> str:
    """Render a classification :class:`Report` to ``svg`` or ``dot`` in one call.

    Resolves the parent map and integration-branch labels the graph draws with — the
    same setup the CLI performs — from ``config`` + ``repo``, so a caller holding a
    ``Report`` need not rebuild that wiring. ``fmt="svg"`` needs graphviz ``dot`` on PATH.
    """
    parents_by_sha = {outcome.target_sha: [outcome.mainline, outcome.incoming] for outcome in report.outcomes}
    prefer = name_rev_patterns(config)
    label_shas = context_shas(report, parents_by_sha) | {
        outcome.target_sha
        for outcome in report.outcomes
        if outcome.classification == UNMATCHED and outcome.pr_target_ref is None
    }
    branches = repo.branches_for(sorted(label_shas), prefer)
    dot = to_dot(
        report,
        parents_by_sha=parents_by_sha,
        branches=branches,
        drop_orphan_islands=config.drop_orphan_islands,
        merged_in_arrow=config.merged_in_arrow,
    )
    return to_svg(dot) if fmt == "svg" else dot
