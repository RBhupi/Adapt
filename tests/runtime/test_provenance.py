# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Run provenance capture + config hashing for reproducibility."""

import subprocess
from pathlib import Path

import pytest

from adapt.runtime import provenance
from adapt.runtime.provenance import capture_provenance, config_hash


def test_capture_provenance_populates_environment_fields():
    p = capture_provenance()
    assert p.hostname
    assert p.username
    assert "." in p.python_version
    assert p.platform
    assert p.software_version
    # git_commit is a real hex string, marked when the code has changed since,
    # OR None (faithful — never fabricated)
    assert p.git_commit is None or (
        len(p.git_commit) >= 7
        and all(c in "0123456789abcdef" for c in p.git_commit.removesuffix("-dirty"))
    )


def test_config_hash_is_stable_and_sensitive_to_change():
    assert config_hash('{"a": 1}') == config_hash('{"a": 1}')
    assert config_hash('{"a": 1}') != config_hash('{"a": 2}')
    assert len(config_hash("anything")) == 64  # sha256 hex digest


def _commit_of_this_checkout() -> str | None:
    """The commit of the checkout these tests run from; None outside one."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def test_the_commit_is_that_of_adapts_code_wherever_the_run_is_started(tmp_path, monkeypatch):
    """A run is started in its store's folder, which is no checkout of Adapt."""
    monkeypatch.chdir(tmp_path)

    commit = capture_provenance().git_commit

    assert (commit and commit.removesuffix("-dirty")) == _commit_of_this_checkout()


def _git(folder: Path, *arguments: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.test", *arguments],
        cwd=folder,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A checkout holding Adapt's code as one committed file, which
    ``capture_provenance`` takes for its own; returns (the package folder, the commit)."""
    package = tmp_path / "checkout" / "adapt"
    source = package / "runtime" / "provenance.py"
    source.parent.mkdir(parents=True)
    source.write_text("# the code\n")
    _git(package.parent, "init", "--quiet")
    _git(package.parent, "add", ".")
    _git(package.parent, "commit", "--quiet", "-m", "the code")
    monkeypatch.setattr(provenance, "_SOURCE", source)
    return package, _git(package.parent, "rev-parse", "HEAD")


def test_code_as_committed_has_the_commit_alone(checkout):
    _, commit = checkout

    assert capture_provenance().git_commit == commit


def test_code_changed_since_the_commit_is_marked(checkout):
    package, commit = checkout
    (package / "runtime" / "provenance.py").write_text("# the code, changed\n")

    assert capture_provenance().git_commit == f"{commit}-dirty"


def test_code_added_since_the_commit_is_marked(checkout):
    package, commit = checkout
    (package / "new_module.py").write_text("# not committed\n")

    assert capture_provenance().git_commit == f"{commit}-dirty"


def test_code_the_checkout_around_it_does_not_track_has_no_commit(checkout, monkeypatch):
    """Adapt installed as a release into a folder inside some other checkout."""
    package, _ = checkout
    installed = package.parent / "site-packages" / "adapt" / "runtime" / "provenance.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("# installed, not tracked\n")
    monkeypatch.setattr(provenance, "_SOURCE", installed)

    assert capture_provenance().git_commit is None


def test_code_in_no_checkout_has_no_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "_SOURCE", tmp_path / "adapt" / "runtime" / "provenance.py")
    (tmp_path / "adapt" / "runtime").mkdir(parents=True)

    assert capture_provenance().git_commit is None
