# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Architecture tests: enforce module-independence without hardcoding module names.

These tests discover adapt.modules subpackages at runtime and verify that
no scientific module imports from any other scientific module. New modules
are picked up automatically — no test edits required.

Run: pytest tests/test_architecture.py
"""

import ast
import importlib
import pkgutil
import re
from pathlib import Path

import pytest

# Skip the entire file gracefully if adapt is not installed in this environment.
# This prevents VSCode pytest discovery errors when the wrong interpreter is active.
adapt_modules = pytest.importorskip(
    "adapt.modules",
    reason="adapt not installed in this Python environment — activate adapt_env",
)


def _discover_module_packages() -> list[str]:
    """Return all immediate subpackage names under adapt.modules."""
    return [
        f"adapt.modules.{info.name}"
        for info in pkgutil.iter_modules(adapt_modules.__path__)
        if info.ispkg
    ]


def _source_files(package_name: str) -> list[Path]:
    """Return all .py files belonging to a package."""
    mod = importlib.import_module(package_name)
    assert mod.__file__ is not None
    pkg_dir = Path(mod.__file__).parent
    return list(pkg_dir.rglob("*.py"))


def _imported_adapt_modules(py_file: Path) -> set[str]:
    """Parse a .py file and return the set of adapt.modules.* names it imports."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("adapt.modules."):
                    imports.add(alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("adapt.modules.")
        ):
            imports.add(node.module)
    return imports


# Build the test matrix at collection time — works for any future module.
_PACKAGES = _discover_module_packages()
_SKIP = {"adapt.modules.base"}  # base.py is shared infrastructure, not a science module
_MODULE_INTERFACE = "adapt.modules.base"


