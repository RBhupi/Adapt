# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Base Pydantic model with strict defaults for Adapt configs.

The pipeline file's models and the run config inherit from this base, so both
validate the same way.
"""

from pydantic import BaseModel, ConfigDict


class AdaptBaseModel(BaseModel):
    """Base model for all Adapt configuration schemas.

    Enforces strict validation:
    - No extra fields allowed
    - Validates assignments after initialization
    - Uses Python mode (not JSON mode)
    - Forbids mutations after construction (frozen for internal config)
    """

    model_config = ConfigDict(
        extra="forbid",  # Reject unknown fields
        validate_assignment=True,  # Validate on field mutation
        use_enum_values=True,  # Convert enums to values
        str_strip_whitespace=True,  # Strip whitespace from strings
    )


class NoOptions(BaseModel):
    """The options of a module or source that declares none: every key is unknown."""

    model_config = ConfigDict(extra="forbid")
