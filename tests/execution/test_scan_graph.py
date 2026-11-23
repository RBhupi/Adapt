"""The live modules' own declarations form one per-scan graph.

Each real module is replaced by a stub carrying its declarations (name, inputs,
outputs, required_history), so these tests pin what the declarations mean for a
scan without running any science.
"""

import pytest

from adapt.contracts import ProductTableWrite, StoreRead
from adapt.execution.graph.builder import data_edges
from adapt.execution.graph.executor import GraphExecutor
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import (
    _ensure_modules_registered,
    prior_depth,
    product_views,
    scan_graph,
    store_read_edges,
    store_read_tables,
)
from adapt.modules.base import BaseModule

pytestmark = pytest.mark.unit

LIVE = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]


def _declared_as(module_class: type[BaseModule]) -> BaseModule:
    declared = {
        key: getattr(module_class, key) for key in ("name", "inputs", "outputs", "required_history")
    }
    outputs = dict.fromkeys(module_class.outputs, module_class.name)
    return type(f"_{module_class.name}", (BaseModule,), {**declared, "run": lambda s, c: outputs})()


def _live_modules() -> list[BaseModule]:
    _ensure_modules_registered()
    return [_declared_as(registry.get(name)) for name in LIVE]


def _skipped(history: int) -> set[str]:
    """Names of the modules skipped on a scan with ``history`` usable prior scans."""
    return set(GraphExecutor(scan_graph(_live_modules(), {})).run({}, history=history).skipped)


def test_the_views_of_every_catalog_module_are_listed():
    _ensure_modules_registered()

    assert [view.name for view in product_views()] == ["cells_by_scan"]


def test_first_scan_runs_the_modules_that_need_no_earlier_scan():
    """Volume stats need the scan's cells, not their tracks: they do not wait."""
    assert _skipped(history=0) == {"projection", "analysis", "tracking"}


def test_every_module_runs_once_a_prior_scan_is_usable():
    assert _skipped(history=1) == set()


def test_what_each_module_reads_is_a_graph_edge():
    edges = data_edges(_live_modules())

    assert ("detection", "projection", "segmented_ds") in edges
    assert ("ingest", "cell_volume_stats", "grid_ds") in edges
    assert ("detection", "cell_volume_stats", "segmented_ds") in edges
    assert ("analysis", "tracking", "cell_adjacency") in edges
    assert not [edge for edge in edges if edge[:2] == ("tracking", "cell_volume_stats")]


def test_a_module_config_is_provided_by_the_runtime():
    class _NeedsItsConfig(BaseModule):
        name = "probe"
        inputs = ["probe_config", "scan_time", "prior_scans"]
        outputs = ["probe_out"]

        def run(self, context: dict) -> dict:
            return {}

    (node,) = scan_graph([_NeedsItsConfig()], {})

    assert node.dependencies == []


def test_a_module_listed_without_its_producer_is_told_which_module_to_list():
    modules = [m for m in _live_modules() if m.name in ("ingest", "detection", "tracking")]

    with pytest.raises(ValueError) as excinfo:
        scan_graph(modules, {})

    message = str(excinfo.value)
    assert "Module 'tracking' needs input 'projected_ds'" in message
    assert "which module 'projection' produces — list 'projection' too" in message


def test_over_a_stored_run_a_stored_output_needs_no_producer():
    """A module reads from the store what a module not listed stored before."""
    from adapt.modules.xlma_stat.node import XlmaStatModule

    (node,) = scan_graph([XlmaStatModule()], {}, stored_run=True)

    assert node.dependencies == []
    with pytest.raises(ValueError, match="list 'tracking' too"):
        scan_graph([XlmaStatModule()], {})


def test_a_modules_options_decide_what_it_reads():
    class _ReadsLessWhenTold(BaseModule):
        name = "probe"
        inputs = ["probe_config", "scan_time", "grid_ds"]
        outputs = ["probe_out"]

        @classmethod
        def inputs_for(cls, config) -> list[str]:
            return cls.inputs if config["needs_grid"] else ["probe_config", "scan_time"]

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()  # so ingest is known as grid_ds's producer
    with pytest.raises(ValueError, match="list 'ingest' too"):
        scan_graph([_ReadsLessWhenTold()], {"probe_config": {"needs_grid": True}})

    (node,) = scan_graph([_ReadsLessWhenTold()], {"probe_config": {"needs_grid": False}})
    assert node.inputs == ["probe_config", "scan_time"]