@pytest.mark.parametrize("pkg", [p for p in _PACKAGES if p not in _SKIP])
def test_module_does_not_import_other_modules(pkg: str) -> None:
    """A module package imports no other module package; of ``adapt.modules`` it
    may import only ``adapt.modules.base``, the interface its node implements.

    This test is parameterised over every subpackage discovered under adapt.modules.
    Adding a new module directory makes it appear here automatically.
    """
    files = _source_files(pkg)
    violations: list[str] = []

    for py_file in files:
        for imported in _imported_adapt_modules(py_file):
            # Allow self-imports (within the same subpackage)
            if not imported.startswith(pkg) and imported != _MODULE_INTERFACE:
                violations.append(f"  {py_file.name}: imports {imported!r}")

    assert not violations, (
        f"\n{pkg} imports from other scientific modules — "
        "shared types belong in adapt.contracts:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize("pkg", [p for p in _PACKAGES if p not in _SKIP])
def test_module_does_not_import_execution_or_runtime(pkg: str) -> None:
    """Scientific module must not import from adapt.execution or adapt.runtime."""
    forbidden_prefixes = ("adapt.execution", "adapt.runtime", "adapt.persistence")
    files = _source_files(pkg)
    violations: list[str] = []

    for py_file in files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if any(name.startswith(p) for p in forbidden_prefixes):
                    violations.append(f"  {py_file.name}: imports {name!r}")

    assert not violations, (
        f"\n{pkg} imports from layers above it — "
        "modules must only depend on contracts/ and utils/:\n" + "\n".join(violations)
    )


# ── Canonical scan-time serialization (single source of truth) ────────────────
# scan_id is the cross-table join key; scan_time is ordering/display METADATA —
# but it must still serialize identically everywhere (one canonical format), or
# time-ordered reads and displays silently disagree. The format lives in exactly
# one function — adapt.utils.time.to_scan_iso. This fitness function fails if
# any other code formats scan-time independently. (Evidence for the join-key
# change: the pre-identity dashboard failure class — wall-clock-corrupted rows,
# "+00:00" vs "Z" divergence, and ±60/±90 s consumer tolerance windows.)

_SCAN_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SRC_ADAPT = Path(__file__).parents[1] / "src" / "adapt"


def _rel(py_file: Path, root: Path) -> str:
    """Path of *py_file* relative to *root*, always with '/' separators.

    These fitness functions compare paths against literal module locations, so
    the separator must not depend on the OS the suite runs on.
    """
    return py_file.relative_to(root).as_posix()


def test_scan_time_format_is_defined_in_exactly_one_place() -> None:
    """The canonical scan-time metadata format may appear only in adapt.utils.time."""
    offenders: list[str] = []
    for py_file in _SRC_ADAPT.rglob("*.py"):
        if _SCAN_TIME_FORMAT in py_file.read_text(encoding="utf-8"):
            offenders.append(_rel(py_file, _SRC_ADAPT))

    assert offenders == ["utils/time.py"], (
        "scan-time format must be centralized in adapt.utils.time.to_scan_iso — "
        f"found the literal {_SCAN_TIME_FORMAT!r} in: {offenders}. "
        "Serialize scan_time via to_scan_iso (or let ModuleOutputWriter do it); "
        "never hardcode the format. Rows JOIN on scan_id; scan_time is the "
        "single-format ordering/display metadata."
    )


def test_adapt_name_is_never_all_caps() -> None:
    """The product is 'Adapt', never 'ADAPT' — in output, prints, comments, or docstrings.

    Matches the standalone token only, so 'arm_adaptive', 'ADAPTIVE', 'ADAPTER' are fine.
    """
    offenders = [
        f"{_rel(py_file, _SRC_ADAPT)}:{i}"
        for py_file in _SRC_ADAPT.rglob("*.py")
        for i, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\bADAPT\b", line)
    ]
    assert not offenders, "Use 'Adapt', not 'ADAPT':\n" + "\n".join(offenders)


# ── Determinism: no wall clock or global RNG in scientific modules ─────────────
# Identical inputs + config must produce identical outputs. A module that reads
# the wall clock or numpy's global RNG breaks that silently. The acquisition
# module is the single allowed exception: it injects its clock (testable) and
# stamps queue telemetry, which never enters scientific outputs.

_WALL_CLOCK_ALLOWED = {"acquisition/module.py"}


def _nondeterminism_calls(py_file: Path) -> list[str]:
    """Return wall-clock / global-RNG usages in a file (AST, ignores docstrings)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        if isinstance(value, ast.Name):
            if value.id == "datetime" and node.attr in {"now", "utcnow", "today"}:
                offenders.append(f"line {node.lineno}: datetime.{node.attr}")
            elif value.id == "time" and node.attr == "time":
                offenders.append(f"line {node.lineno}: time.time")
        elif (
            isinstance(value, ast.Attribute)
            and value.attr == "random"
            and isinstance(value.value, ast.Name)
            and value.value.id in {"np", "numpy"}
        ):
            offenders.append(f"line {node.lineno}: np.random.{node.attr}")
    return offenders


@pytest.mark.parametrize("pkg", [p for p in _PACKAGES if p not in _SKIP])
def test_module_does_not_read_wall_clock_or_global_rng(pkg: str) -> None:
    """Scientific modules must be deterministic: no wall clock, no global RNG."""
    modules_dir = _SRC_ADAPT / "modules"
    violations: list[str] = []

    for py_file in _source_files(pkg):
        rel = _rel(py_file, modules_dir)
        if rel in _WALL_CLOCK_ALLOWED:
            continue
        violations.extend(f"  {rel}: {hit}" for hit in _nondeterminism_calls(py_file))

    assert not violations, (
        f"\n{pkg} reads the wall clock or global RNG — identical inputs must give "
        "identical outputs. Inject a clock (see acquisition/module.py) or seed an "
        "explicit np.random.default_rng from config:\n" + "\n".join(violations)
    )


def test_the_execution_layer_does_not_read_wall_clock() -> None:
    """Graph nodes must be deterministic too, not just modules/*.

    The pre-identity wall-clock scan_time fallback lived in the ingest node,
    then a file of the execution layer. Scan times come from the source
    boundary; nothing in the execution layer may substitute a clock.
    """
    execution_dir = _SRC_ADAPT / "execution"
    violations: list[str] = []
    for py_file in execution_dir.rglob("*.py"):
        violations.extend(
            f"  {_rel(py_file, execution_dir)}: {hit}" for hit in _nondeterminism_calls(py_file)
        )

    assert not violations, (
        "\nadapt.execution reads the wall clock or global RNG — scan times are "
        "owned by the source boundary and identical inputs must give identical "
        "outputs:\n" + "\n".join(violations)
    )


# ── Heavy third-party dependencies stay in their owning component ──────────────
# Each heavy or domain-specific dependency is imported by exactly one component.
# If one leaks (e.g. matplotlib into modules/, cv2 into runtime/), the core
# becomes uninstallable headless and modules stop being swappable.

_DEP_HOMES = {
    "matplotlib": ("visualization/", "consumers/"),
    "contextily": ("visualization/", "consumers/"),
    "tkinter": ("consumers/",),
    "cv2": ("modules/projection/",),
    "pyart": ("modules/ingest/", "modules/detection/"),
    "boto3": ("downloaders/",),
    "botocore": ("downloaders/",),
    "networkx": ("modules/tracking/",),
    "pyxlma": ("modules/xlma_stat/",),
    "sklearn": ("modules/xlma_stat/",),
    "skimage": ("modules/detection/", "modules/analysis/"),
    "pyproj": ("modules/xlma_stat/", "consumers/"),
}


def _top_level_imports(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            tops.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            tops.add(node.module.split(".")[0])
    return tops


@pytest.mark.parametrize("dep", sorted(_DEP_HOMES))
def test_heavy_dependency_stays_in_its_component(dep: str) -> None:
    """Each heavy dependency may only be imported inside its owning component."""
    allowed = _DEP_HOMES[dep]
    violations: list[str] = []

    for py_file in _SRC_ADAPT.rglob("*.py"):
        rel = _rel(py_file, _SRC_ADAPT)
        if dep in _top_level_imports(py_file) and not rel.startswith(allowed):
            violations.append(f"  {rel}")

    assert not violations, (
        f"\n'{dep}' may only be imported under {list(allowed)} — move the call "
        "behind that component's interface instead of importing directly:\n" + "\n".join(violations)
    )


# ── Context-dict ownership: declared IO must be coherent ───────────────────────
# The context dict is the inter-module interface. Two modules claiming the same
# output key would shadow each other; an input nobody produces and nobody seeds
# is a typo that only fails at runtime. These tests make both fail at CI time.

# The keys the runtime provides (legitimate module inputs that no module
# produces) are ``adapt.contracts.SCAN_SEEDS`` and one ``<name>_config`` per
# module; a module may also list the keys of its own declared store reads
# (``store_reads``), which the runtime fills from the store.


def _registered_default_modules():
    """One instance of every catalog module."""
    from adapt.execution.module_registry import registry
    from adapt.execution.pipeline_builder import _ensure_modules_registered

    _ensure_modules_registered()
    return registry.create_modules()


def test_module_output_keys_are_unique() -> None:
    """No two registered modules may declare the same output key."""
    producers: dict[str, str] = {}
    collisions: list[str] = []

    for module in _registered_default_modules():
        for key in module.outputs:
            if key in producers:
                collisions.append(f"  '{key}': {producers[key]} and {module.name}")
            else:
                producers[key] = module.name

    assert not collisions, (
        "\nOutput-key collision in the context dict — later modules would shadow "
        "earlier results:\n" + "\n".join(collisions)
    )


def test_module_inputs_are_produced_or_seeded() -> None:
    """Every declared input is another module's output, a runtime-seeded key, or
    one of that module's own store reads."""
    from adapt.contracts import SCAN_SEEDS, config_key

    modules = _registered_default_modules()
    produced = {key for m in modules for key in m.outputs}
    provided = SCAN_SEEDS | {config_key(m.name) for m in modules}
    orphans: list[str] = []

    for module in modules:
        own_reads = {read.key for read in module.store_reads}
        for key in module.inputs:
            if key not in produced and key not in provided and key not in own_reads:
                orphans.append(f"  {module.name} needs '{key}'")

    assert not orphans, (
        "\nInput key is neither produced by any module, seeded by the runtime, nor "
        "read from the store by the module itself — either a typo in the declaration, "
        "a missing StoreRead in the module's store_reads, or adapt.contracts.SCAN_SEEDS "
        "needs the new runtime-provided key:\n" + "\n".join(orphans)
    )


def test_a_store_read_is_an_input_of_its_module_from_a_declared_source() -> None:
    """A module lists what it reads from the store in ``inputs``, from a table or
    a view a catalog module declares."""
    from adapt.execution.pipeline_builder import store_read_tables

    for module in _registered_default_modules():
        for read in module.store_reads:
            assert read.key in type(module).inputs, (
                f"{module.name} reads '{read.key}' from the store without listing it in inputs"
            )
            store_read_tables(read.source)  # raises for a source no module declares


def test_the_runtime_store_reader_names_no_table_or_view() -> None:
    """The runtime reads what the modules declare; it names none of their tables
    or views, so a module's rows change with zero edits in ``runtime/``."""
    from adapt.contracts import ProductTableWrite, ProductView

    declared = {
        spec.table if isinstance(spec, ProductTableWrite) else spec.name
        for module in _registered_default_modules()
        for spec in module.persistence
        if isinstance(spec, ProductTableWrite | ProductView)
    }
    text = (_SRC_ADAPT / "runtime" / "store_inputs.py").read_text(encoding="utf-8")
    named = sorted(name for name in declared if re.search(rf"\b{re.escape(name)}\b", text))

    assert not named, f"runtime/store_inputs.py names module tables or views: {named}"


# Outputs knowingly shipped without a contract validator. This list may only
# shrink: never add to it — write a check_* validator in adapt.contracts instead.
_UNCONTRACTED_OUTPUTS = {
    ("ingest", "grid_ds"),
    ("detection", "num_cells"),
}


def test_module_outputs_carry_contracts() -> None:
    """Every module output has a validator, except the pinned shrink-only list."""
    uncontracted = {
        (module.name, key)
        for module in _registered_default_modules()
        for key in module.outputs
        if key not in (module.output_contracts or {})
    }

    new = uncontracted - _UNCONTRACTED_OUTPUTS
    assert not new, (
        f"\nNew module outputs without contracts: {sorted(new)}. "
        "Add a check_* validator to adapt.contracts and register it in "
        "output_contracts — do not extend _UNCONTRACTED_OUTPUTS."
    )

    stale = _UNCONTRACTED_OUTPUTS - uncontracted
    assert not stale, (
        f"\n_UNCONTRACTED_OUTPUTS contains entries that now have contracts: "
        f"{sorted(stale)}. Remove them so the ratchet only tightens."
    )


def test_the_processor_names_no_module_output() -> None:
    """What the processor reads of a scan's results it reads under the keys of
    ``adapt.contracts`` (``scan_report``), never under a catalog module's own
    output name: a module is replaced with zero edits in ``runtime/``."""
    outputs = {key for module in _registered_default_modules() for key in module.outputs}
    tree = ast.parse((_SRC_ADAPT / "runtime" / "processor.py").read_text(encoding="utf-8"))
    named = sorted(
        {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        & outputs
    )

    assert not named, f"runtime/processor.py names module outputs: {named}"


# ── import-linter config and source tree stay in sync ────────────────────────
# .importlinter is the single authoritative layer stack; src/adapt is reality.

_REPO_ROOT = Path(__file__).parents[1]
_IMPORTLINTER = _REPO_ROOT / ".importlinter"


def _importlinter_field(field: str) -> list[str]:
    """Indented entries of a `field =` list in the .importlinter layers contract."""
    lines = _IMPORTLINTER.read_text(encoding="utf-8").splitlines()
    entries: list[str] = []
    in_field = False
    for line in lines:
        if line.strip() == f"{field} =":
            in_field = True
            continue
        if in_field:
            if line.startswith((" ", "\t")) and line.strip():
                entries.append(line.strip())
            else:
                break
    return entries


def test_layer_stack_covers_the_real_source_tree() -> None:
    """Every top-level package/module under src/adapt is placed in a layer
    (or deliberately listed in exhaustive_ignores). lint-imports enforces this
    too; this test keeps the guarantee even where only pytest runs."""
    placed = {name for line in _importlinter_field("layers") for name in line.split("|")}
    placed = {name.strip() for name in placed}
    ignored = set(_importlinter_field("exhaustive_ignores"))

    actual = {
        p.name
        for p in _SRC_ADAPT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists() and p.name != "__pycache__"
    } | {p.stem for p in _SRC_ADAPT.glob("*.py") if p.stem != "__init__"}

    assert placed | ignored == actual, (
        f"Layer stack + exhaustive_ignores != src/adapt tree.\n"
        f"  unplaced packages: {sorted(actual - placed - ignored)}\n"
        f"  phantom entries:   {sorted((placed | ignored) - actual)}\n"
        "Place new packages in a layer in .importlinter."
    )
    assert not placed & ignored, (
        f"{sorted(placed & ignored)} appear both in the layer stack and in "
        "exhaustive_ignores — pick one."
    )


# ── DDL single home: static CREATE TABLE lives in configuration/schemas/*.sql ──
# The store's bookkeeping tables are defined once, in the .sql schema files.
# api/store_client.py queries these tables by name, so every extra place that
# defines them is another chance for silent reader/writer drift. The allowlist
# below pins today's exceptions and may only SHRINK — move DDL into the .sql
# files (or through SchemaLedger.freeze), never into a new Python file.

_DDL_ALLOWED_PY = {
    # The sanctioned dynamic creator: frozen first-frame product tables.
    "persistence/products.py",
}

# Case-sensitive: SQL here is uppercase by convention; lowercase "Create
# tables" in docstring prose must not count.
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE")


def test_create_table_statements_have_one_home() -> None:
    """CREATE TABLE may appear only in configuration/schemas/*.sql or the pinned files."""
    offenders = [
        rel
        for py_file in _SRC_ADAPT.rglob("*.py")
        if _CREATE_TABLE.search(py_file.read_text(encoding="utf-8"))
        and (rel := _rel(py_file, _SRC_ADAPT)) not in _DDL_ALLOWED_PY
    ]
    assert not offenders, (
        f"\nCREATE TABLE outside its home: {offenders}. Table definitions live in "
        "configuration/schemas/*.sql (static tables) or go through "
        "SchemaLedger.freeze (dynamic product tables) — do not extend _DDL_ALLOWED_PY."
    )

    stale = {
        rel
        for rel in _DDL_ALLOWED_PY
        if not _CREATE_TABLE.search((_SRC_ADAPT / rel).read_text(encoding="utf-8"))
    }
    assert not stale, (
        f"\n_DDL_ALLOWED_PY contains files with no CREATE TABLE left: {sorted(stale)}. "
        "Remove them so the ratchet only tightens."
    )


# ── Field-generic core: reflectivity stat-column literals are quarantined ─────
# After the tracking_field refactor, stat columns are minted via
# adapt.contracts.stat_column and read via the configured field. The literal
# "radar_reflectivity_max" appears nowhere in the package. Shrink-only: it stays empty.

_REFL_LITERAL_ALLOWED: set[str] = set()


def test_reflectivity_stat_literal_is_quarantined() -> None:
    offenders = [
        rel
        for py_file in _SRC_ADAPT.rglob("*.py")
        if "radar_reflectivity_max" in py_file.read_text(encoding="utf-8")
        and (rel := _rel(py_file, _SRC_ADAPT)) not in _REFL_LITERAL_ALLOWED
    ]
    assert not offenders, (
        f"\n'radar_reflectivity_max' literal outside the quarantine: {offenders}. "
        "Mint stat columns via adapt.contracts.stat_column and resolve the "
        "field from config/provenance."
    )
    stale = {
        rel
        for rel in _REFL_LITERAL_ALLOWED
        if "radar_reflectivity_max" not in (_SRC_ADAPT / rel).read_text(encoding="utf-8")
    }
    assert not stale, (
        f"\n_REFL_LITERAL_ALLOWED has stale entries: {sorted(stale)} — "
        "remove them so the ratchet only tightens."
    )


# ── Telemetry ids stay out of the science context dict ────────────────────────
# Observability correlation ids (trace/span/scan/pipeline/...) travel out-of-band
# in contextvars. If one ever appeared as a module input/output key it would couple
# telemetry to the science data contract and could perturb determinism. Pin it.


def test_obs_context_fields_never_in_module_io() -> None:
    """No ObsContext field name may be a module input/output key.

    Runtime-seeded SCIENCE identity keys are exempt: ``scan_id`` is the
    store join key (sha256 of raw bytes) and the uid-v2 birth input —
    ObsContext.scan_id is a correlation id DERIVED from it, not the
    reverse, so a module declaring it consumes science identity, not
    telemetry.
    """
    from adapt.contracts import SCAN_SEEDS
    from adapt.contracts.observability import ObsContext

    obs_fields = set(ObsContext.__dataclass_fields__) - SCAN_SEEDS
    leaks: list[str] = []
    for module in _registered_default_modules():
        for key in list(module.inputs) + list(module.outputs):
            if key in obs_fields:
                leaks.append(f"  {module.name}: '{key}'")

    assert not leaks, (
        "\nTelemetry correlation ids leaked into the science context dict — keep "
        "ObsContext fields out of module inputs/outputs:\n" + "\n".join(leaks)
    )


# ── Consumer boundary: consumers read ONLY through the store API ───────────────
# Consumers (dashboard, TSE) may import adapt.api + adapt.utils only; the store
# fitness below closes the I/O side doors the import-linter cannot see: raw
# sqlite, direct NetCDF/parquet opens, directory walks, and store-internal
# path construction. Rasters arrive in-memory via the client; tables arrive as
# DataFrames. The one sanctioned literal is the registry.db root marker used
# for repo detection in _utils.
_CONSUMER_FORBIDDEN_CALLS = {
    "open_dataset",
    "open_mfdataset",
    "load_dataset",
    "read_parquet",
    "glob",
    "rglob",
    "iterdir",
    "connect",  # sqlite3.connect / duckdb.connect
}
_CONSUMER_FORBIDDEN_LITERALS = ("catalog.db", "products.db", "adapt_registry.db")
_CONSUMER_LITERAL_ALLOWLIST = {"_utils.py"}  # legacy-root detection marker only


def _consumer_violations(py_file: Path) -> list[str]:
    import ast

    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in {"sqlite3", "duckdb"}:
                    violations.append(f"{py_file.name}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"sqlite3", "duckdb"}:
                violations.append(f"{py_file.name}:{node.lineno} imports {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _CONSUMER_FORBIDDEN_CALLS:
                violations.append(f"{py_file.name}:{node.lineno} calls {name}()")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(lit in node.value for lit in _CONSUMER_FORBIDDEN_LITERALS)
            and py_file.name not in _CONSUMER_LITERAL_ALLOWLIST
        ):
            violations.append(f"{py_file.name}:{node.lineno} builds store path {node.value!r}")
    return violations


def test_consumers_read_only_through_the_store_api() -> None:
    """No consumer touches storage directly — every read goes through StoreClient.

    Guards the exact failure classes behind past dashboard bugs: raw sqlite
    connections (fd leaks), per-frame NetCDF opens (too many open files),
    directory globs (stale/foreign files), and hardcoded store paths.
    """
    consumers_dir = Path(__file__).parent.parent / "src" / "adapt" / "consumers"
    violations: list[str] = []
    for py_file in sorted(consumers_dir.rglob("*.py")):
        violations.extend(_consumer_violations(py_file))
    assert not violations, "consumers must read via the store API only:\n" + "\n".join(violations)


# ── One module graph: the designs it replaced stay gone ───────────────────────
# Every per-scan step is a module of one graph (ARCHITECTURE.md). These names
# belonged to what that graph replaced: module kinds and passes (core /
# extended / post-processing), the store's hand-built track tables, and
# consumers run as threads handing each other files. A match means a second
# mechanism is growing back beside the graph.
_REPLACED_NAMES = (
    "EXTENDED_PHASE",
    "POSTPROCESS_PHASE",
    "pipeline_phase",
    "post-persistence",
    "enrichment",
    "postprocess",
    "PostProcessor",
    "REQUIRED_SCAN_PRODUCTS",
    "TrackStore",
    "TrackTablesWrite",
    "RunFollowers",
    "CONSUMERS",
    "current_selection",
    "grid_ds_3d",
    "STORE_VIEWS",
    "register_store_view",
)
_REPLACED_SECTIONS = re.compile(r"^\s*(core|extended|consumers|postprocessing):", re.MULTILINE)


def test_names_of_the_replaced_designs_are_not_in_the_source() -> None:
    offenders = [
        f"{_rel(path, _SRC_ADAPT)}: {name}"
        for path in sorted(_SRC_ADAPT.rglob("*"))
        if path.suffix in {".py", ".yaml", ".sql"}
        for name in _REPLACED_NAMES
        if name in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "names of the designs the one module graph replaced are back:\n" + "\n".join(offenders)
    )


def test_packaged_yaml_has_no_section_of_the_old_pipeline_file() -> None:
    """A pipeline file is ``run / modules / params``; presets and defaults follow it."""
    offenders = [
        _rel(path, _SRC_ADAPT)
        for path in sorted(_SRC_ADAPT.rglob("*.yaml"))
        if _REPLACED_SECTIONS.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"old pipeline-file sections in: {offenders}"


# ── A module's options belong to the module ──────────────────────────────────
# A module's options are one model in the module, set under ``params.<module>``
# and handed to the module by ``configure`` (ARCHITECTURE.md, rule 1). These
# names belonged to what that replaced: sections of a shared config named
# differently from the modules, defined in three schemas and translated between
# them. A match means an option is defined or named outside its module again.
_REMOVED_OPTION_NAMES = (
    "UserConfig",
    "ParamConfig",
    "resolve_config",
    "param_sections",
    "build_config",
    "default_params",
    "options_text",
    "injected_global_fields",
    "config_class",
    "with_module_params",
    "coord_names",
    "labels_var",
)
# The sections that held the options, each with the module that owns them now.
_OLD_SECTIONS = {
    "reader": "ingest",
    "regridder": "ingest",
    "segmenter": "detection",
    "projector": "projection",
    "analyzer": "analysis",
    "tracker": "tracking",
}
# Names an option had under its old section and no longer has under its module
# (``tracker.cell_uid.width`` is ``tracking.uid_width``; the optical-flow values
# moved into ``projection.flow_params``). The options models cannot give these.
_OLD_OPTION_NAMES = {
    "tracker": ("cell_uid", "split_overlap", "merge_overlap"),
    "analyzer": ("adjacency_min_touching",),
    "projector": (
        "pyr_scale",
        "levels",
        "winsize",
        "iterations",
        "poly_n",
        "poly_sigma",
        "flags",
    ),
}
# A comment inside a CREATE TABLE statement is stored with the table in every
# decisions.db. Rewording it would make a new store differ from the stores
# already written, so this one old name stays.
_STORED_WITH_THE_SCHEMA = {
    "configuration/schemas/collection_decisions.sql": {"tracker.core_field_threshold"},
}


def _whole_names_in_the_source(names: list[str]) -> list[str]:
    """``file: name`` for each of ``names`` found as a whole name in the packaged
    code and text (``_build_config`` is not ``build_config``)."""
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in names) + r")\b")
    offenders = []
    for path in sorted(_SRC_ADAPT.rglob("*")):
        if path.suffix not in {".py", ".yaml", ".sql", ".md"}:
            continue
        rel = _rel(path, _SRC_ADAPT)
        found = set(pattern.findall(path.read_text(encoding="utf-8")))
        offenders.extend(
            f"{rel}: {name}" for name in sorted(found - _STORED_WITH_THE_SCHEMA.get(rel, set()))
        )
    return offenders


def test_names_of_the_removed_option_layer_are_not_in_the_source() -> None:
    offenders = _whole_names_in_the_source(list(_REMOVED_OPTION_NAMES))
    assert not offenders, (
        "names of the config layer that module-owned options replaced are back:\n"
        + "\n".join(offenders)
    )


def test_no_option_is_named_by_the_section_that_held_it() -> None:
    """Code, messages and comments name an option as ``params.<module>.<option>``."""
    from adapt.execution.module_registry import registry
    from adapt.execution.pipeline_builder import catalog_module_names

    catalog_module_names()
    old_names = [f"params.{section}" for section in _OLD_SECTIONS]
    for section, module in _OLD_SECTIONS.items():
        options_class = registry.get(module).options_class
        assert options_class is not None
        options = (*options_class.model_fields, *_OLD_OPTION_NAMES.get(section, ()))
        old_names += [f"{section}.{option}" for option in options]

    offenders = _whole_names_in_the_source(old_names)
    assert not offenders, (
        "an option is named by a section that no longer exists; name it by its "
        "module (params.<module>.<option>):\n" + "\n".join(offenders)
    )


# What a node object may set on itself while running: the algorithm it builds
# once from its config, and xlma_stat's flash files (inputs read once, not
# results). Anything a module carries from scan to scan is a key it both reads
# and writes — the runtime hands it back and checkpoints it — never an attribute.
_NODE_ATTRIBUTES = {"_analyzer", "_flashes", "_folder", "_loader", "_projector", "_segmenter"}


def _attributes_set_while_running(py_file: Path) -> set[str]:
    """Names of ``self`` attributes assigned outside ``__init__`` in ``py_file``."""
    found: set[str] = set()
    for cls in ast.walk(ast.parse(py_file.read_text(encoding="utf-8"))):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef) or method.name == "__init__":
                continue
            for node in ast.walk(method):
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                    targets = [node.target]
                else:
                    continue
                for target in targets:
                    while isinstance(target, ast.Subscript):
                        target = target.value
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        found.add(target.attr)
    return found


def _node_files() -> list[Path]:
    """``modules/<name>/node.py``: the file holding the class the pipeline runs."""
    return sorted((_SRC_ADAPT / "modules").glob("*/node.py"))


def test_a_module_is_one_package_named_by_one_catalog_line() -> None:
    """A module's class lives in ``modules/<name>/node.py``, ``<name>`` being the
    name a pipeline file lists, and one line of the catalog names it."""
    from adapt.execution.module_registry import registry
    from adapt.execution.pipeline_builder import catalog_module_names

    homes = [registry.get(name).__module__ for name in catalog_module_names()]

    assert homes == [f"adapt.modules.{name}.node" for name in catalog_module_names()]
    assert sorted(homes) == [f"adapt.modules.{f.parent.name}.node" for f in _node_files()]


def test_the_execution_layer_holds_no_module() -> None:
    """``execution/`` wires modules into a graph; it defines none."""
    defined = [
        f"{_rel(py_file, _SRC_ADAPT)}: class {node.name}"
        for py_file in sorted((_SRC_ADAPT / "execution").rglob("*.py"))
        for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "BaseModule" for base in node.bases)
    ]

    assert not defined, f"a module class belongs in modules/<name>/node.py: {defined}"
    assert not (_SRC_ADAPT / "execution" / "nodes").exists()


