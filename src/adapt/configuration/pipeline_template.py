# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline templates: the short, commented pipeline file a new pipeline starts from.

``PIPELINE_TEMPLATES`` is the registry (name -> template). A template fills in
the ``run:`` section and says which modules are on; the module list is the
module catalog (``defaults.yaml``), in its order. Only names are written, each
with a one-line comment: the template's modules on, the others offered
commented out, and an empty ``params:`` last (every option at its default),
ready for what ``adapt config --params`` prints or adds. The file names no pipeline: a
copy is right as is.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from adapt.configuration.pipeline_file import RunSection
from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.yaml_writer import scalar
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names

__all__ = [
    "PIPELINE_TEMPLATES",
    "PipelineTemplate",
    "pipeline_template",
    "write_pipeline_template",
]


@dataclass(frozen=True)
class PipelineTemplate:
    """What a template fills in: the run section, and the modules that are on."""

    run: RunSection
    modules: tuple[str, ...]  # listed; every other catalog module is offered commented out


PIPELINE_TEMPLATES: Mapping[str, PipelineTemplate] = MappingProxyType(
    {
        "nexrad": PipelineTemplate(
            run=RunSection(source="aws_nexrad", radar="KLOT", mode="realtime"),
            modules=(
                "ingest",
                "detection",
                "projection",
                "analysis",
                "tracking",
                "cell_volume_stats",
            ),
        ),
    }
)

_HEADER = """\
# Adapt pipeline file. Run it with:  adapt run <this file's name>
# Turn a module on by uncommenting its line; Adapt orders the modules by what each reads.
# Everything not set here keeps its default. To see a module's options:
#   adapt config --params NAME
# and give only the ones you change, under params:. To add every option a listed
# module lacks, with its default:  adapt config --params --file <this file's name>
# To see how the listed modules feed each other:  adapt graph <this file's name>"""

_MODULES_COMMENT = "per-scan modules; Adapt orders them by their inputs and outputs"

_COMMENT_COLUMN = 30


def write_pipeline_template(template: str, path: Path, **run: Any) -> None:
    """Write ``pipeline_template(template, **run)`` to ``path`` (overwriting it)."""
    path.write_text(pipeline_template(template, **run), encoding="utf-8")


def pipeline_template(template: str, **run: Any) -> str:
    """The pipeline file of ``template``.

    ``run`` holds ``run:`` section values (e.g. ``radar="KTST",
    mode="historical"``) that replace the template's; the others keep the
    template's values.

    Raises
    ------
    ConfigError
        ``template`` is not a registered template, or ``run`` is not a valid
        run section.
    """
    if template not in PIPELINE_TEMPLATES:
        raise ConfigError(
            f"Unknown pipeline template '{template}'. Available: {', '.join(PIPELINE_TEMPLATES)}"
        )
    base = PIPELINE_TEMPLATES[template]
    section = validated(
        RunSection,
        {**base.run.model_dump(), **run},
        source=f"run values for the pipeline template '{template}'",
    )
    return _render(replace(base, run=section))


def _line(text: str, comment: str | None = None) -> str:
    if not comment:
        return text
    return f"{text.ljust(max(_COMMENT_COLUMN, len(text) + 2))}# {comment}"


def _render(template: PipelineTemplate) -> str:
    lines = [_HEADER, "", "run:"]
    for key, value in template.run.model_dump(mode="json").items():
        lines.append(_line(f"  {key}: {scalar(value)}", RunSection.model_fields[key].description))
    lines += ["", _line("modules:", _MODULES_COMMENT)]
    for module in catalog_module_names():
        mark = "  - " if module in template.modules else "  # - "
        lines.append(_line(f"{mark}{module}", registry.get(module).module_summary()))
    lines += ["", "params:"]
    return "\n".join(lines) + "\n"
