"""Tests for config discovery, loading and CLI-override precedence."""

from __future__ import annotations

import pytest

from giraph.config import find_config, load_config, resolve_config
from giraph.models import GiraphError


def test_load_and_resolve_from_file(tmp_path):
    (tmp_path / "giraph.toml").write_text(
        '[giraph]\neligible_paths = ["src/"]\naccept_target_refs = ["trunk"]\nconvention = "gitlab"\n'
    )

    config = resolve_config(load_config(find_config(tmp_path, None)))

    assert config.eligible_paths == ("src/",)
    assert config.accept_refs == ("trunk",)
    assert config.convention == "gitlab"


def test_defaults():
    config = resolve_config({})
    assert config.convention == "plain"
    assert config.accept_refs == ("main",)
    assert config.drop_orphan_islands is False


def test_drop_orphan_islands_from_file_and_override(tmp_path):
    (tmp_path / "giraph.toml").write_text("[giraph]\ndrop_orphan_islands = true\n")
    section = load_config(find_config(tmp_path, None))

    assert resolve_config(section).drop_orphan_islands is True
    assert resolve_config(section, drop_orphan_islands=False).drop_orphan_islands is False


def test_merged_in_arrow_from_file_override_and_invalid(tmp_path):
    (tmp_path / "giraph.toml").write_text('[giraph]\nmerged_in_arrow = "out"\n')
    section = load_config(find_config(tmp_path, None))

    assert resolve_config(section).merged_in_arrow == "out"
    assert resolve_config(section, merged_in_arrow="in").merged_in_arrow == "in"
    assert resolve_config({}).merged_in_arrow == "in"
    with pytest.raises(GiraphError):
        resolve_config({"merged_in_arrow": "sideways"})


def test_cli_overrides_file(tmp_path):
    (tmp_path / "giraph.toml").write_text('[giraph]\neligible_paths = ["src/"]\nconvention = "plain"\n')

    section = load_config(find_config(tmp_path, None))
    config = resolve_config(section, convention="gitlab", eligible_paths=["lib/"])

    assert config.convention == "gitlab"
    assert config.eligible_paths == ("lib/",)


def test_missing_explicit_config_raises(tmp_path):
    with pytest.raises(GiraphError):
        find_config(tmp_path, tmp_path / "nope.toml")


def test_scalar_list_key_rejected():
    with pytest.raises(GiraphError):
        resolve_config({"accept_target_refs": "main"})


def test_unknown_config_key_rejected(tmp_path):
    (tmp_path / "giraph.toml").write_text('[giraph]\nelligible_paths = ["src/"]\n')

    with pytest.raises(GiraphError, match="elligible_paths"):
        load_config(find_config(tmp_path, None))