def test_a_module_node_carries_nothing_between_scans_in_its_attributes() -> None:
    """A run resumed from its checkpoint must go on as if it had not stopped,
    which holds only while every module's state is in the keys the runtime carries."""
    offenders = {
        _rel(py_file, _SRC_ADAPT): sorted(extra)
        for py_file in _node_files()
        if (extra := _attributes_set_while_running(py_file) - _NODE_ATTRIBUTES)
    }
    assert not offenders, (
        "a node sets attributes while running; return carried state under a key the "
        f"module also reads (see BaseModule), or extend _NODE_ATTRIBUTES: {offenders}"
    )


# ── A module's defaults are complete ──────────────────────────────────────────
# ``adapt config --params`` and the option resolver take a module's defaults from
# its packaged preset, else from its options model built with no argument. A
# part of the options given alone is filled from the part's own defaults.


def _options_without_default(model: type, prefix: str = "") -> list[str]:
    """Dotted names of the options of ``model``, nested parts included, with no default."""
    from typing import get_args

    from pydantic import BaseModel

    missing = []
    for name, field in model.model_fields.items():
        if field.is_required():
            missing.append(f"{prefix}{name}")
        for part in (field.annotation, *get_args(field.annotation)):
            if isinstance(part, type) and issubclass(part, BaseModel):
                missing.extend(_options_without_default(part, f"{prefix}{name}."))
    return missing


