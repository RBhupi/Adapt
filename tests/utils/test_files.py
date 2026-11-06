# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Atomic text writes and fail-loud YAML section reads."""

import os
import stat
import sys
import time

import pytest

from adapt.utils.files import atomic_write_text, read_yaml_section

pytestmark = pytest.mark.unit


def test_atomic_write_text_writes_exact_text(tmp_path):
    target = tmp_path / "current.json"

    atomic_write_text(target, '{\n  "a": 1\n}\n')

    assert target.read_bytes() == b'{\n  "a": 1\n}\n'


def test_atomic_write_text_replaces_existing_content(tmp_path):
    target = tmp_path / "current.json"
    target.write_text("a much longer previous payload\n", encoding="utf-8")

    atomic_write_text(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"


def test_successful_write_leaves_no_temp_sibling(tmp_path):
    target = tmp_path / "current.json"

    atomic_write_text(target, "new\n")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["current.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_written_file_is_world_readable(tmp_path):
    target = tmp_path / "current.json"

    atomic_write_text(target, "new\n")

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_failed_replace_keeps_original_and_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_text("old\n", encoding="utf-8")

    def refuse(src, dst):
        raise OSError("disk detached")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError, match="disk detached"):
        atomic_write_text(target, "new\n")

    assert target.read_text(encoding="utf-8") == "old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current.json"]


def test_replace_refused_while_a_reader_holds_the_file_is_retried(tmp_path, monkeypatch):
    # Windows refuses to replace a file another handle holds open (a radar-side
    # reader, an antivirus scan) with PermissionError until that handle closes.
    target = tmp_path / "current.json"
    target.write_text("old\n", encoding="utf-8")
    real_replace = os.replace
    refusals = iter([PermissionError(13, "in use"), PermissionError(13, "in use")])

    def held_open_twice(src, dst):
        refusal = next(refusals, None)
        if refusal is not None:
            raise refusal
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", held_open_twice)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    atomic_write_text(target, "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current.json"]


def test_replace_refused_for_good_raises_after_a_bounded_wait(tmp_path, monkeypatch):
    target = tmp_path / "current.json"
    target.write_text("old\n", encoding="utf-8")
    waits = []

    def always_refused(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(os, "replace", always_refused)
    monkeypatch.setattr(time, "sleep", waits.append)

    with pytest.raises(PermissionError, match="in use"):
        atomic_write_text(target, "new\n")

    assert sum(waits) <= 2.0  # seconds
    assert target.read_text(encoding="utf-8") == "old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["current.json"]


def test_missing_parent_directory_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        atomic_write_text(tmp_path / "absent" / "current.json", "new\n")


def test_read_yaml_section_returns_the_named_section(tmp_path):
    path = tmp_path / "targeting.yaml"
    path.write_text("radar_command:\n  output:\n    directory: /data/cmd\n", encoding="utf-8")

    assert read_yaml_section(path, "radar_command") == {"output": {"directory": "/data/cmd"}}


def test_read_yaml_section_ignores_sibling_sections(tmp_path):
    path = tmp_path / "targeting.yaml"
    path.write_text(
        "target_selection:\n  weights: [1, 2]\nradar_command:\n  site: {id: SGP}\nunrelated: 7\n",
        encoding="utf-8",
    )

    assert read_yaml_section(path, "target_selection") == {"weights": [1, 2]}


def test_read_yaml_section_rejects_empty_file(tmp_path):
    path = tmp_path / "targeting.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match=r"targeting\.yaml.*not a mapping"):
        read_yaml_section(path, "radar_command")


def test_read_yaml_section_missing_key_lists_present_keys(tmp_path):
    path = tmp_path / "targeting.yaml"
    path.write_text("target_selection: {}\nsite: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"'radar_command'.*\['site', 'target_selection'\]"):
        read_yaml_section(path, "radar_command")


def test_read_yaml_section_rejects_non_mapping_section(tmp_path):
    path = tmp_path / "targeting.yaml"
    path.write_text("radar_command:\n  - SGP\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"'radar_command'.*not a mapping"):
        read_yaml_section(path, "radar_command")
