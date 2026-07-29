"""Tests for GitRepo ancestry helpers over real repositories."""

from __future__ import annotations

import subprocess

import pytest

from giraph.gitgraph import GitRepo
from giraph.models import GiraphError


def _git(repo, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a").write_text("1")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c1")
    (root / "a").write_text("2")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c2")
    return root


def test_is_ancestor(repo):
    gr = GitRepo(repo)
    c1 = _git(repo, "rev-parse", "HEAD~1")
    c2 = _git(repo, "rev-parse", "HEAD")
    assert gr.is_ancestor(c1, c2) is True
    assert gr.is_ancestor(c2, c1) is False
    assert gr.is_ancestor(c2, c2) is True  # reflexive


def test_is_ancestor_bad_rev_raises(repo):
    with pytest.raises(GiraphError):
        GitRepo(repo).is_ancestor("no-such-ref", "HEAD")


def test_is_first_parent_ancestor(repo):
    gr = GitRepo(repo)
    c1 = _git(repo, "rev-parse", "HEAD~1")
    c2 = _git(repo, "rev-parse", "HEAD")
    assert gr.is_first_parent_ancestor(c1, c2) is True
    assert gr.is_first_parent_ancestor(c2, c1) is False


def test_is_first_parent_ancestor_peels_annotated_tags(repo):
    _git(repo, "tag", "-a", "v1", "-m", "release", "HEAD~1")
    # the annotated tag's own oid is not a commit oid, so it is never on a commit chain
    assert _git(repo, "rev-parse", "v1") != _git(repo, "rev-parse", "HEAD~1")

    assert GitRepo(repo).is_first_parent_ancestor("v1", "HEAD") is True


def test_changed_paths_are_not_c_quoted(repo):
    (repo / "src").mkdir()
    (repo / "src" / "café.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "accented")

    assert GitRepo(repo).changed_paths("HEAD~1", "HEAD") == ["src/café.txt"]


def test_git_dir_in_the_environment_does_not_redirect_the_repo(repo, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "t")
    (other / "b").write_text("1")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "elsewhere")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert GitRepo(repo).commit_message("HEAD") == "c2"


def test_discover_finds_the_repo_root_from_a_subdirectory(repo):
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)

    assert GitRepo.discover(sub).root == repo


def test_is_shallow_detects_a_truncated_clone(repo, tmp_path):
    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)],
        check=True,
        capture_output=True,
    )

    assert GitRepo(repo).is_shallow() is False
    assert GitRepo(clone).is_shallow() is True


def test_branches_for_names_every_sha_in_one_batch(repo):
    c1 = _git(repo, "rev-parse", "HEAD~1")
    c2 = _git(repo, "rev-parse", "HEAD")

    assert GitRepo(repo).branches_for([c1, c2, c1]) == {c1: "main", c2: "main"}


def test_spine_branch_for_declines_side_branch_names(repo):
    _git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "b").write_text("side")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side")
    tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature/x", "-m", "merge x")
    _git(repo, "branch", "-q", "-D", "feature/x")
    gr = GitRepo(repo)

    assert gr.spine_branch_for(_git(repo, "rev-parse", "HEAD")) == "main"
    assert gr.spine_branch_for(tip) is None  # only reachable as main^2


def test_spine_branch_for_restricted_to_given_refs(repo):
    landed = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature/later")
    (repo / "c").write_text("later")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later")
    _git(repo, "checkout", "-q", "main")
    for name in ("d", "e"):
        (repo / name).write_text(name)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", name)
    gr = GitRepo(repo)

    # main moved on, so the branch cut from it is now the closest ref to the older commit
    assert gr.spine_branch_for(landed) == "feature/later"
    assert gr.spine_branch_for(landed, ("*/main",)) == "main"
    assert gr.spine_branch_for(landed, ("*/release/*",)) is None
