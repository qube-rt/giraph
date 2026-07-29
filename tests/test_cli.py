"""Tests for the CLI entrypoint: output routing and exit codes."""

from __future__ import annotations

import subprocess

import pytest

from giraph import cli


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


def _init(root):
    _git(root, "init", "-q")
    _git(root, "checkout", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")


def _run(monkeypatch, argv) -> int:
    monkeypatch.setattr("sys.argv", ["giraph", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    code = exc.value.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


@pytest.fixture
def octopus_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    base = _commit(root, "README", "root", "root")
    for name in ("f1", "f2", "f3"):
        _git(root, "checkout", "-q", "main")
        _git(root, "checkout", "-q", "-b", name)
        _commit(root, f"src/{name}.cpp", name, name)
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "f1", "f2", "f3", "-m", "octopus")
    head = _git(root, "rev-parse", "HEAD")
    return root, base, head


def test_error_outcome_exits_3_table_format(octopus_repo, monkeypatch, capsys):
    root, base, head = octopus_repo
    code = _run(monkeypatch, [base, head, "--repo", str(root), "--format", "table"])
    out = capsys.readouterr().out
    assert code == 3
    assert "error" in out
    assert "digraph" not in out and "<svg" not in out  # table path emits only the table


def test_error_outcome_exits_3_dot_format(octopus_repo, monkeypatch, capsys):
    root, base, head = octopus_repo
    code = _run(monkeypatch, [base, head, "--repo", str(root), "--format", "dot"])
    out = capsys.readouterr().out
    assert code == 3
    assert "digraph" in out  # graph still rendered before the non-zero exit


def test_nothing_matched_exits_4(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    base = _commit(root, "README", "root", "root")
    _git(root, "checkout", "-q", "-b", "topic")
    _commit(root, "src/a.cpp", "code", "add a")
    _git(root, "checkout", "-q", "main")
    _git(root, "merge", "-q", "--no-ff", "topic", "-m", "just merging stuff")
    head = _git(root, "rev-parse", "HEAD")

    code = _run(monkeypatch, [base, head, "--repo", str(root), "--format", "table"])

    assert code == 4  # a window where nothing could be attributed is not a clean audit
    assert "no PR" in capsys.readouterr().out


def test_empty_window_exits_4(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    base = _commit(root, "README", "root", "root")
    head = _commit(root, "src/a.cpp", "code", "no merges here")

    assert _run(monkeypatch, [base, head, "--repo", str(root), "--format", "table"]) == 4


def test_config_is_discovered_from_the_repo_root_of_a_subdirectory(tmp_path, monkeypatch, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    (root / "giraph.toml").write_text('[giraph]\nconvention = "bitbucket"\n')
    base = _commit(root, "README", "root", "root")
    _git(root, "checkout", "-q", "-b", "feature/x")
    _commit(root, "src/a.cpp", "code", "add a")
    _git(root, "checkout", "-q", "main")
    _git(
        root,
        "merge",
        "-q",
        "--no-ff",
        "feature/x",
        "-m",
        "Merge pull request #4 in P/R from feature/x to refs/heads/main",
    )
    head = _git(root, "rev-parse", "HEAD")

    # the default 'plain' convention cannot read that message: only the repo-root config makes it real
    code = _run(monkeypatch, [base, head, "--repo", str(root / "src"), "--format", "table"])

    assert code == 0
    assert "real" in capsys.readouterr().out


def test_git_dir_in_the_environment_does_not_override_repo(tmp_path, monkeypatch, capsys):
    other = tmp_path / "other"
    other.mkdir()
    _init(other)
    _commit(other, "README", "other", "other root")

    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    base = _commit(root, "README", "root", "root")
    _git(root, "checkout", "-q", "-b", "feature/x")
    _commit(root, "src/a.cpp", "code", "add a")
    _git(root, "checkout", "-q", "main")
    _git(
        root,
        "merge",
        "-q",
        "--no-ff",
        "feature/x",
        "-m",
        "Merge pull request #4 in P/R from feature/x to refs/heads/main",
    )
    head = _git(root, "rev-parse", "HEAD")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    argv = [base, head, "--repo", str(root), "--format", "table", "--convention", "bitbucket"]
    assert _run(monkeypatch, argv) == 0
    assert "#4" in capsys.readouterr().out


def test_clean_run_exits_0(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _init(root)
    base = _commit(root, "README", "root", "root")
    _git(root, "checkout", "-q", "-b", "feature/x")
    _commit(root, "src/a.cpp", "code", "add a")
    _git(root, "checkout", "-q", "main")
    _git(
        root,
        "merge",
        "-q",
        "--no-ff",
        "feature/x",
        "-m",
        "Merge pull request #4 in P/R from feature/x to refs/heads/main",
    )
    head = _git(root, "rev-parse", "HEAD")
    argv = [base, head, "--repo", str(root), "--format", "dot", "--convention", "bitbucket"]
    assert _run(monkeypatch, argv) == 0