def test_a_module_without_a_preset_has_a_default_for_every_option() -> None:
    from adapt.execution.module_registry import registry
    from adapt.execution.pipeline_builder import catalog_module_names

    offenders = {
        name: missing
        for name in catalog_module_names()
        if (cls := registry.get(name)).options_preset() is None
        and cls.options_class is not None
        and (missing := _options_without_default(cls.options_class))
    }
    assert not offenders, (
        "options without a default: give each one, or package a preset "
        f"(BaseModule.options_preset): {offenders}"
    )


# ── configuration code names no module ───────────────────────────────────────
# Which modules exist is the catalog's (defaults.yaml); which of them a new
# pipeline lists is a template's (configuration/templates/*.yaml). Both are
# data. A module's name written as a string of its own in configuration's
# Python means the code knows that module again.


def test_configuration_code_names_no_module() -> None:
    from adapt.execution.pipeline_builder import catalog_module_names

    names = set(catalog_module_names())
    offenders = [
        f"{_rel(py_file, _SRC_ADAPT)}:{node.lineno}: {node.value!r}"
        for py_file in sorted((_SRC_ADAPT / "configuration").rglob("*.py"))
        for node in ast.walk(ast.parse(py_file.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names
    ]
    assert not offenders, (
        "configuration code names a module; a template's module list belongs in "
        "configuration/templates/<name>.yaml:\n" + "\n".join(offenders)
    )