def _real(*names: str) -> list[BaseModule]:
    """The registered modules themselves, with everything they declare."""
    _ensure_modules_registered()
    return [registry.get(name)() for name in names]


def test_an_earlier_scan_output_must_come_from_a_listed_module():
    """Detection's seed carry reads projection's labels of the scan before."""
    with pytest.raises(ValueError) as excinfo:
        scan_graph(_real("ingest", "detection"), {})

    message = str(excinfo.value)
    assert "Module 'detection' reads output 'projected_ds' of earlier scans" in message
    assert "which module 'projection' produces" in message
    assert "list 'projection' under modules:" in message
    assert "or change detection's options so it does not read it" in message
    assert "(adapt config --params detection)" in message


def test_a_module_may_read_its_own_outputs_of_earlier_scans():
    """Detection reads back its own carry ages and cell count: they need no other module."""
    nodes = scan_graph(_real("ingest", "detection", "projection"), {})

    assert [node.name for node in nodes] == ["ingest", "detection", "projection"]


class _ReadsBackWhenTold(BaseModule):
    """Reads the earlier scans' ``cell_stats`` (analysis's, a stored output) when told to."""

    name = "probe"
    inputs = ["probe_config", "prior_scans"]
    outputs = ["probe_out"]
    prior_inputs = ("cell_stats",)

    @classmethod
    def prior_inputs_for(cls, config) -> tuple[str, ...]:
        return cls.prior_inputs if config["reads_back"] else ()

    def run(self, context: dict) -> dict:
        return {}


def test_a_modules_options_decide_what_it_reads_of_earlier_scans():
    _ensure_modules_registered()  # so analysis is known as cell_stats's producer

    with pytest.raises(ValueError, match="list 'analysis' under modules:"):
        scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": True}})

    (node,) = scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": False}})
    assert node.dependencies == []


def test_over_a_stored_run_an_earlier_scan_output_still_needs_its_producer():
    """Earlier scans' outputs are kept by the runtime as it goes, never read from the store."""
    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsBackWhenTold()], {"probe_config": {"reads_back": True}}, stored_run=True)

    assert "Module 'probe' reads output 'cell_stats' of earlier scans" in str(excinfo.value)


def test_an_earlier_scan_output_no_module_produces_is_refused():
    class _ReadsNothingKnown(BaseModule):
        name = "probe"
        inputs = ["prior_scans"]
        outputs = ["probe_out"]
        prior_inputs = ("no_such_output",)

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsNothingKnown()], {})

    message = str(excinfo.value)
    assert "Module 'probe' reads output 'no_such_output' of earlier scans" in message
    assert "no registered module produces" in message


class _ReadsNothingBack(BaseModule):
    name = "plain"
    inputs = ["scan_time"]
    outputs = ["plain_out"]

    def run(self, context: dict) -> dict:
        return {}


class _WaitsForThree(BaseModule):
    name = "waits"
    inputs = ["prior_scans"]
    outputs = ["waits_out"]
    required_history = 4

    def run(self, context: dict) -> dict:
        return {}


def test_no_earlier_scan_is_kept_when_no_module_reads_one():
    assert prior_depth([_ReadsNothingBack()], {}) == 0
    assert prior_depth([], {}) == 0


def test_a_module_that_reads_earlier_outputs_without_waiting_is_kept_the_last_scan():
    assert prior_depth([_ReadsBackWhenTold()], {"probe_config": {"reads_back": True}}) == 1
    assert prior_depth([_ReadsBackWhenTold()], {"probe_config": {"reads_back": False}}) == 0


def test_a_module_is_kept_as_many_scans_as_it_waits_for():
    assert prior_depth([_WaitsForThree()], {}) == 3


