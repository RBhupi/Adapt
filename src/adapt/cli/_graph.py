# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""``adapt graph NAME``: the module graph of pipeline file ``./NAME.yaml``, as Mermaid text.

The graph is the one a new run of the file would build: each module reads what
the file's options make it read, and a file a run would refuse (an unknown
module, a wrong option, an input with no source) is refused here with the same
message. Nothing runs and no store is read: the current folder only has to
hold the file, named with or without its extension (``adapt graph nexrad.yaml``).
"""

from pathlib import Path

from adapt.cli._run import pipeline_path
from adapt.configuration.pipeline_file import PipelineFile, load_pipeline_file
from adapt.configuration.pipeline_run import configured_modules
from adapt.contracts import SCAN_SEEDS, STORE_VIEWS
from adapt.execution.graph.builder import data_edges
from adapt.execution.pipeline_builder import catalog_module_names, stored_output_keys

__all__ = ["graph"]

# Graph nodes for what the runtime provides: the scan itself, the scans before
# it, and what is read from the store.
_PRIOR_SCANS = "prior_scans"
_SCAN = "scan"
_STORE = "store"

Edge = tuple[str, str, str]  # (source node, target node, data key)


def graph(name: str) -> str:
    """Mermaid flowchart of ``./NAME.yaml``: its modules and the data between them.

    Raises
    ------
    ConfigError
        The file is missing or invalid, or a run of it would be refused.
    """
    path = pipeline_path(Path.cwd(), name, "graph")
    pipeline = load_pipeline_file(path)
    catalog_module_names()  # registers every catalog module
    wired = {module.name: module for module in configured_modules(pipeline, path)}
    modules = [wired[name] for name in pipeline.modules]
    return "\n".join(_lines(pipeline, modules, pipeline.run.source == "stored_run")) + "\n"


def _lines(pipeline: PipelineFile, modules: list, stored_run: bool) -> list[str]:
    provided = _provided_edges(modules, stored_run)
    sources = {source for source, _, _ in provided}
    lines = ["flowchart TB"]
    if _SCAN in sources:
        lines.append(f'  {_SCAN}(["{", ".join(sorted(SCAN_SEEDS - {_PRIOR_SCANS}))}"])')
    if _PRIOR_SCANS in sources:
        lines.append(f'  {_PRIOR_SCANS}(["{_PRIOR_SCANS}"])')
    if _STORE in sources:
        lines.append(f'  {_STORE}[("{_STORE}")]')
    lines += ["  subgraph modules", *(f"    {name}" for name in pipeline.modules), "  end"]
    return lines + _edge_lines([*provided, *data_edges(modules)])


def _provided_edges(modules: list, stored_run: bool) -> list[Edge]:
    """Edges from what the runtime provides to the modules that read it."""
    produced = {key for module in modules for key in module.outputs}
    from_store = STORE_VIEWS | (stored_output_keys() - produced if stored_run else frozenset())
    edges: list[Edge] = []
    for module in modules:
        for key in module.inputs:
            if key == _PRIOR_SCANS:
                edges.append((_PRIOR_SCANS, module.name, key))
            elif key in SCAN_SEEDS:
                edges.append((_SCAN, module.name, key))
            elif key in from_store:
                edges.append((_STORE, module.name, key))
    return edges


def _edge_lines(edges: list[Edge]) -> list[str]:
    """One line per (source, target) pair, labelled with every key it carries."""
    keys: dict[tuple[str, str], list[str]] = {}
    for source, target, key in edges:
        keys.setdefault((source, target), []).append(key)
    return [
        f"  {source} --> {target}"
        if carried == [source]  # the node is the key: a label would repeat it
        else f"  {source} -->|{', '.join(carried)}| {target}"
        for (source, target), carried in keys.items()
    ]
