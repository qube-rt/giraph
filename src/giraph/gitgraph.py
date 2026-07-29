"""Local git graph operations via subprocess."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from giraph.models import GiraphError

GIT_TIMEOUT = 60
NAME_REV_BATCH = 200
GIT_ENV_PINS = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def git_env() -> dict[str, str]:
    """Environment with every inherited ``GIT_*`` variable dropped.

    ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` and friends silently redirect git at a
    different repository than the one asked for, so the repo path is the only thing that selects it.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_ENV_PINS)
    return env


class GitRepo:
    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def discover(cls, path: Path) -> GitRepo:
        """Repo containing ``path``, via git's own upward search (``path`` may be a subdirectory)."""
        probe = cls(path)
        top = probe._run("rev-parse", "--show-toplevel", ok_codes=(0, 128))
        return cls(Path(top) if top else Path(probe._run("rev-parse", "--absolute-git-dir")))

    def _exec(self, args: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(self.root), "--literal-pathspecs", *args],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GiraphError(f"git {' '.join(args)} timed out after {timeout}s") from exc
        except OSError as exc:
            raise GiraphError(f"could not run git {' '.join(args)}: {exc}") from exc

    def _run(self, *args: str, timeout: int = GIT_TIMEOUT, ok_codes: tuple[int, ...] = (0,)) -> str:
        result = self._exec(args, timeout)
        if result.returncode not in ok_codes:
            raise GiraphError(
                f"git {' '.join(args)} failed with {result.returncode}: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _lines(self, *args: str) -> list[str]:
        return [line for line in self._run(*args).splitlines() if line]

    def _zlines(self, *args: str) -> list[str]:
        """Records of a ``-z`` command: NUL-separated, so paths are never C-quoted."""
        return [field for field in self._run(*args).split("\0") if field]

    def is_shallow(self) -> bool:
        return self._run("rev-parse", "--is-shallow-repository") == "true"

    def parents(self, sha: str) -> list[str]:
        lines = self._lines("rev-list", "--parents", "-n", "1", "--end-of-options", sha)
        if not lines:
            raise GiraphError(f"git returned no commit for {sha}")
        return lines[0].split()[1:]

    def commits_with_parents(self, from_sha: str, to_sha: str) -> list[tuple[str, list[str]]]:
        lines = self._lines(
            "rev-list",
            "--parents",
            "--topo-order",
            "--reverse",
            "--end-of-options",
            f"{from_sha}..{to_sha}",
        )
        return [(parts[0], parts[1:]) for parts in (line.split() for line in lines)]

    def changed_paths(self, base_sha: str, target_sha: str) -> list[str]:
        return self._zlines("diff", "-z", "--name-only", base_sha, target_sha, "--")

    def merge_introduced_paths(self, merge_sha: str) -> list[str]:
        """Paths the merge resolved to content present in NEITHER parent
        (hand-authored resolution / evil-merge edits) — not code it merely carried,
        nor a verbatim side-pick of a conflict."""
        commit_parents = self.parents(merge_sha)
        parent0, parent1 = commit_parents[0], commit_parents[1]
        # merge-tree exits 1 on conflicts but still writes the tree on line 1.
        merged = self._run("merge-tree", "--write-tree", parent0, parent1, ok_codes=(0, 1))
        merged_lines = merged.splitlines()
        if not merged_lines:
            raise GiraphError(f"git merge-tree produced no tree for {merge_sha}")
        actual = self._run("rev-parse", f"{merge_sha}^{{tree}}")
        resolved = self._zlines("diff", "-z", "--name-only", merged_lines[0], actual, "--")
        if not resolved:
            return []
        from_parent0 = set(self._zlines("diff", "-z", "--name-only", parent0, merge_sha, "--", *resolved))
        from_parent1 = set(self._zlines("diff", "-z", "--name-only", parent1, merge_sha, "--", *resolved))
        return [p for p in resolved if p in from_parent0 and p in from_parent1]

    def commit_message(self, sha: str) -> str:
        return self._run("log", "-1", "--format=%B", sha)

    def first_parent_chain(self, head_sha: str) -> list[str]:
        return self._lines("rev-list", "--first-parent", "--end-of-options", head_sha)

    def is_first_parent_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True iff ``ancestor`` lies on ``descendant``'s first-parent chain (tags are peeled)."""
        full = self._run("rev-parse", "--verify", "--end-of-options", f"{ancestor}^{{commit}}")
        return full in self.first_parent_chain(descendant)

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """True iff ``ancestor`` is a (general) ancestor of ``descendant``."""
        # merge-base signals via exit code; _run would raise on rc 1 and discard the code.
        args = ("merge-base", "--is-ancestor", "--end-of-options", ancestor, descendant)
        result = self._exec(args, GIT_TIMEOUT)
        if result.returncode not in (0, 1):
            raise GiraphError(
                f"git merge-base --is-ancestor failed with {result.returncode}: {result.stderr.strip()}"
            )
        return result.returncode == 0

    @staticmethod
    def _short_ref_name(name: str) -> str:
        name = re.split(r"[~^]", name, maxsplit=1)[0]
        if name.startswith("remotes/"):
            parts = name.split("/", 2)
            if len(parts) == 3:
                return parts[2]
        return name

    def _name_rev(self, shas: Sequence[str], options: Sequence[str]) -> list[tuple[str, str]]:
        """``(sha, raw name-rev name)`` pairs, one batched call per ``NAME_REV_BATCH`` commits."""
        named: list[tuple[str, str]] = []
        for start in range(0, len(shas), NAME_REV_BATCH):
            chunk = list(shas[start : start + NAME_REV_BATCH])
            names = self._run("name-rev", "--name-only", *options, "--end-of-options", *chunk).splitlines()
            if len(names) != len(chunk):
                raise GiraphError(f"git name-rev returned {len(names)} names for {len(chunk)} commits")
            named.extend(zip(chunk, names, strict=True))
        return named

    def branches_for(self, shas: Sequence[str], prefer: tuple[str, ...] = ()) -> dict[str, str]:
        """Best-effort branch name per sha (absent when unnamed), in batched ``name-rev`` calls.

        With ``prefer`` name-rev ref patterns, an integration branch is used when a sha lies
        on its first-parent spine (a ``~N``-only name, no ``^`` side-steps); otherwise the
        closest ref (usually the origin feature branch the commit was authored on) is used.
        """
        ordered = list(dict.fromkeys(shas))
        if not ordered:
            return {}
        names: dict[str, str] = {}
        if prefer:
            options = [f"--refs={pattern}" for pattern in prefer]
            for sha, name in self._name_rev(ordered, options):
                if name and name != "undefined" and "^" not in name:
                    names[sha] = self._short_ref_name(name)
        remaining = [sha for sha in ordered if sha not in names]
        for sha, name in self._name_rev(remaining, ["--exclude=tags/*"]):
            if name and name != "undefined":
                names[sha] = self._short_ref_name(name)
        return names

    def branch_for(self, sha: str, prefer: tuple[str, ...] = ()) -> str | None:
        """Best-effort branch name for one ``sha`` (``None`` if unnamed); see :meth:`branches_for`."""
        return self.branches_for([sha], prefer).get(sha)

    def spine_branch_for(self, sha: str, refs: tuple[str, ...] = ()) -> str | None:
        """Branch whose first-parent spine holds ``sha``, i.e. the ref it was merged onto.

        ``refs`` limits the candidates to those name-rev ref patterns — without it the closest
        ref wins, and a branch cut from the mainline afterwards is closer than the mainline.
        ``None`` when only a side branch names it (a ``^`` side-step), which would make the
        attribution a guess rather than a fact.
        """
        options = [f"--refs={pattern}" for pattern in refs]
        name = self._run("name-rev", "--name-only", *options, "--exclude=tags/*", "--end-of-options", sha)
        if not name or name == "undefined" or "^" in name:
            return None
        return self._short_ref_name(name)
