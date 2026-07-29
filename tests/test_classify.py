"""End-to-end classification over small, real git repositories."""

from __future__ import annotations

import subprocess

import pytest

from giraph.classify import Classifier
from giraph.gitgraph import GitRepo
from giraph.models import ERROR, INELIGIBLE, MIXED, REAL, TRANSPORT, UNMATCHED
from giraph.providers.conventions import BitbucketConvention, MergeConvention, PlainConvention
from giraph.providers.message import MessageProvider

ELIGIBLE = ("src/",)
ACCEPT_REFS = ("main",)
ACCEPT_PREFIXES = ("release/",)
ATTRIBUTE_REFS = ("*/main", "*/release/*")


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, rel, content, msg) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _merge_pr(repo, into_branch, feature_branch, pr_id, target_ref) -> str:
    _git(repo, "checkout", "-q", into_branch)
    msg = (
        f"Pull request #{pr_id}: {feature_branch}\n\nMerge in PROJ/repo from {feature_branch} to {target_ref}"
    )
    _git(repo, "merge", "-q", "--no-ff", feature_branch, "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _commit(root, "README", "root", "root")
    return root


def _classify(
    repo_path,
    from_sha,
    to_sha,
    *,
    convention: MergeConvention | None = None,
    eligible: tuple[str, ...] = ELIGIBLE,
):
    repo = GitRepo(repo_path)
    return Classifier(
        provider=MessageProvider(
            repo=repo,
            convention=convention or BitbucketConvention(),
            attribute_refs=ATTRIBUTE_REFS,
        ),
        repo=repo,
        eligible_paths=eligible,
        accept_refs=ACCEPT_REFS,
        accept_prefixes=ACCEPT_PREFIXES,
    ).run(from_sha, to_sha)


def test_branch_for_names_the_branch(repo):
    _git(repo, "checkout", "-q", "-b", "feature/z")
    sha = _commit(repo, "src/z.cpp", "z", "z work")
    assert GitRepo(repo).branch_for(sha) == "feature/z"


def test_branch_for_prefers_integration_branch_on_first_parent_spine(repo):
    _git(repo, "checkout", "-q", "-b", "feature/q")
    tip = _commit(repo, "src/q.cpp", "q", "q work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature/q", "-m", "merge q")
    merge = _git(repo, "rev-parse", "HEAD")
    gr = GitRepo(repo)

    # the merge sits on main's first-parent spine -> integration branch wins
    assert gr.branch_for(merge, prefer=("*/main",)) == "main"
    # a commit back along that spine names bare (position suffix stripped)
    assert gr.branch_for(_git(repo, "rev-parse", "main~1"), prefer=("*/main",)) == "main"
    # the feature tip is a side (second parent) -> falls back to the feature branch
    assert gr.branch_for(tip, prefer=("*/main",)) == "feature/q"


def test_intrinsic_pr_merge_is_real(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/x")
    _commit(repo, "src/a.cpp", "code", "add a")
    head = _merge_pr(repo, "main", "feature/x", 4499, "main")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [REAL]
    assert report.outcomes[0].pr_id == 4499


def test_merge_touching_no_eligible_paths_is_ineligible(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/docs")
    _commit(repo, "docs/readme.md", "x", "docs only")
    head = _merge_pr(repo, "main", "feature/docs", 10, "main")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [INELIGIBLE]


def test_non_pr_merge_is_unmatched(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "topic")
    _commit(repo, "src/b.cpp", "code", "b")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "topic", "-m", "just merging stuff")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [UNMATCHED]


def test_merge_into_untracked_ref_is_unmatched_but_records_ref(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/topic")
    _git(repo, "checkout", "-q", "-b", "feature/side")
    _commit(repo, "src/s.cpp", "code", "side")
    _git(repo, "checkout", "-q", "feature/topic")
    msg = "Merge branch 'feature/side' into feature/topic"
    _git(repo, "merge", "-q", "--no-ff", "feature/side", "-m", msg)
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [UNMATCHED]
    assert report.outcomes[0].pr_target_ref == "feature/topic"
    assert report.outcomes[0].pr_id is None


def test_release_backmerge_carrying_a_pr_is_transport(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/r.cpp", "rel", "rel work")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "release/1.0",
        "-m",
        "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main",
    )
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    # the release-side feature merge classifies real; the main back-merge is transport
    assert [o.classification for o in report.outcomes] == [REAL, TRANSPORT]
    transport = report.outcomes[1]
    assert transport.pr_id == 3 and "PR#2" in transport.detail


def test_transport_touching_no_eligible_paths_stays_transport(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "docs/only.md", "docs", "docs work")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "release/1.0",
        "-m",
        "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main",
    )
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    # release-side feature merge is intrinsic + no eligible paths -> ineligible;
    # the back-merge carries PR#2 and touches no eligible paths -> still transport, not ineligible
    assert [o.classification for o in report.outcomes] == [INELIGIBLE, TRANSPORT]


def test_backmerge_with_merge_introduced_edit_is_mixed(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/r.cpp", "rel", "rel work")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "release/1.0")
    # an eligible edit introduced by the merge itself, present in neither parent
    (repo / "src" / "evil.cpp").write_text("sneaky")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [REAL, MIXED]
    assert "src/evil.cpp" in report.outcomes[1].detail


def test_bare_merge_onto_default_branch_is_attributed_to_that_branch(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/bare")
    _commit(repo, "src/bare.cpp", "code", "bare work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature/bare")
    head = _git(repo, "rev-parse", "HEAD")
    # git omits "into <ref>" when merging onto the default branch
    assert _git(repo, "log", "-1", "--format=%s") == "Merge branch 'feature/bare'"

    report = _classify(repo, base, head, convention=PlainConvention())

    assert [o.classification for o in report.outcomes] == [REAL]
    assert report.outcomes[0].pr_target_ref == "main"


def test_bare_merges_are_attributed_past_a_closer_feature_branch(repo):
    base = _git(repo, "rev-parse", "HEAD")
    for name in ("feature/a", "feature/b", "feature/c"):
        _git(repo, "checkout", "-q", "-b", name)
        _commit(repo, f"src/{name[-1]}.cpp", "code", f"work on {name}")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", name)
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head, convention=PlainConvention())

    # each later branch is cut from main, so name-rev's closest ref is a feature branch, not main
    assert [o.classification for o in report.outcomes] == [REAL, REAL, REAL]
    assert {o.pr_target_ref for o in report.outcomes} == {"main"}


def test_bare_merge_off_any_branch_spine_stays_unmatched(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/one")
    _commit(repo, "src/one.cpp", "one", "one")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature/one")
    head = _git(repo, "rev-parse", "HEAD")
    # detach the spine: no branch names the merge any more, so nothing may be attributed
    _git(repo, "checkout", "-q", "--detach", "HEAD")
    _git(repo, "branch", "-q", "-D", "main")

    report = _classify(repo, base, head, convention=PlainConvention())

    assert [o.classification for o in report.outcomes] == [UNMATCHED]


def test_non_ascii_path_is_seen_by_the_eligibility_gate(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/accents")
    _commit(repo, "src/café.cpp", "code", "accented work")
    head = _merge_pr(repo, "main", "feature/accents", 11, "main")

    report = _classify(repo, base, head)

    # git C-quotes non-ASCII paths without -z, which would hide this merge as ineligible
    assert [o.classification for o in report.outcomes] == [REAL]


def test_merge_introduced_edit_to_a_non_ascii_path_is_mixed(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/r.cpp", "rel", "rel work")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "release/1.0")
    (repo / "src" / "naïve.cpp").write_text("sneaky")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [REAL, MIXED]
    assert "src/naïve.cpp" in report.outcomes[1].detail


def test_merge_introduced_edit_is_mixed_with_no_eligible_paths_configured(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/r.cpp", "rel", "rel work")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-commit", "release/1.0")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "evil.md").write_text("sneaky")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head, eligible=())

    # no configured prefixes means every path is in scope, so the resolution is still visible
    assert [o.classification for o in report.outcomes] == [REAL, MIXED]
    assert "docs/evil.md" in report.outcomes[1].detail


def test_octopus_merge_is_error(repo):
    base = _git(repo, "rev-parse", "HEAD")
    for name in ("f1", "f2", "f3"):
        _git(repo, "checkout", "-q", "main")
        _git(repo, "checkout", "-q", "-b", name)
        _commit(repo, f"src/{name}.cpp", name, name)
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "f1", "f2", "f3", "-m", "octopus")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    assert [o.classification for o in report.outcomes] == [ERROR]
    assert "octopus" in report.outcomes[0].detail


def test_verbatim_conflict_side_pick_is_transport_not_mixed(repo):
    _commit(repo, "src/shared.cpp", "line1\n", "seed shared")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/shared.cpp", "release-version\n", "rel edits shared")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "src/shared.cpp", "main-version\n", "main edits shared")
    # back-merge conflicts; resolve by taking the incoming side verbatim (zero novel content)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "--no-commit", "release/1.0"],
        capture_output=True,
        text=True,
    )
    _git(repo, "checkout", "--theirs", "src/shared.cpp")
    _git(repo, "add", "src/shared.cpp")
    _git(repo, "commit", "-q", "-m", "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main")
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    # a verbatim side-pick introduces nothing novel -> plain transport, not edited transport
    assert [o.classification for o in report.outcomes] == [REAL, TRANSPORT]


def test_clean_nonoverlapping_backmerge_is_transport_not_mixed(repo):
    _commit(repo, "src/shared.cpp", "top\nmid\nbottom\n", "seed shared")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "release/1.0")
    _git(repo, "checkout", "-q", "-b", "feature/rel")
    _commit(repo, "src/shared.cpp", "TOP\nmid\nbottom\n", "rel edits top")
    _merge_pr(repo, "release/1.0", "feature/rel", 2, "release/1.0")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "src/shared.cpp", "top\nmid\nBOTTOM\n", "main edits bottom")
    _git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "release/1.0",
        "-m",
        "Merge pull request #3 in PROJ/repo from release/1.0 to refs/heads/main",
    )
    head = _git(repo, "rev-parse", "HEAD")

    report = _classify(repo, base, head)

    # non-overlapping edits auto-merge cleanly (no human resolution) -> transport, not mixed
    assert [o.classification for o in report.outcomes] == [REAL, TRANSPORT]
