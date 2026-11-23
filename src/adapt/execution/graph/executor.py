# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Executes a DAG of nodes in dependency order.

The executor runs a simple topological loop: a node is settled once all its
dependencies are settled. This single-threaded executor is the reference
implementation.

Skipping and failing
--------------------
A node is skipped, not run, when it needs more prior scans than are usable
(``required_history - 1 > history``) or when any of its dependencies was
skipped or failed. A node that raises has failed: the pass goes on with the
nodes that do not depend on it, and the caller is told which failed and why.
A node that ran without returning every output it declares is a bug and
raises, naming the node and the outputs: it never looks like a skip.

Contract enforcement
--------------------
If a module declares ``input_contracts`` or ``output_contracts``, the
executor calls each validator automatically before and after ``run()``.
Validators raise ``ContractViolation`` on failure; it is never a node failure
and always propagates to the caller (e.g. ``RadarProcessor``), ending the pass.
"""

import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass

from adapt.contracts.observability import Observability
from adapt.contracts.pipeline import ContractViolation, require
from adapt.execution.graph.node import Node

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphRun:
    """One pass over a graph: the final context and what became of each node
    that did not run to the end (name -> why it was skipped / what it raised)."""

    context: dict
    skipped: dict[str, str]
    failed: dict[str, Exception]


class GraphExecutor:
    """Execute a list of nodes in topological order.

    Parameters
    ----------
    nodes : list[Node]
        Connected nodes returned by ``GraphBuilder.build()``.

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
        self,
        context: dict,
        *,
        history: int,
        before_node: Callable[[Node, dict], str | None] | None = None,
        after_node: Callable[[Node, dict], None] | None = None,
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
        before_node : callable, optional
            Called with ``(node, context)`` when a node is about to run; it may
            add inputs to the context (the processor reads them from the store
            here), or return why the node cannot run, which skips it.
        after_node : callable, optional
            Called with ``(node, context)`` once a node has run and its outputs
            are in the context (the processor stores them here). If it raises,
            the node has failed.

        Returns
        -------
        GraphRun
            The final context with all module outputs, the skipped nodes and
            the failed ones.

        Raises
        ------
        RuntimeError
            If the graph contains a cycle (nodes that can never be settled).
        ContractViolation
            If a contract fails, or a node that ran did not return a key a
            later node consumes.
        """
        context = dict(context)  # shallow copy — don't mutate caller's dict
        ran: set[str] = set()
        skipped: dict[str, str] = {}
        failed: dict[str, Exception] = {}
        pending = list(self.nodes)

        while pending:
            progress_made = False
            for node in list(pending):
                # A node is ready once every dependency is settled.
                if any(
                    dep.name not in ran and dep.name not in skipped and dep.name not in failed
                    for dep in node.dependencies
                ):
                    continue
                pending.remove(node)
                progress_made = True
                reason = self._skip_reason(node, history, skipped, failed)
                if reason is not None:
                    skipped[node.name] = reason
                    logger.debug("Skipped node %s: %s", node.name, reason)
                    continue
                try:
                    if before_node is not None:
                        reason = before_node(node, context)
                    if reason is not None:
                        skipped[node.name] = reason
                        logger.debug("Skipped node %s: %s", node.name, reason)
                        continue
                    self._run_node(node, context)
                    if after_node is not None:
                        after_node(node, context)
                except ContractViolation:
                    raise
                except Exception as exc:
                    failed[node.name] = exc
                    continue
                ran.add(node.name)
                logger.debug("Completed node: %s", node.name)

            if not progress_made:
                raise RuntimeError(
                    "No progress made — possible cycle or missing dependency. "
                    f"Pending: {[node.name for node in pending]}"
                )

        return GraphRun(context=context, skipped=skipped, failed=failed)

    @staticmethod
    def _skip_reason(
        node: Node, history: int, skipped: dict[str, str], failed: dict[str, Exception]
    ) -> str | None:
        """Why ``node`` cannot run in this pass; None when it can."""
        needed = node.module.required_history - 1
        if needed > history:
            return f"needs {needed} prior scan(s), {history} usable"
        for dep in node.dependencies:
            if dep.name in failed:
                return f"needs '{dep.name}', which failed"
            if dep.name in skipped:
                return f"needs '{dep.name}', which was skipped"
        return None

    def _run_node(self, node: Node, context: dict) -> None:
        """Validate, run and merge one node; its dependencies all ran."""
        # Validate inputs declared by the module — fail immediately if absent
        for key, validator in (node.module.input_contracts or {}).items():
            require(
                key in context,
                f"Required input '{key}' missing for module '{node.name}'",
            )
            validator(context[key])

        # One span per node — the single auto-instrumentation seam. The span's
        # exit captures any exception the module raises and records errors_total.
        span_cm = self._obs.span(node.name) if self._obs is not None else nullcontext()
        with span_cm:
            outputs = node.module.run(context) or {}

        missing = [key for key in node.outputs if key not in outputs]
        require(
            not missing,
            f"Module '{node.name}' ran but did not return its declared output(s) "
            f"{', '.join(missing)}",
        )
        # Validate outputs declared by the module
        for key, validator in (node.module.output_contracts or {}).items():
            if key in outputs:
                validator(outputs[key])
        context.update(outputs)
