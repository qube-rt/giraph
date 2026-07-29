"""giraph — classify git merges as real changes vs transport noise, and render the decisions."""

from __future__ import annotations

from giraph.classify import Classifier
from giraph.config import (
    CONFIG_FILENAME,
    Config,
    find_config,
    load_config,
    name_rev_patterns,
    resolve_config,
)
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
    Outcome,
    PullRequest,
    Report,
    short_sha,
)
from giraph.providers import PullRequestProvider
from giraph.providers.conventions import CONVENTIONS, MergeConvention, get_convention
from giraph.providers.message import MessageProvider
from giraph.render import decisions_table, render_report, to_dot, to_svg

__all__ = [
    "CONFIG_FILENAME",
    "CONVENTIONS",
    "ERROR",
    "INELIGIBLE",
    "MIXED",
    "REAL",
    "TRACKED_CLASSES",
    "TRANSPORT",
    "UNMATCHED",
    "Classification",
    "Classifier",
    "Config",
    "GitRepo",
    "GiraphError",
    "MergeConvention",
    "MessageProvider",
    "Outcome",
    "PullRequest",
    "PullRequestProvider",
    "Report",
    "decisions_table",
    "find_config",
    "get_convention",
    "load_config",
    "name_rev_patterns",
    "render_report",
    "resolve_config",
    "short_sha",
    "to_dot",
    "to_svg",
]
