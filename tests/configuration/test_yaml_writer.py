# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for the commented-YAML writer used by config generation.

The writer serializes a plain config dict to YAML, annotating scalar leaves with
their field descriptions as inline comments. Correctness is pinned by round-trip:
parsing the emitted YAML must reproduce the original data exactly.
"""

import pytest

pytestmark = pytest.mark.unit

import yaml  # noqa: E402

from adapt.configuration.schemas.errors import ConfigError  # noqa: E402
from adapt.configuration.schemas.yaml_writer import dump, insert  # noqa: E402


class TestRoundTrip:
    def test_roundtrip_reproduces_data(self):
        data = {
            "mode": "realtime",
            "threshold": 30.0,
            "max_cellsize_gridpoint": None,
            "grid_shape": (41, 301, 301),
            "grid_limits": ((0.0, 20000.0), (-150000.0, 150000.0)),
            "radar_variables": ["reflectivity", "velocity"],
            "global": {"z_level": 2000.0, "var_names": {"reflectivity": "reflectivity"}},
            "module_params": {"cvs": {"gain": 2.5}},
        }
        loaded = yaml.safe_load(dump(data))
        expected = {
            **data,
            "grid_shape": [41, 301, 301],
            "grid_limits": [[0.0, 20000.0], [-150000.0, 150000.0]],
        }
        assert loaded == expected

    def test_roundtrip_preserves_backslashes(self):
        r"""A Windows path must survive the round trip verbatim.

        A written path such as a store root or source_dir looks like
        C:\Users\...\run on Windows. Emitting that as a double-quoted YAML scalar
        makes the backslash an escape character: the value is silently corrupted,
        and a sequence like \U is a hard parse error — so the written file could
        not be read back.
        """
        data = {
            "base_dir": r"C:\Users\runneradmin\AppData\Local\Temp\nested",
            "note": r"tab\there and a quote ' and a \"double\" quote",
        }
        assert yaml.safe_load(dump(data)) == data


class TestComments:
    def test_scalar_description_becomes_inline_comment(self):
        out = dump({"threshold": 30.0}, {"threshold": "dBZ threshold"})
        assert "threshold: 30.0  # dBZ threshold" in out

    def test_nested_descriptions_apply(self):
        out = dump(
            {"global": {"z_level": 2000.0}},
            {"global": {"z_level": "altitude (m)"}},
        )
        assert "z_level: 2000.0  # altitude (m)" in out

    def test_header_is_prepended(self):
        out = dump({"mode": "realtime"}, header="# Adapt config")
        assert out.startswith("# Adapt config\n")

    def test_no_comment_without_description(self):
        out = dump({"mode": "realtime"})
        assert "mode: realtime" in out
        assert "#" not in out.split("mode: realtime")[1].split("\n")[0]


class TestNonScalarComments:
    """Options whose value is a list or a mapping carry their description too."""

    def test_flat_list_description_is_an_inline_comment(self):
        out = dump({"fields": ["a", "b"]}, {"fields": "variables kept"})
        assert "fields: [a, b]  # variables kept" in out.splitlines()

    def test_nested_list_description_comments_its_key(self):
        data = {"grid_limits": ((0.0, 1.0), (2.0, 3.0))}
        out = dump(data, {"grid_limits": "grid extent (m)"})
        assert "grid_limits:  # grid extent (m)" in out.splitlines()
        assert yaml.safe_load(out) == {"grid_limits": [[0.0, 1.0], [2.0, 3.0]]}

    def test_empty_mapping_description_is_an_inline_comment(self):
        out = dump({"field_map": {}}, {"field_map": "variable renames"})
        assert "field_map: {}  # variable renames" in out.splitlines()

    def test_mapping_leaf_description_comments_its_key(self):
        data = {"extra": {"n_adjacent_cells": -10.0}}
        out = dump(data, {"extra": "extra column weights"})
        assert "extra:  # extra column weights" in out.splitlines()
        assert yaml.safe_load(out) == data

    def test_nested_section_header_is_a_comment_above_it(self):
        data = {"detection": {"threshold_params": {"threshold": 30}}}
        desc = {
            "detection": {
                "threshold_params": {"_header": "threshold method", "threshold": "dBZ"},
            }
        }
        lines = dump(data, desc).splitlines()
        heading = lines.index("  # threshold method")
        assert lines[heading + 1] == "  threshold_params:"
        assert lines[heading + 2] == "    threshold: 30  # dBZ"


def test_dict_keys_with_yaml_significant_chars_round_trip():
    # Unquoted keys break YAML when a source field name carries ': ' or '#'
    # — the same defect class as the double-quoted-Windows-path incident.
    data = {"m": {"weird: key": "v", "has#hash": "w"}}
    out = dump(data)
    assert yaml.safe_load(out) == data


def test_empty_dict_round_trips_as_empty_dict():
    # A bare 'key:' reloads as None and fails pydantic validation downstream.
    data = {"field_map": {}}
    out = dump(data)
    assert yaml.safe_load(out) == {"field_map": {}}


def test_a_list_of_flat_mappings_round_trips_one_row_each():
    data = {"gates": [{"field": "n_scans", "op": "ge", "value": 2}, {"field": "x", "value": 0.5}]}

    out = dump(data)

    assert out == "gates:\n  - {field: n_scans, op: ge, value: 2}\n  - {field: x, value: 0.5}\n"
    assert yaml.safe_load(out) == data


def test_a_list_of_nested_mappings_is_rejected_loudly():
    # Emitting only the dict keys would be silent corruption.
    with pytest.raises(ValueError, match="list of dicts"):
        dump({"rows": [{"a": {"b": 1}}]})


_DOC = """\
# head
params:
  tracking:  # mine
    max_speed_ms: 35  # changed
    # a note
  detection:
    nested:
      a: 1
# tail
"""


class TestInsert:
    def test_entries_go_at_the_end_of_the_mapping_indented_to_its_keys(self):
        out = insert(_DOC, ("params", "tracking"), "latent_scans: 2  # N\n")

        assert out == _DOC.replace(
            "    max_speed_ms: 35  # changed\n",
            "    max_speed_ms: 35  # changed\n    latent_scans: 2  # N\n",
        )

    def test_a_nested_mapping_takes_entries_at_its_own_depth(self):
        out = insert(_DOC, ("params", "detection", "nested"), "b: 2\n")

        assert "      a: 1\n      b: 2\n# tail\n" in out
        assert yaml.safe_load(out)["params"]["detection"]["nested"] == {"a": 1, "b": 2}

    def test_a_gap_puts_empty_lines_before_the_entries(self):
        out = insert(_DOC, ("params",), "# projection\nprojection:\n  levels: 3\n", gap=2)

        assert "      a: 1\n\n\n  # projection\n  projection:\n    levels: 3\n# tail" in out

    def test_a_key_without_a_value_takes_entries_two_spaces_past_it(self):
        out = insert("run: x\nparams:\n# end\n", ("params",), "tracking:\n  a: 1\n")

        assert out == "run: x\nparams:\n  tracking:\n    a: 1\n# end\n"

    def test_a_document_without_a_final_newline_is_extended(self):
        assert insert("params:\n  a: 1", ("params",), "b: 2\n") == "params:\n  a: 1\n  b: 2\n"

    @pytest.mark.parametrize("value", ["{a: 1}", "{}", "null", "[1]", "3"])
    def test_what_is_not_a_block_mapping_is_refused_naming_it(self, value):
        with pytest.raises(
            ConfigError, match=r"params\.tracking is not written one entry per line"
        ):
            insert(f"params:\n  tracking: {value}\n", ("params", "tracking"), "b: 2\n")
