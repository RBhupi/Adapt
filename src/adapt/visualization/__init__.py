# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Optional visualization package — never imported by core.

``PlotConsumer`` draws a run's scans from the store. It is a store client like
the dashboard, not part of the pipeline: ``adapt run`` no longer launches it,
and it has no launcher of its own yet.
"""
