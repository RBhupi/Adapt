# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""scan_id mint: deterministic content identity for a raw source file.

The scan_id is the single identity key joining tracking tables, catalog rows,
artifacts, and dashboard lookups. It is a pure function of the raw file bytes —
never of the filename, clock, or run — so the same volume always gets the same
identity, across runs, hosts, and re-downloads.
"""

import hashlib

import pytest

from adapt.utils.identity import scan_id_from_bytes

pytestmark = pytest.mark.unit


def test_same_bytes_same_id():
    assert scan_id_from_bytes(b"volume-payload") == scan_id_from_bytes(b"volume-payload")


def test_different_bytes_different_id():
    assert scan_id_from_bytes(b"volume-a") != scan_id_from_bytes(b"volume-b")


def test_id_is_16_lowercase_hex_chars():
    sid = scan_id_from_bytes(b"volume-payload")
    assert len(sid) == 16
    assert sid == sid.lower()
    int(sid, 16)  # every character is a hex digit


def test_id_is_sha256_prefix():
    # Externally verifiable: `sha256sum <raw file> | cut -c1-16` reproduces the id.
    data = b"volume-payload"
    assert scan_id_from_bytes(data) == hashlib.sha256(data).hexdigest()[:16]


def test_non_bytes_input_raises():
    with pytest.raises(TypeError):
        scan_id_from_bytes("not-bytes")


# ── options_hash: the identity of a module's options ─────────────────────────


def test_equal_options_have_the_same_hash():
    from adapt.utils.identity import options_hash

    assert options_hash('{"tag":"a"}') == options_hash('{"tag":"a"}')
    assert options_hash('{"tag":"a"}') != options_hash('{"tag":"b"}')


def test_an_options_hash_is_the_first_sixteen_hex_chars_of_the_sha256_of_the_json():
    from adapt.utils.identity import options_hash

    text = '{"tag":"a"}'

    assert options_hash(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
