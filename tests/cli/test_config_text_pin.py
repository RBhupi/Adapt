# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The file ``adapt config --nexrad [--params]`` writes, pinned by its digest.

A new user's first pipeline file must not change by accident. When it changes
on purpose (a module added, a module's summary or an option's default or
description changed), replace the digest here in the same change.
"""

import hashlib

import pytest

pytestmark = pytest.mark.unit

_PINNED = {
    (): "80ddd73e921082ce86695127b920c28ace33eaf7fd1c2e8beebdb80b97aacd7f",
    ("--params",): "da86d0ea478b35770e4caf19325e04e3a60f27a9029f87ea62eb296e9c3b21d5",
}


@pytest.mark.parametrize("flags", list(_PINNED), ids=["short", "params"])
def test_the_written_file_is_byte_for_byte_the_pinned_text(tmp_path, monkeypatch, adapt, flags):
    monkeypatch.chdir(tmp_path)

    assert adapt("config", "--nexrad", *flags) == 0

    text = (tmp_path / "nexrad.yaml").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _PINNED[flags]