def test_the_depth_is_the_largest_need_of_the_listed_modules():
    modules = [_ReadsBackWhenTold(), _WaitsForThree()]

    assert prior_depth(modules, {"probe_config": {"reads_back": True}}) == 3


@pytest.mark.parametrize("seed_carry_frames", [1, 0])
def test_the_plain_nexrad_pipeline_keeps_one_earlier_scan_with_the_seed_carry_on_or_off(
    seed_carry_frames, make_detection_config, make_projection_config
):
    configs = {
        "detection_config": make_detection_config(seed_carry_frames=seed_carry_frames),
        "projection_config": make_projection_config(),
    }

    assert prior_depth(_real(*LIVE), configs) == 1


def test_without_projection_and_the_seed_carry_no_earlier_scan_is_kept(make_detection_config):
    configs = {"detection_config": make_detection_config(seed_carry_frames=0)}

    assert prior_depth(_real("ingest", "detection", "cell_volume_stats"), configs) == 0


def test_a_table_source_is_made_of_that_table():
    _ensure_modules_registered()

    assert [table.table for table in store_read_tables("cell_tracks")] == ["cell_tracks"]


def test_a_view_source_is_made_of_its_row_table_what_it_reads_and_every_table_with_its_key():
    _ensure_modules_registered()

    tables = [table.table for table in store_read_tables("cells_by_scan")]

    assert tables[0] == "cell_identity"
    assert set(tables) == {
        "cell_identity",
        "cell_events",
        "cell_stats",
        "cell_volume_stats",
        "xlma_stat_scan",
    }
    assert len(tables) == len(set(tables))


def test_a_source_no_module_declares_is_refused_by_name():
    _ensure_modules_registered()

    with pytest.raises(ValueError, match="'no_such_rows' is not a table or a view"):
        store_read_tables("no_such_rows")


_PROBE_ROWS = ProductTableWrite(
    key="probe_rows", table="probe_rows", primary_key=("run_id", "scan_id", "k")
)


class _WritesProbeRows(BaseModule):
    name = "probe_writer"
    inputs = ["scan_time"]
    outputs = ["probe_rows"]
    persistence = (_PROBE_ROWS,)

    def run(self, context: dict) -> dict:
        return {"probe_rows": None}


class _ReadsProbeRows(BaseModule):
    name = "probe_reader"
    inputs = ["probe_so_far"]
    outputs = ["probe_seen"]
    store_reads = (StoreRead(key="probe_so_far", source="probe_rows", up_to_scan=True),)

    def run(self, context: dict) -> dict:
        return {"probe_seen": None}


class _ReadsItsOwnRows(BaseModule):
    name = "probe_self"
    inputs = ["probe_self_so_far"]
    outputs = ["probe_self_rows"]
    persistence = (
        ProductTableWrite(
            key="probe_self_rows", table="probe_self_rows", primary_key=("run_id", "scan_id")
        ),
    )
    store_reads = (StoreRead(key="probe_self_so_far", source="probe_self_rows", up_to_scan=True),)

    def run(self, context: dict) -> dict:
        return {"probe_self_rows": None}


@pytest.fixture
def probes():
    """The stand-in writer and readers, registered for the test only."""
    for module in (_WritesProbeRows, _ReadsProbeRows, _ReadsItsOwnRows):
        registry.register(module)
    yield
    for module in (_WritesProbeRows, _ReadsProbeRows, _ReadsItsOwnRows):
        registry.unregister(module.name)


def test_a_store_read_gives_an_edge_from_every_listed_module_that_writes_one_of_its_tables(
    probes,
):
    reader, writer = _ReadsProbeRows(), _WritesProbeRows()

    assert store_read_edges([reader, writer]) == [("probe_writer", "probe_reader", "probe_rows")]
    assert store_read_edges([reader]) == []
    assert store_read_edges([_ReadsItsOwnRows()]) == []


def _order_of(modules: list[BaseModule], *, stored_run: bool = False) -> list[str]:
    """The names of ``modules`` in the order the executor runs them for one scan."""
    order: list[str] = []
    nodes = scan_graph(modules, {}, stored_run=stored_run)
    GraphExecutor(nodes).run(
        {}, history=1, before_node=lambda node, context: order.append(node.name)
    )
    return order


