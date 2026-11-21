# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline templates: the short, commented pipeline file a new pipeline starts from.

A template is a pipeline file packaged with Adapt, ``templates/<name>.yaml``; the
file's name is the template's name. It is read and checked as any pipeline file
(``load_pipeline_file``): its ``run:`` gives the run values, its ``modules:`` the
modules that are on. The module list written is the module catalog
(``defaults.yaml``), in its order. Only names are written, each with a one-line
comment: the template's modules on, the others offered commented out, and an
empty ``params:`` last (every option at its default), ready for what
``adapt config --params`` prints or adds. The file names no pipeline: a copy is
right as is.
"""

from pathlib import Path
from typing import Any

from adapt.configuration.pipeline_file import RunSection, load_pipeline_file
from adapt.configuration.schemas.errors import ConfigError, validated
from adapt.configuration.schemas.yaml_writer import scalar
from adapt.execution.module_registry import registry
from adapt.execution.pipeline_builder import catalog_module_names
from adapt.execution.source_registry import catalog_source_names

__all__ = [
    "pipeline_template",
    "pipeline_template_names",
    "write_pipeline_template",
]

_TEMPLATES = Path(__file__).parent / "templates"

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


def pipeline_template_names() -> tuple[str, ...]:
    """The names of the packaged templates, sorted. Reads file names only.

    Raises
    ------
    RuntimeError
        The templates folder holds no template (an installation without its
        data files).
    """
    names = tuple(sorted(path.stem for path in _TEMPLATES.glob("*.yaml")))
    if not names:
        raise RuntimeError(f"No pipeline template (NAME.yaml) in '{_TEMPLATES}'")
    return names


def pipeline_template(template: str, **run: Any) -> str:
    """The pipeline file of ``template``.

    ``run`` holds ``run:`` section values (e.g. ``radar="KTST",
    mode="historical"``) that replace the template's; the others keep the
    template's values.

    Raises
    ------
    ConfigError
        ``template`` is not a packaged template, its file is not a valid
        pipeline file or sets params, or ``run`` is not a valid run section.
    """
    names = pipeline_template_names()
    if template not in names:
        raise ConfigError(f"Unknown pipeline template '{template}'. Available: {', '.join(names)}")
    path = _TEMPLATES / f"{template}.yaml"
    base = load_pipeline_file(path)
    if base.params:
        raise ConfigError(
            f"Pipeline template {path} sets params ({', '.join(base.params)}); a template "
            "gives run: and modules: only, and the file written from it has an empty params:"
        )
    section = validated(
        RunSection,
        {**base.run.model_dump(), **run},
        source=f"run values for the pipeline template '{template}'",
    )
    return _render(section, base.modules)


def _line(text: str, comment: str | None = None) -> str:
    if not comment:
        return text
    return f"{text.ljust(max(_COMMENT_COLUMN, len(text) + 2))}# {comment}"


def _render(run: RunSection, modules: tuple[str, ...]) -> str:
    lines = [_HEADER, "", "run:"]
    for key, value in run.model_dump(mode="json").items():
        comment = RunSection.model_fields[key].description
        if key == "source":
            comment = f"{comment}: {' | '.join(catalog_source_names())}"
        lines.append(_line(f"  {key}: {scalar(value)}", comment))
    lines += ["", _line("modules:", _MODULES_COMMENT)]
    for module in catalog_module_names():
        mark = "  - " if module in modules else "  # - "
        lines.append(_line(f"{mark}{module}", registry.get(module).module_summary()))
    lines += ["", "params:"]
    return "\n".join(lines) + "\n"
