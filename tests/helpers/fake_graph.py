"""Stand-ins for a RadarProcessor's per-scan graph, for tests of what surrounds it."""

from adapt.execution.graph.executor import GraphRun

# The modules that need a prior scan, directly or through their inputs.
NEEDS_PRIOR_SCAN = ("projection", "analysis", "tracking")


def fake_graph(proc, monkeypatch, every_scan, with_prior=lambda context: {}) -> None:
    """Replace ``proc``'s pass over the graph with canned outputs.

    ``every_scan(context)`` stands in for the modules that run on every scan,
    ``with_prior(context)`` for those that run once a prior scan is usable. Each
    returns the outputs to merge into the context, as modules do. The processor
    is handed every module that "ran", so it stores their declared outputs.
    """

    def run(context, *, history, before_node=None, after_node=None):
        merged = {**context, **every_scan(context)}
        skipped = {}
        if history < 1:
            skipped = dict.fromkeys(NEEDS_PRIOR_SCAN, "needs 1 prior scan(s), 0 usable")
        else:
            merged.update(with_prior(merged))
        if after_node is not None:
            for node in proc._executor.nodes:
                if node.name not in skipped:
                    after_node(node, merged)
        return GraphRun(context=merged, skipped=skipped, failed={})

    monkeypatch.setattr(proc._executor, "run", run)