def test_a_store_reader_runs_after_a_listed_writer_whatever_the_list_order(probes):
    assert _order_of([_ReadsProbeRows(), _WritesProbeRows()]) == ["probe_writer", "probe_reader"]


def test_a_store_read_key_is_provided_by_the_runtime(probes):
    reader, _ = scan_graph([_ReadsProbeRows(), _WritesProbeRows()], {})

    assert [dep.name for dep in reader.dependencies] == ["probe_writer"]
    assert "probe_so_far" in reader.inputs


def test_a_new_run_must_list_a_module_that_writes_the_rows_it_reads(probes):
    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsProbeRows()], {})

    message = str(excinfo.value)
    assert "Module 'probe_reader' reads 'probe_rows' from the store" in message
    assert "module 'probe_writer' writes" in message
    assert "list 'probe_writer' too" in message


def test_over_a_stored_run_the_writer_need_not_be_listed(probes):
    (node,) = scan_graph([_ReadsProbeRows()], {}, stored_run=True)

    assert node.dependencies == []


def test_a_read_of_a_source_no_module_declares_is_refused_at_start():
    class _ReadsNothingKnown(BaseModule):
        name = "probe"
        inputs = ["probe_so_far"]
        outputs = ["probe_out"]
        store_reads = (StoreRead(key="probe_so_far", source="no_such_rows", up_to_scan=False),)

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsNothingKnown()], {}, stored_run=True)

    message = str(excinfo.value)
    assert "Module 'probe' reads 'no_such_rows' from the store" in message
    assert "is not a table or a view that a registered module declares" in message


def test_a_read_of_a_decision_table_is_refused_at_start():
    class _ReadsTheDecisionLog(BaseModule):
        name = "probe"
        inputs = ["probe_decisions"]
        outputs = ["probe_out"]
        store_reads = (StoreRead(key="probe_decisions", source="tracking_cells", up_to_scan=False),)

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsTheDecisionLog()], {}, stored_run=True)

    message = str(excinfo.value)
    assert "Module 'probe' reads 'tracking_cells' from the store" in message
    assert "is not a table or a view that a registered module declares" in message


def test_a_read_up_to_the_scan_of_rows_that_are_not_per_scan_is_refused():
    class _ReadsTracksUpToTheScan(BaseModule):
        name = "probe"
        inputs = ["probe_tracks"]
        outputs = ["probe_out"]
        store_reads = (StoreRead(key="probe_tracks", source="cell_tracks", up_to_scan=True),)

        def run(self, context: dict) -> dict:
            return {}

    _ensure_modules_registered()

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsTracksUpToTheScan()], {}, stored_run=True)

    message = str(excinfo.value)
    assert "Module 'probe' reads 'cell_tracks' from the store up to the scan" in message
    assert "declare the read with up_to_scan=False" in message


def test_target_selection_waits_for_every_listed_module_that_writes_the_cells_it_reads():
    (target_selection, *_) = scan_graph(_real("target_selection", *LIVE), {})

    assert {dep.name for dep in target_selection.dependencies} == {
        "analysis",
        "tracking",
        "cell_volume_stats",
    }


def test_a_module_that_feeds_on_its_own_reader_is_refused_at_start(probes):
    """It reads what the reader returns and writes rows the reader reads back."""

    class _FeedsOnTheReader(BaseModule):
        name = "probe_feeder"
        inputs = ["probe_seen"]
        outputs = ["probe_fed_rows"]
        persistence = (
            ProductTableWrite(
                key="probe_fed_rows", table="probe_rows", primary_key=("run_id", "scan_id", "k")
            ),
        )

        def run(self, context: dict) -> dict:
            return {}

    with pytest.raises(ValueError) as excinfo:
        scan_graph([_ReadsProbeRows(), _FeedsOnTheReader()], {})

    assert "Modules probe_reader, probe_feeder wait for each other" in str(excinfo.value)
