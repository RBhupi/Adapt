import os
import shutil
from argparse import Namespace
from pathlib import Path

from adapt.cli import _config_cmd


def test_adapt_config_handles_deleted_cwd(tmp_path, monkeypatch):
    # Create and chdir into a temp directory, then delete it to simulate stale cwd.
    cwd = tmp_path / "gone"
    cwd.mkdir()
    os.chdir(cwd)
    shutil.rmtree(cwd)

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    # No output arg: must fail loudly (cannot resolve ./config.yaml).
    args = Namespace(output=None)
    try:
        _config_cmd(args)
    except FileNotFoundError as e:
        assert "Current working directory no longer exists" in str(e)
    else:
        raise AssertionError(
            "Expected FileNotFoundError when cwd is missing and no output is provided"
        )

    # Absolute output path should still work even when cwd is missing.
    os.chdir(home)
    out = Path(home) / "config.yaml"
    args2 = Namespace(output=str(out))
    _config_cmd(args2)
    assert out.exists()
    text = out.read_text()
    assert f'base_dir: "{str(home)}"' in text


def test_adapt_config_sets_base_dir_to_output_parent(tmp_path):
    out_dir = tmp_path / "nested"
    out_path = out_dir / "my_config.yaml"
    args = Namespace(output=str(out_path))
    _config_cmd(args)

    assert out_path.exists()
    text = out_path.read_text()
    assert f'base_dir: "{str(out_dir)}"' in text
