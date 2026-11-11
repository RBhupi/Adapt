# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Shared fixtures for the ``adapt`` command tests, which go through ``cli.main()``."""

import pytest

from adapt import cli


@pytest.fixture
def adapt(monkeypatch):
    """Run ``adapt ARGS...`` through ``cli.main()``; return its exit code."""

    def _adapt(*args: str) -> int | str:
        monkeypatch.setattr("sys.argv", ["adapt", *args])
        try:
            cli.main()
        except SystemExit as exc:
            return 0 if exc.code is None else exc.code
        return 0

    return _adapt


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A private home directory: the PID file ``~/.adapt/pipeline.pid`` lands here."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))  # Path.home() on Windows
    return path
