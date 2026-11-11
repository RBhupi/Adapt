# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Configuration resolution and merging logic.

This module provides the SINGLE AUTHORITATIVE ENTRYPOINT for configuration
resolution: resolve_config(). It merges UserConfig overrides over ParamConfig
defaults and returns a validated InternalConfig. The pipeline-file loader
(``adapt.configuration.pipeline_file``) translates a pipeline file into a
UserConfig and resolves it here.

Precedence (highest to lowest):
1. UserConfig (overrides)
2. ParamConfig (expert defaults)

Merge Semantics:
- Nested dictionaries: recursively merged (child keys from higher-priority
  configs override lower-priority configs)
- Lists: completely REPLACED (higher-priority list replaces entire
  lower-priority list; no concatenation)
- Other values: replaced

Special Cases:
- analyzer.exclude_fields: UNIONED (default excludes + user excludes)
- mode inference: auto-set to 'historical' if start_time/end_time provided
  but no explicit mode

Example precedence:
- ParamConfig: {"radar_variables": ["A", "B"], "threshold": 30}
- UserConfig: {"radar_variables": ["C"], "threshold": 40}
- Result: {"radar_variables": ["C"], "threshold": 40}  # List replaced
"""

from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.internal import InternalConfig
from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.user import UserConfig


def deep_merge(base: dict, *overrides: dict) -> dict:
    """Deep merge multiple dictionaries.

    Later dictionaries override earlier ones. Nested dictionaries are
    merged recursively; other values are replaced.

    Parameters
    ----------
    base : dict
        Base dictionary (lowest priority)
    *overrides : dict
        Override dictionaries (higher priority, left to right)

    Returns
    -------
    dict
        Merged dictionary

    Examples
    --------
    >>> base = {"a": 1, "b": {"c": 2, "d": 3}}
    >>> override = {"b": {"d": 4, "e": 5}, "f": 6}
    >>> deep_merge(base, override)
    {'a': 1, 'b': {'c': 2, 'd': 4, 'e': 5}, 'f': 6}
    """
    result = base.copy()

    for override in overrides:
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Recursive merge for nested dicts
                result[key] = deep_merge(result[key], value)
            else:
                # Replace value
                result[key] = value

    return result


def resolve_config(
    param_cfg: dict | ParamConfig,
    user_cfg: dict | UserConfig | None = None,
) -> InternalConfig:
    """Resolve final runtime configuration from param and user configs.

    This is the SINGLE ENTRYPOINT for configuration resolution. It validates
    and merges configs in the correct precedence order, then returns an
    immutable InternalConfig for runtime use.

    Precedence (highest to lowest):
    1. UserConfig (overrides)
    2. ParamConfig (expert defaults)

    Parameters
    ----------
    param_cfg : dict or ParamConfig
        Expert configuration with complete defaults. Required.
    user_cfg : dict or UserConfig, optional
        User configuration with overrides. If None or empty, uses only param defaults.

    Returns
    -------
    InternalConfig
        Fully validated, immutable runtime configuration

    Raises
    ------
    ConfigError
        If any config fails validation — with a clear message naming the
        offending section/field, its value, and why.

    Examples
    --------
    >>> from adapt.configuration.schemas import resolve_config, ParamConfig, UserConfig
    >>>
    >>> # Load expert defaults
    >>> param = ParamConfig()
    >>>
    >>> # User wants higher threshold
    >>> user = UserConfig(threshold=35, radar="KHTX")
    >>>
    >>> # Resolve to internal config
    >>> config = resolve_config(param, user)
    >>> config.segmenter.threshold
    35.0
    >>> config.downloader.radar
    'KHTX'
    """
    # Validate/convert inputs to Pydantic models
    if not isinstance(param_cfg, ParamConfig):
        param = validated(ParamConfig, param_cfg, source="expert defaults")
    else:
        param = param_cfg

    if user_cfg is None or (isinstance(user_cfg, dict) and not user_cfg):
        user = UserConfig()  # type: ignore[call-arg]
    elif not isinstance(user_cfg, UserConfig):
        user = validated(UserConfig, user_cfg, source="config file")
    else:
        user = user_cfg

    # Convert to dicts for merging
    param_dict = param.model_dump(by_alias=True)  # Use 'global' not 'global_'
    user_overrides = user.to_internal_overrides()

    # Deep merge: param < user
    merged = deep_merge(param_dict, user_overrides)

    # Special handling for analyzer.exclude_fields: AMEND instead of REPLACE
    # We want to keep the internal defaults and add user-specified ones: defaults
    # first, then each addition once, in the order given (a set's order would
    # change from run to run).
    user_excludes = list(user.exclude_fields or [])  # top-level UserConfig alias
    if user.analyzer and user.analyzer.exclude_fields:  # nested UserConfig section
        user_excludes += user.analyzer.exclude_fields
    if user_excludes:
        merged.setdefault("analyzer", {})["exclude_fields"] = list(
            dict.fromkeys([*param.analyzer.exclude_fields, *user_excludes])
        )

    # Schema responsibility: Infer historical mode from times if mode not explicitly set
    # This handles cases where times are provided but mode is not.
    # We check user.mode directly to see if it was explicitly set.
    if user.mode is None:
        downloader_dict = merged.get("downloader", {})
        if downloader_dict.get("start_time") or downloader_dict.get("end_time"):
            merged["mode"] = "historical"

    # Add mode to downloader config for validation
    if "downloader" in merged and "mode" not in merged["downloader"]:
        merged["downloader"]["mode"] = merged.get("mode", "realtime")

    # Validate and freeze as InternalConfig
    internal = validated(InternalConfig, merged, source="merged runtime configuration")

    # Conditional validation: Historical mode requires start_time and end_time
    # (a pass over a stored run takes its scans from that run, not from a window).
    if internal.downloader.mode == "historical" and internal.source != "stored_run":
        if not internal.downloader.start_time:
            raise ConfigError(
                "start_time is required when mode='historical'. "
                "Set run.start_time in the pipeline file."
            )
        if not internal.downloader.end_time:
            raise ConfigError(
                "end_time is required when mode='historical'. "
                "Set run.end_time in the pipeline file."
            )

    # The tracking field must actually exist for the core to run on it:
    # the analyzer mints the per-cell stats the tracker reads, and the
    # reader.fields selection decides which variables survive ingest.
    # Failing here beats a KeyError three modules into the first scan.
    field = internal.global_.tracking_field
    if field not in internal.analyzer.radar_variables:
        raise ConfigError(
            f"global.tracking_field {field!r} is not in analyzer.radar_variables "
            f"{internal.analyzer.radar_variables} — the analyzer would never mint "
            "the per-cell statistics the tracker needs. Add it to the whitelist."
        )
    if internal.reader.fields and field not in internal.reader.fields:
        raise ConfigError(
            f"global.tracking_field {field!r} is excluded by reader.fields "
            f"{internal.reader.fields} — the tracked field would never survive "
            "ingest. Add it to reader.fields or clear the list."
        )

    return internal
