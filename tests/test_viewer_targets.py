"""Targets: the config files the console may read, and the ones it may not.

Every test here is about a name that arrives over HTTP. The functions under
test build paths out of those names, so "returns None" is the whole contract:
a caller must never get a Path pointing outside ``configs/``, and must never
get an exception it would have to turn into a 500.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clousight_bench.viewer.targets import (
    list_targets,
    load_target,
    redact,
    target_path,
    valid_target_name,
)


@pytest.fixture
def configs(tmp_path: Path) -> Path:
    d = tmp_path / "configs"
    d.mkdir()
    (d / "duckdb-sf1.yaml").write_text(
        "target:\n  mode: mock\n  provider: duckdb\nparams:\n  scale_factor: 1\n"
    )
    (d / "aliyun-live.yaml").write_text(
        "target:\n  provider: aliyun\n  region: cn-hangzhou\n  access_key_secret: hunter2\n"
    )
    (d / "notes.txt").write_text("not a target")
    return d


class TestNames:
    """A name off the wire is a name, never a path."""

    @pytest.mark.parametrize(
        "name",
        [
            "../etc/passwd",
            "a/b",
            "a\\b",
            ".",
            "..",
            "",
            "x" * 65,
            "with space",
            "nul\x00byte",
            "sub/../escape",
        ],
    )
    def test_rejected(self, configs: Path, name: str) -> None:
        assert valid_target_name(name) is False
        assert target_path(configs, name) is None
        assert load_target(configs, name) is None

    @pytest.mark.parametrize("name", ["duckdb-sf1", "a", "A.B_c-1", "x" * 64])
    def test_accepted(self, configs: Path, name: str) -> None:
        assert valid_target_name(name) is True
        resolved = target_path(configs, name)
        assert resolved is not None
        assert resolved.name == f"{name}.yaml"

    def test_a_symlink_out_of_the_tree_is_not_a_target(self, configs: Path, tmp_path: Path) -> None:
        """Name validation alone cannot see this one — only the realpath can."""
        outside = tmp_path / "secrets.yaml"
        outside.write_text("target: {token: hunter2}\n")
        os.symlink(outside, configs / "sneaky.yaml")
        assert valid_target_name("sneaky") is True
        assert target_path(configs, "sneaky") is None
        assert load_target(configs, "sneaky") is None

    def test_a_missing_file_is_a_path_but_not_a_target(self, configs: Path) -> None:
        # target_path answers "where would it live", which new-target writes
        # need; load_target answers "what is there", which is nothing.
        assert target_path(configs, "nope") is not None
        assert load_target(configs, "nope") is None


class TestListing:
    def test_lists_yaml_files_only(self, configs: Path) -> None:
        names = [t["name"] for t in list_targets(configs)]
        assert names == ["aliyun-live", "duckdb-sf1"]

    def test_carries_what_the_file_says_about_itself(self, configs: Path) -> None:
        by_name = {t["name"]: t for t in list_targets(configs)}
        assert by_name["duckdb-sf1"]["mode"] == "mock"
        assert by_name["duckdb-sf1"]["provider"] == "duckdb"
        assert by_name["duckdb-sf1"]["size"] > 0
        assert by_name["aliyun-live"]["mode"] == ""

    def test_a_directory_that_does_not_exist_lists_nothing(self, tmp_path: Path) -> None:
        assert list_targets(tmp_path / "absent") == []

    def test_an_unparsable_file_is_listed_with_its_error(self, configs: Path) -> None:
        """A broken config must stay visible: hiding it looks like deletion."""
        (configs / "broken.yaml").write_text("target: [unclosed\n")
        entry = next(t for t in list_targets(configs) if t["name"] == "broken")
        assert entry["error"] != ""
        assert entry["mode"] == ""


class TestRedaction:
    def test_credential_shaped_keys_never_leave_the_process(self) -> None:
        clean, hidden = redact(
            {"target": {"region": "cn-hangzhou", "access_key_secret": "hunter2", "auth": {"api_token": "t"}}}
        )
        assert clean["target"]["region"] == "cn-hangzhou"
        assert clean["target"]["access_key_secret"] == "***"
        assert clean["target"]["auth"]["api_token"] == "***"
        assert hidden == ["target.access_key_secret", "target.auth.api_token"]

    def test_inside_a_list_too(self) -> None:
        clean, hidden = redact({"creds": [{"password": "p"}]})
        assert clean["creds"][0]["password"] == "***"
        assert hidden == ["creds.0.password"]

    def test_nothing_to_hide_reports_nothing(self) -> None:
        clean, hidden = redact({"target": {"mode": "mock"}})
        assert hidden == []
        assert clean == {"target": {"mode": "mock"}}


class TestLoading:
    def test_a_clean_file_comes_back_with_its_text_intact(self, configs: Path) -> None:
        # Comments and key order are the file. A target with nothing to hide is
        # served verbatim so editing it in the console cannot silently reformat
        # it or drop the comments that explain it.
        loaded = load_target(configs, "duckdb-sf1")
        assert loaded is not None
        assert loaded["redacted"] == []
        assert loaded["yaml"] == (configs / "duckdb-sf1.yaml").read_text()
        assert loaded["data"]["params"] == {"scale_factor": 1}

    def test_a_file_with_a_secret_does_not_come_back_as_text(self, configs: Path) -> None:
        """The raw text is the one thing that cannot be redacted safely."""
        loaded = load_target(configs, "aliyun-live")
        assert loaded is not None
        assert loaded["yaml"] is None
        assert loaded["redacted"] == ["target.access_key_secret"]
        assert loaded["data"]["target"]["access_key_secret"] == "***"
        assert "hunter2" not in repr(loaded)

    def test_an_unparsable_file_loads_as_an_error_not_an_exception(self, configs: Path) -> None:
        (configs / "broken.yaml").write_text("target: [unclosed\n")
        loaded = load_target(configs, "broken")
        assert loaded is not None
        assert loaded["error"] != ""
        assert loaded["data"] == {}

    def test_a_top_level_list_is_not_a_target(self, configs: Path) -> None:
        (configs / "list.yaml").write_text("- one\n- two\n")
        loaded = load_target(configs, "list")
        assert loaded is not None
        assert loaded["error"] != ""
