"""Tests for merge-message conventions (pure, no git)."""

from __future__ import annotations

import pytest

from giraph.models import GiraphError
from giraph.providers.conventions import (
    BitbucketConvention,
    GitLabConvention,
    PlainConvention,
    get_convention,
)


def test_bitbucket_convention_parses_each_supported_format():
    parse = BitbucketConvention().parse
    current = "Pull request #42: t\n\nMerge in PROJ/repo from a-branch to main\n"
    legacy = "Merge pull request #7 in PROJ/repo from feature/x to refs/heads/release/1.0"
    assert parse(current) == (42, "main")
    assert parse(legacy) == (7, "release/1.0")
    assert parse("Automatic merge from release/1.0 -> main\n") == (None, "main")
    assert parse("Merge branch 'release/1.0' into main") == (None, "main")  # plain fallback
    assert parse("Merge pull request #123 from octocat/feature") is None  # github: no target ref


def test_gitlab_convention_parses_merge_request():
    parse = GitLabConvention().parse
    full = "Merge branch 'feature' into 'main'\n\ntitle\n\nSee merge request grp/proj!42"
    assert parse("Merge branch 'topic' into 'main'") == (None, "main")
    assert parse(full) == (42, "main")
    assert parse("Merge branch 'x' into main") == (None, "main")  # plain fallback


def test_plain_convention_matches_only_git_default_merge():
    parse = PlainConvention().parse
    assert parse("Merge branch 'x' into main") == (None, "main")
    assert parse("Merge remote-tracking branch 'origin/x' into main") == (None, "main")
    assert parse("Pull request #1: t\n\nMerge in P/R from b to main") is None


def test_bare_merge_message_parses_with_an_unknown_target():
    # git omits "into <ref>" when merging onto the default branch
    assert PlainConvention().parse("Merge branch 'feat'") == (None, None)
    assert PlainConvention().parse("Merge branch 'feat'\n\nconflict notes\n") == (None, None)
    assert BitbucketConvention().parse("Merge remote-tracking branch 'origin/feat'") == (None, None)
    assert GitLabConvention().parse("Merge branch 'feat' of git@host:p/r") == (None, None)
    assert PlainConvention().parse("Merge branch 'feat' into main") == (None, "main")  # named wins
    assert PlainConvention().parse("Merge pull request #1 from octocat/feature") is None


def test_unknown_convention_raises():
    with pytest.raises(GiraphError):
        get_convention("mercurial")


def test_convention_normalizes_captured_target_ref():
    assert PlainConvention().parse("Merge branch 'x' into main.") == (None, "main")
    assert PlainConvention().parse("Merge branch 'x' into 'main'") == (None, "main")
    assert PlainConvention().parse("Merge branch 'x' into 'main'.") == (None, "main")
    assert BitbucketConvention().parse("Pull request #9: t\n\nMerge in P/R from feat to main,") == (
        9,
        "main",
    )
    auto = BitbucketConvention().parse("Automatic merge from release/1.0 -> refs/heads/main;")
    assert auto == (None, "main")
    # internal dots/slashes in a legitimate ref are preserved
    assert BitbucketConvention().parse("Pull request #2: t\n\nMerge in P/R from feat to release/1.0") == (
        2,
        "release/1.0",
    )
