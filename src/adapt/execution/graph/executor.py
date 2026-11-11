# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Executes a DAG of nodes in dependency order.

The executor runs a simple topological loop: a node is settled once all its
dependencies are settled. This single-threaded executor is the reference
implementation.

Skipping
--------
A node is skipped, not run, when it needs more prior scans than are usable
(``required_history - 1 > history``) or when any of its dependencies was
skipped. A dependency that ran without returning a key the node consumes is a
bug and raises: it never looks like a skip.

Contract enforcement
--------------------
If a module declares ``input_contracts`` or ``output_contracts``, the
executor calls each validator automatically before and after ``run()``.
Validators raise ``ContractViolation`` on failure; the exception propagates
up to the caller (e.g. ``RadarProcessor``) which handles it.
"""

import logging
from contextlib import nullcontext
from dataclasses import dataclass

from adapt.contracts.observability import Observability
from adapt.contracts.pipeline import require
from adapt.execution.graph.node import Node

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphRun:
    """One pass over a graph: the final context and the nodes that did not run."""

    context: dict
    skipped: frozenset[str]


class GraphExecutor:
    """Execute a list of nodes in topological order.

    Parameters
    ----------
    nodes : list[Node]
        Connected nodes returned by ``GraphBuilder.build()``, or a subset of
        them: a dependency outside ``nodes`` counts as done unless the caller
        names it in ``run(skipped=...)``.

    Example::

        nodes = GraphBuilder([a, b, c], provided=frozenset()).build()
        executor = GraphExecutor(nodes)
        result_context = executor.run({}, history=0).context
    """

    def __init__(self, nodes: list[Node], observability: Observability | None = None) -> None:
        self.nodes = nodes
        # Optional, injected by the runtime composition root. Absent -> no telemetry
        # (the documented default for this optional subsystem); the node still runs.
        self._obs = observability

    def run(
        self, context: dict, *, history: int, skipped: frozenset[str] = frozenset()
    ) -> GraphRun:
        """Execute the nodes in dependency order, skipping those that cannot run.

        Parameters
        ----------
        context : dict
            Initial data available to the first nodes (e.g. file paths,
            config). Each node's outputs are merged into a copy of this dict
            after the node runs, making them available to downstream nodes.
        history : int
            Number of usable prior scans.
        skipped : frozenset[str]
            Nodes of an earlier pass over the same graph that did not run.

        Returns
        -------
        GraphRun
            The final context with all module outputs, and every skipped node
            (those passed in and this pass's own).

        Raises
        ------
        RuntimeError
            If the graph contains a cycle (nodes that can never be settled).
        ContractViolation
            If a contract fails, or a node that ran did not return a key a
            later node consumes.
        """
        context = dict(context)  # shallow copy — don't mutate caller's dict
        own = {node.name for node in self.nodes}
        ran: set[str] = set()
        not_run: set[str] = set(skipped)
        pending = list(self.nodes)

        while pending:
            progress_made = False
            for node in list(pending):
                # A node is ready once every dependency is settled (ran or skipped).
                if any(
                    dep.name in own and dep.name not in ran and dep.name not in not_run
                    for dep in node.dependencies
                ):
                    continue
                pending.remove(node)
                progress_made = True
                if node.module.required_history - 1 > history or any(
                    dep.name in not_run for dep in node.dependencies
                ):
                    not_run.add(node.name)
                    logger.debug("Skipped node: %s", node.name)
                    continue
                self._run_node(node, context)
                ran.add(node.name)
                logger.debug("Completed node: %s", node.name)

            if not progress_made:
                raise RuntimeError(
                    "No progress made — possible cycle or missing dependency. "
                    f"Pending: {[node.name for node in pending]}"
                )

        return GraphRun(context=context, skipped=frozenset(not_run))

    def _run_node(self, node: Node, context: dict) -> None:
        """Validate, run and merge one node; its dependencies all ran."""
        for dep in node.dependencies:
            for key in node.inputs:
                if key in dep.outputs:
                    require(
                        key in context,
                        f"Module '{dep.name}' ran but did not return '{key}', "
                        f"which module '{node.name}' needs",
                    )

        # Validate inputs declared by the module — fail immediately if absent
        for key, validator in (node.module.input_contracts or {}).items():
            require(
                key in context,
                f"Required input '{key}' missing for module '{node.name}'",
            )
            validator(context[key])

        # One span per node — the single auto-instrumentation seam. The span's
        # exit captures any propagating exception and records errors_total.
        span_cm = self._obs.span(node.name) if self._obs is not None else nullcontext()
        with span_cm:
            try:
                outputs = node.module.run(context)
            except Exception as exc:
                # Name the failing stage on the exception so the scan-level
                # handler reports *which* stage broke. The span still records
                # the error; we re-raise unchanged (no swallowing, no wrapping).
                exc.add_note(f"adapt: failing stage = {node.name}")
                raise

        # Validate outputs declared by the module
        if outputs:
            for key, validator in (node.module.output_contracts or {}).items():
                if key in outputs:
                    validator(outputs[key])
            context.update(outputs)
