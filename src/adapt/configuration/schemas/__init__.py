# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Configuration schemas for the Adapt pipeline.

``InternalConfig`` (``internal``) is the frozen run config runtime code sees;
module configs are built from it by ``module_resolver.resolve_module_configs``.
A wrong value is a ``ConfigError`` (``errors``). A pipeline file is loaded by
``adapt.configuration.pipeline_file`` and turned into a run's config by
``adapt.configuration.pipeline_run``.
"""
