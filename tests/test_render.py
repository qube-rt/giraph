"""Tests for the DOT/decision rendering (pure, no git or provider)."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import pytest

from giraph.config import Config
from giraph.models import ERROR, REAL, TRANSPORT, UNMATCHED, Outcome, Report
from giraph.render import decisions_table, render_report, to_dot, to_svg


def _config(*, drop_orphan_islands: bool = False, merged_in_arrow: str = "in") -> Config:
    return Config(
        eligible_paths=(),
        accept_refs=("main",),
        accept_prefixes=("release/",),
        convention="bitbucket",
        drop_orphan_islands=drop_orphan_islands,
        merged_in_arrow=merged_in_arrow,
    )


def _report(
    outcomes: list[Outcome], considered: int | None = None, frm: str = "0" * 40, to: str = "1" * 40
) -> Report:
    report = Report(
        from_sha=frm, to_sha=to, considered_merges=considered if considered is not None else len(outcomes)
    )
    report.outcomes = outcomes
    return report


def test_real_node_bold_mainline_edge_and_dashed_incoming():
    target, mainline, incoming = "a" * 40, "b" * 40, "c" * 40
    report = _report(
        [
            Outcome(
                target_sha=target,
                classification=REAL,
                mainline=mainline,
                incoming=incoming,
                pr_id=7,
                pr_target_ref="release/1.0",
            )
        ],
        frm=mainline,
        to=target,
    )

    dot = to_dot(report, parents_by_sha={target: [mainline, incoming]})

    assert f'"{target}" [label="aaaaaaaaaaaa\\nreal\\nPR#7 -> release/1.0", fillcolor="#1a7f37"' in dot
    assert f'"{target}" -> "{mainline}" [color="#1a7f37", penwidth=2.0, label="base"];' in dot
    assert f'"{incoming}" -> "{target}" [style=dashed, color="#8250df", label="merged in"];' in dot
    # the merged-in tip is not itself evaluated -> drawn as a context node
    assert f'"{incoming}" [label="cccccccccccc", shape=ellipse' in dot


def test_context_node_labels_carry_branch_when_known():
    target, mainline, incoming = "a" * 40, "b" * 40, "c" * 40
    report = _report(
        [Outcome(target_sha=target, classification=REAL, mainline=mainline, incoming=incoming)],
        frm=mainline,
        to=target,
    )

    dot = to_dot(
        report,
        parents_by_sha={target: [mainline, incoming]},
        branches={mainline: "main~2", incoming: "feature/x"},
    )

    assert f'"{mainline}" [label="bbbbbbbbbbbb\\nmain~2", shape=ellipse' in dot
    assert f'"{incoming}" [label="cccccccccccc\\nfeature/x", shape=ellipse' in dot


def test_legend_describes_edge_arrows_per_config():
    report = _report(
        [Outcome("a" * 40, REAL, "b" * 40, "c" * 40, pr_id=1, pr_target_ref="main")],
        frm="b" * 40,
        to="a" * 40,
    )
    pbs = {"a" * 40: ["b" * 40, "c" * 40]}

    in_dot = to_dot(report, parents_by_sha=pbs, merged_in_arrow="in")
    assert "base arrow points to the first-parent of a commit" in in_dot
    assert "merged in arrow points to the commit that merged the change in" in in_dot

    out_dot = to_dot(report, parents_by_sha=pbs, merged_in_arrow="out")
    assert "merged in arrow points to the commit that was merged in" in out_dot


def test_merged_in_arrow_out_reverses_dashed_edge():
    target, mainline, incoming = "a" * 40, "b" * 40, "c" * 40
    report = _report(
        [Outcome(target, REAL, mainline, incoming, pr_id=7, pr_target_ref="main")], frm=mainline, to=target
    )

    dot = to_dot(report, parents_by_sha={target: [mainline, incoming]}, merged_in_arrow="out")

    assert f'"{target}" -> "{incoming}" [style=dashed, color="#8250df", label="merged in"];' in dot


def test_no_pr_node_shows_branch_when_no_target_parsed():
    sha, base, incoming = "a" * 40, "b" * 40, "c" * 40
    report = _report([Outcome(sha, UNMATCHED, base, incoming)], frm=base, to=sha)

    dot = to_dot(report, parents_by_sha={sha: [base, incoming]}, branches={sha: "feature/pch"})

    assert f'"{sha}" [label="aaaaaaaaaaaa\\nno PR\\nfeature/pch"' in dot


def test_drop_orphan_islands_hides_pure_no_pr_components():
    real, base, incoming = "a" * 40, "b" * 40, "c" * 40
    orphan, op_base, op_incoming = "d" * 40, "e" * 40, "f" * 40
    outcomes = [
        Outcome(real, REAL, base, incoming, pr_id=1, pr_target_ref="main"),
        Outcome(orphan, UNMATCHED, op_base, op_incoming),
    ]
    report = _report(outcomes, frm=base, to=real)
    pbs = {real: [base, incoming], orphan: [op_base, op_incoming]}

    pruned = to_dot(report, parents_by_sha=pbs, drop_orphan_islands=True)
    assert f'"{orphan}"' not in pruned
    assert f'"{op_base}"' not in pruned and f'"{op_incoming}"' not in pruned
    assert f'"{real}"' in pruned
    assert "orphan islands hidden" in pruned
    assert "legend_unmatched" not in pruned  # nothing no-PR left visible

    shown = to_dot(report, parents_by_sha=pbs, drop_orphan_islands=False)
    assert f'"{orphan}"' in shown and "legend_unmatched" in shown


def test_transport_and_unmatched_styles_and_decision_table():
    outcomes = [
        Outcome(
            target_sha="1" * 40,
            classification=TRANSPORT,
            mainline="2" * 40,
            incoming="5" * 40,
            pr_id=99,
            pr_target_ref="release/2.0",
            detail="incoming side carries ...",
        ),
        Outcome(target_sha="3" * 40, classification=UNMATCHED, mainline="4" * 40, incoming="6" * 40),
    ]
    report = _report(outcomes, considered=2)

    dot = to_dot(report, parents_by_sha={"1" * 40: ["2" * 40, "5" * 40], "3" * 40: ["4" * 40, "6" * 40]})
    assert '"legend_transport"' in dot and "#57606a" in dot
    assert '"legend_unmatched"' in dot and "#d0d7de" in dot
    assert "legend_real" not in dot  # no real merges in this window -> not in legend

    table = decisions_table(report)
    assert "transport" in table and "#99" in table
    assert "release/2.0" in table
    assert "merges-examined=2" in table


def test_to_svg_renders_when_graphviz_available():
    if shutil.which("dot") is None:
        pytest.skip("graphviz 'dot' not installed")
    svg = to_svg("digraph { a -> b; }")
    assert "<svg" in svg


def test_dot_escapes_injection_in_labels():
    evil = 'main\\"];injected[label="PWNED"]'  # backslash + quote break-out
    report = _report([Outcome("a" * 40, UNMATCHED, "b" * 40, "c" * 40, pr_id=None, pr_target_ref=evil)])
    dot = to_dot(report, parents_by_sha={"a" * 40: ["b" * 40, "c" * 40]})

    assert 'injected[label="PWNED"]' not in dot  # not a live node
    assert "\\\\" in dot and '\\"' in dot  # backslash doubled, then quote escaped
    if shutil.which("dot") is not None:
        svg = to_svg(dot)
        assert "<svg" in svg
        assert "<title>injected</title>" not in svg  # payload never became a graph node


def test_table_sanitizes_control_chars_in_cells():
    outcome = Outcome(
        "1" * 40, TRANSPORT, "2" * 40, "5" * 40, pr_id=9, pr_target_ref="rel\nX", detail="carries\tstuff"
    )
    table = decisions_table(_report([outcome], considered=1))

    assert "rel X" in table
    assert "carries stuff" in table
    assert "rel\nX" not in table


class TestRenderReport:
    def test_builds_parents_and_branch_labels_from_repo(self):
        target, mainline, incoming = "a" * 40, "b" * 40, "c" * 40
        report = _report(
            [Outcome(target, REAL, mainline, incoming, pr_id=7, pr_target_ref="main")],
            frm=mainline,
            to=target,
        )
        repo = MagicMock()
        repo.branches_for.return_value = {mainline: "main~2", incoming: "main~2"}

        dot = render_report(report, repo=repo, config=_config(), fmt="dot")

        assert f'"{target}" [label="aaaaaaaaaaaa\\nreal\\nPR#7 -> main"' in dot
        # context node labels come from one batched repo.branches_for with the accept-ref patterns
        assert f'"{incoming}" [label="cccccccccccc\\nmain~2", shape=ellipse' in dot
        assert repo.branches_for.call_count == 1
        assert sorted(repo.branches_for.call_args.args[0]) == sorted([mainline, incoming])
        assert repo.branches_for.call_args.args[1] == ("*/main", "*/release/*")

    def test_drop_orphan_islands_flows_from_config(self):
        real, base, incoming = "a" * 40, "b" * 40, "c" * 40
        orphan, ob, oi = "d" * 40, "e" * 40, "f" * 40
        report = _report(
            [
                Outcome(real, REAL, base, incoming, pr_id=1, pr_target_ref="main"),
                Outcome(orphan, UNMATCHED, ob, oi),
            ],
            frm=base,
            to=real,
        )
        repo = MagicMock()
        repo.branches_for.return_value = {}

        dot = render_report(report, repo=repo, config=_config(drop_orphan_islands=True), fmt="dot")

        assert f'"{orphan}"' not in dot
        assert "orphan islands hidden" in dot

    def test_svg_format_renders_via_graphviz(self):
        if shutil.which("dot") is None:
            pytest.skip("graphviz 'dot' not installed")
        report = _report(
            [Outcome("a" * 40, REAL, "b" * 40, "c" * 40, pr_id=1, pr_target_ref="main")],
            frm="b" * 40,
            to="a" * 40,
        )
        repo = MagicMock()
        repo.branches_for.return_value = {}

        assert "<svg" in render_report(report, repo=repo, config=_config(), fmt="svg")


def test_drop_orphan_islands_drops_error_only_component():
    real, rb, ri = "a" * 40, "b" * 40, "c" * 40
    err, eb, ei = "d" * 40, "e" * 40, "f" * 40
    outcomes = [
        Outcome(real, REAL, rb, ri, pr_id=1, pr_target_ref="main"),
        Outcome(err, ERROR, eb, ei, detail="boom"),
    ]
    report = _report(outcomes, frm=rb, to=real)
    pbs = {real: [rb, ri], err: [eb, ei]}

    pruned = to_dot(report, parents_by_sha=pbs, drop_orphan_islands=True)
    assert f'"{err}"' not in pruned  # error-only component has no tracked merge -> dropped
    assert f'"{real}"' in pruned
    assert "orphan islands hidden" in pruned
