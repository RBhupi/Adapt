# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Builds an execution DAG from a list of modules.

``data_edges`` matches each module's declared ``inputs`` to the module that
declares the same key in its ``outputs``; it is the one description of the
graph's wiring. ``GraphBuilder`` turns those edges into connected nodes and
refuses any input that has no source.
"""

from typing import TYPE_CHECKING

from adapt.execution.graph.node import Node

if TYPE_CHECKING:
    from adapt.modules.base import BaseModule


def data_edges(modules: list["BaseModule"]) -> list[tuple[str, str, str]]:
    """``(producer, consumer, key)`` for every input matched to a module's output.

    Ordered by consumer (list order), then by that consumer's input order.

    Raises
    ------
    ValueError
        If two modules declare the same output key (ambiguous dependency).
    """
    producers: dict[str, str] = {}
    for module in modules:
        for key in module.outputs:
            if key in producers:
                raise ValueError(
                    f"Output '{key}' declared by both "
                    f"'{producers[key]}' and '{module.name}'. "
                    "Each output key must be unique."
                )
            producers[key] = module.name
    return [
        (producers[key], module.name, key)
        for module in modules
        for key in module.inputs
        if key in producers
    ]


class GraphBuilder:
    """Construct a directed acyclic graph from module declarations.

    Parameters
    ----------
    modules : list[BaseModule]
        Ordered or unordered list of modules to connect. Execution order
        is determined by input/output matching, not list order.
    provided : frozenset[str]
        Context keys the caller seeds before the graph runs. Every module
        input must be one of these or another module's output.

    Example::

        builder = GraphBuilder([ingest_mod, detection_mod], provided=frozenset({"nexrad_file"}))
        nodes = builder.build()
    """

    def __init__(self, modules: list["BaseModule"], provided: frozenset[str]) -> None:
        self.modules = modules
        self.provided = provided

    def build(self) -> list[Node]:
        """Build and return the list of connected nodes.

        Returns
        -------
        list[Node]
            All nodes with ``dependencies`` and ``dependents`` populated.
            Nodes are returned in insertion order; execution order is
            determined by the GraphExecutor.

        Raises
        ------
        ValueError
            If two modules declare the same output key, or a module declares
            an input that no module produces and the caller does not provide.
        """
        edges = data_edges(self.modules)

        produced = {key for module in self.modules for key in module.outputs}
        unbound = [
            f"Module '{module.name}' needs input '{key}'"
            for module in self.modules
            for key in module.inputs
            if key not in produced and key not in self.provided
        ]
        if unbound:
            raise ValueError(
                f"{'; '.join(unbound)}: no enabled module produces it and the runtime "
                f"does not provide it. The runtime provides: {', '.join(sorted(self.provided))}"
            )

        nodes: dict[str, Node] = {m.name: Node(m) for m in self.modules}
        for producer, consumer, _key in edges:
            parent, node = nodes[producer], nodes[consumer]
            if parent not in node.dependencies:
                node.dependencies.append(parent)
                parent.dependents.append(node)
        return list(nodes.values())
