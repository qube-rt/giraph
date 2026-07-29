"""Regenerate the README example graphs from real, reproducible git repositories.

Every commit is stamped with a fixed identity and clock, so the SHAs in the committed
SVGs are the SHAs this script produces. Needs graphviz ``dot`` on PATH.

    uv run python scripts/generate_examples.py
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from giraph import (
    Classifier,
    Config,
    GitRepo,
    MessageProvider,
    get_convention,
    name_rev_patterns,
    render_report,
)

log = logging.getLogger("giraph.examples")

REPO_ROOT = Path(__file__).resolve().parent.parent
EPOCH = 1700000000
TICK = 3600
FIXED_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "giraph",
    "GIT_AUTHOR_EMAIL": "giraph@example.invalid",
    "GIT_COMMITTER_NAME": "giraph",
    "GIT_COMMITTER_EMAIL": "giraph@example.invalid",
}


class Builder:
    """A throwaway git repository built one deterministic commit at a time."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.clock = EPOCH
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        self.clock += TICK
        stamp = f"{self.clock} +0000"
        env = {**os.environ, **FIXED_ENV, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        if result.returncode not in ok_codes:
            raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def commit(self, rel: str, content: str, message: str) -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()

    def checkout(self, branch: str, create: bool = False) -> None:
        self.git("checkout", "-q", *(["-b"] if create else []), branch)

    def merge(self, branch: str, message: str) -> str:
        self.git("merge", "-q", "--no-ff", branch, "-m", message)
        return self.head()

    def merge_pr(self, branch: str, pr_id: int, target_ref: str, title: str) -> str:
        message = f"Pull request #{pr_id}: {title}\n\nMerge in PROJ/repo from {branch} to {target_ref}"
        return self.merge(branch, message)

    def evil_merge(self, branch: str, rel: str, message: str) -> str:
        """A merge whose own resolution introduces content present in neither parent."""
        self.git("merge", "-q", "--no-ff", "--no-commit", branch, ok_codes=(0, 1))
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("resolved by hand\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()


def build_simple(b: Builder) -> tuple[str, str]:
    """A short main-line window: one real PR, one docs-only PR, a back-merge, a no-PR merge."""
    base = b.commit("README.md", "example\n", "root")

    b.checkout("feature/auth", create=True)
    b.commit("src/auth.py", "def login():\n    ...\n", "add login")
    b.checkout("main")
    b.merge_pr("feature/auth", 101, "main", "add login")

    b.checkout("feature/docs", create=True)
    b.commit("docs/guide.md", "how to\n", "document login")
    b.checkout("main")
    b.merge_pr("feature/docs", 102, "main", "document login")

    b.checkout("release/1.0", create=True)
    b.checkout("feature/hotfix", create=True)
    b.commit("src/auth.py", "def login():\n    return True\n", "fix login")
    b.checkout("release/1.0")
    b.merge_pr("feature/hotfix", 103, "release/1.0", "fix login")

    b.checkout("main")
    b.merge_pr("release/1.0", 104, "main", "back-merge release/1.0")

    b.checkout("spike/perf", create=True)
    b.commit("src/bench.py", "bench\n", "try something")
    b.checkout("main")
    b.merge("spike/perf", "merge the perf spike")

    return base, b.head()


def build_complex(b: Builder) -> tuple[str, str]:
    """A release-fed main line: back-merges, an evil merge, and feature-internal no-PR islands."""
    base = b.commit("README.md", "example\n", "root")
    b.commit("src/core.py", "core = 1\n", "seed core")

    b.checkout("release/1.0", create=True)
    b.checkout("main")

    # a feature that first absorbs another branch (no PR), then keeps working -> orphan island
    b.checkout("feature/api", create=True)
    b.commit("src/api.py", "api\n", "start api")
    b.checkout("feature/api-helpers", create=True)
    b.commit("src/helpers.py", "helpers\n", "add helpers")
    b.checkout("feature/api")
    b.merge("feature/api-helpers", "Merge branch 'feature/api-helpers' into feature/api")
    b.commit("src/api.py", "api = 2\n", "finish api")
    b.checkout("main")
    b.merge_pr("feature/api", 201, "main", "new api")

    b.checkout("feature/schema", create=True)
    b.commit("src/schema.py", "schema\n", "add schema")
    b.checkout("feature/schema-fixups", create=True)
    b.commit("src/schema.py", "schema = 2\n", "tidy schema")
    b.checkout("feature/schema")
    b.merge("feature/schema-fixups", "Merge branch 'feature/schema-fixups' into feature/schema")
    b.commit("docs/schema.md", "schema docs\n", "document schema")
    b.checkout("main")
    b.merge_pr("feature/schema", 202, "main", "schema support")

    b.checkout("feature/notes", create=True)
    b.commit("docs/notes.md", "notes\n", "release notes")
    b.checkout("main")
    b.merge_pr("feature/notes", 203, "main", "release notes")

    # release-line work, back-merged into main
    b.checkout("release/1.0")
    b.checkout("hotfix/crash", create=True)
    b.commit("src/core.py", "core = 2\n", "fix the crash")
    b.checkout("release/1.0")
    b.merge_pr("hotfix/crash", 204, "release/1.0", "fix the crash")
    b.checkout("main")
    b.merge_pr("release/1.0", 205, "main", "back-merge release/1.0")

    b.checkout("release/1.0")
    b.checkout("hotfix/limits", create=True)
    b.commit("src/limits.py", "limit = 10\n", "raise the limit")
    b.checkout("release/1.0")
    b.merge_pr("hotfix/limits", 206, "release/1.0", "raise the limit")

    b.checkout("main")
    b.commit("src/limits.py", "limit = 5\n", "lower the limit on main")
    # the back-merge conflicts and the resolution invents a third value: edited transport
    b.evil_merge(
        "release/1.0",
        "src/limits.py",
        "Pull request #207: back-merge release/1.0\n\nMerge in PROJ/repo from release/1.0 to main",
    )

    b.checkout("feature/cli", create=True)
    b.commit("src/cli.py", "cli\n", "add cli")
    b.checkout("main")
    b.merge_pr("feature/cli", 208, "main", "add a cli")

    return base, b.head()


def render(root: Path, window: tuple[str, str], config: Config, out: Path) -> None:
    repo = GitRepo(root)
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
    ).run(*window)
    out.write_text(render_report(report, repo=repo, config=config, fmt="svg"), encoding="utf-8")
    log.info(f"{out.name}: {report.summary_line()}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if shutil.which("dot") is None:
        raise SystemExit("graphviz 'dot' not found on PATH")
    config = Config(
        eligible_paths=("src/",),
        accept_refs=("main",),
        accept_prefixes=("release/",),
        convention="bitbucket",
        drop_orphan_islands=False,
        merged_in_arrow="in",
    )
    for name, build, cfg in (
        ("example.svg", build_simple, config),
        # the complex example hides the feature-internal no-PR churn
        ("example-complex.svg", build_complex, replace(config, drop_orphan_islands=True)),
    ):
        workdir = Path(tempfile.mkdtemp(prefix="giraph-example-"))
        try:
            builder = Builder(workdir)
            render(workdir, build(builder), cfg, REPO_ROOT / name)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
