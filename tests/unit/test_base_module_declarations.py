# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Tests for BaseModule class-level declarations.

required_history declares how many scans of history a module needs;
options_class, options_preset and configure are how a module owns its options;
rows_keyed_by_options says whether rows made with other options stand beside its own.
"""

import pytest

pytestmark = pytest.mark.unit

from adapt.modules.base import BaseModule  # noqa: E402
from tests.helpers.shared_config import SHARED  # noqa: E402


class _MinimalModule(BaseModule):
    """Concrete subclass for testing BaseModule defaults."""

    name = "test_minimal"

    def run(self, context: dict) -> dict:
        return {}


class _MultiScanModule(BaseModule):
    """Module that declares it needs 2 historical scans."""

    name = "test_multi_scan"
    required_history = 2

    def run(self, context: dict) -> dict:
        return {}


class TestBaseModuleDefaults:
    def test_default_required_history_is_one(self):
        """Modules that need only the current scan default to required_history=1."""
        assert _MinimalModule.required_history == 1

    def test_multi_scan_module_declares_required_history(self):
        """A module needing N scans declares required_history=N."""
        assert _MultiScanModule.required_history == 2

    def test_required_history_is_class_variable_not_instance_state(self):
        """required_history is a ClassVar — same value on all instances."""
        m1 = _MultiScanModule()
        m2 = _MultiScanModule()
        assert m1.required_history == m2.required_history == 2

    def test_a_module_has_no_run_header_line_unless_it_says_one(self):
        """The run header names methods; a module that declares none adds no line."""
        assert _MinimalModule.method_line(None) is None

    def test_a_module_reads_the_earlier_outputs_it_declares_whatever_its_options(self):
        """What a module reads of earlier scans is ``prior_inputs`` unless an option decides."""

        class _ReadsBack(_MinimalModule):
            prior_inputs = ("some_output",)

        assert _MinimalModule.prior_inputs_for(None) == ()
        assert _ReadsBack.prior_inputs_for(None) == ("some_output",)

    def test_a_module_has_no_options_unless_it_declares_their_model(self):
        """Without an options model a module has no options and ships no preset."""
        assert _MinimalModule.options_class is None
        assert _MinimalModule.options_preset() is None

    def test_a_modules_config_is_its_validated_options_unless_it_derives_more(self):
        """``configure`` hands ``run()`` the options as they are, by default."""
        options = object()

        assert _MinimalModule.configure(options, SHARED) is options

    def test_a_modules_rows_are_not_keyed_by_its_options_unless_it_says_so(self):
        """Rows made with other options replace a module's own, by default."""
        assert _MinimalModule.rows_keyed_by_options is False
