# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Target selection for store clients: replaying its rules over a stored run.

The rules themselves live in ``adapt.modules.target_selection`` and run inside
the pipeline. Here: ``build_snapshot`` (a stored run's scan as the engine sees
it, read through ``adapt.api.StoreClient``) and the replay study that compares
rule variants against an oracle.
"""

from adapt.consumers.target_selection.repository_source import build_snapshot

__all__ = ["build_snapshot"]
