# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Adapt command-line interface.

Entry point: ``adapt``. The project folder is the store, and each pipeline is a
file ``NAME.yaml`` in it::

    mkdir case && cd case
    adapt init                  # the store + one pipeline file per template (nexrad.yaml)
    adapt config --nexrad       # write ./nexrad.yaml from the template (never over a file)
    adapt config --params NAME...              # NAMEs' options with their defaults, to paste
    adapt config --params --file nexrad.yaml   # add the options its modules lack
    adapt run nexrad            # run ./nexrad.yaml: its modules, scan by scan
    adapt run nexrad.yaml       # the same: the file may be named with its extension
    adapt graph nexrad          # print ./nexrad.yaml's module graph (Mermaid text)
    adapt dashboard             # browse the store

``cli`` is the only layer that may import both the runtime and the consumers
(the dashboard), so every command starts here.
"""

import argparse
import os
import sys
from pathlib import Path

import adapt
from adapt import __version__
from adapt.configuration.schemas.errors import ConfigError
from adapt.persistence.errors import StoreError

__all__ = ["main"]


# ---------------------------------------------------------------------------
# adapt init
# ---------------------------------------------------------------------------


def _init_cmd(args: argparse.Namespace) -> None:
    """Create the store, then one pipeline file per template (existing files are kept).

    The store comes first: on a folder that already is one, ``init_store`` fails
    before any file is touched.
    """
    from adapt.configuration.pipeline_template import (
        pipeline_template_names,
        write_pipeline_template,
    )
    from adapt.persistence.store import init_store
    from adapt.runtime.run_reporter import format_banner

    root = init_store(args.directory)
    print(format_banner(__version__) + "\n")
    print(f"Initialized empty Adapt store at {root}")
    for name in pipeline_template_names():
        path = root / f"{name}.yaml"
        if path.exists():
            print(f"Kept the existing pipeline file {path}")
            continue
        write_pipeline_template(name, path)
        print(f"Wrote the pipeline file {path}; run it from {root} with:  adapt run {name}")


# ---------------------------------------------------------------------------
# adapt config
# ---------------------------------------------------------------------------


def _config_cmd(args: argparse.Namespace) -> None:
    """Write ``./NAME.yaml`` from template NAME (``--NAME``), or print module
    options (``--params NAME...``), or add them to a file (``--params --file``)."""
    from adapt.cli._params import add_to_file, params

    if args.template is not None:
        if args.names or args.file is not None:
            raise ConfigError(
                f"adapt config --{args.template} writes ./{args.template}.yaml and takes no "
                "module names or --file; to add options to a file:  "
                "adapt config --params --file FILE [NAME...]"
            )
        _write_template(args.template, with_params=args.params)
    elif not args.params:
        raise ConfigError(
            "adapt config needs --params (print or add module options) or a template to "
            "write: " + ", ".join(f"--{name}" for name in _templates())
        )
    elif args.file is not None:
        path = Path(args.file)
        gained = add_to_file(path, args.names)
        if gained:
            print(f"Added the missing options of {', '.join(gained)} to {path}")
        else:
            print(f"{path} already gives every option of these modules; nothing changed")
    elif args.names:
        print(params(args.names), end="")
    else:
        raise ConfigError(
            "adapt config --params needs module names to print, or --file FILE to add "
            "the options of the modules FILE lists"
        )


def _templates() -> tuple[str, ...]:
    from adapt.configuration.pipeline_template import pipeline_template_names

    return pipeline_template_names()


def _write_template(name: str, *, with_params: bool) -> None:
    """Write ``./NAME.yaml`` from template NAME, with every catalog module's
    options under ``params:`` when ``with_params``; never over an existing file."""
    from adapt.cli._params import add_params
    from adapt.configuration.pipeline_template import pipeline_template
    from adapt.execution.pipeline_builder import catalog_module_names
    from adapt.persistence.store import store_root

    text = pipeline_template(name)
    if with_params:
        text, _ = add_params(text, catalog_module_names())
    path = Path.cwd() / f"{name}.yaml"
    try:
        with path.open("x", encoding="utf-8") as file:
            file.write(text)
    except FileExistsError:
        raise ConfigError(
            f"{path} already exists, and adapt config does not overwrite it. Add the "
            f"options it lacks with:  adapt config --params --file {path.name}  — or "
            "move it away first"
        ) from None
    print(f"Pipeline file written: {path}")
    if store_root(path.parent) == path.parent:
        print(f"Edit it, then run it from this folder with:  adapt run {name}")
    else:
        print(
            "Edit it. This folder is not an Adapt store yet: run  adapt init  here, "
            f"then  adapt run {name}"
        )


# ---------------------------------------------------------------------------
# adapt run
# ---------------------------------------------------------------------------


def _run_cmd(args: argparse.Namespace) -> None:
    from adapt.cli._run import RunFlags, run

    if args.skip_failed and args.run_id is None:
        raise ConfigError("--skip-failed applies to a run that is resumed: name it with --run-id")
    flags = RunFlags(verbose=args.verbose, skip_failed=args.skip_failed)
    if not run(args.name, args.run_id, args.overrides, flags):
        sys.exit(1)


# ---------------------------------------------------------------------------
# adapt graph
# ---------------------------------------------------------------------------


def _graph_cmd(args: argparse.Namespace) -> None:
    from adapt.cli._graph import graph

    print(graph(args.name, args.overrides), end="")


# ---------------------------------------------------------------------------
# adapt dashboard
# ---------------------------------------------------------------------------


def _dashboard_cmd(args: argparse.Namespace) -> None:
    """Launch the Adapt GUI dashboard."""
    try:
        os.getcwd()
    except FileNotFoundError:
        os.chdir(os.path.expanduser("~"))
    from adapt.consumers.live import main as dashboard

    dashboard(repo=args.repo)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


class _Version(argparse.Action):
    """``--version``: the version, then the install path, each on its own line."""

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        print(f"{parser.prog} {__version__}\nInstalled at: {Path(adapt.__file__).parent}")
        parser.exit()


def _add_set(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="params.KEY.OPTION=VALUE",
        help="Set an option for this run, over what the file says: KEY is a module or a "
        "params section, e.g. --set params.tracking.max_speed_ms=35. May be given more "
        "than once; a later setting of the same option holds.",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adapt",
        description=(
            "Adapt - Real-Time data processing for informed adaptive scanning "
            "of ARM weather radars."
        ),
    )
    parser.add_argument(
        "--version", action=_Version, nargs=0, help="Show the version and where Adapt is installed."
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.required = True

    init = commands.add_parser(
        "init",
        help="Create an Adapt store and its pipeline files.",
        description="Create the store (registry.db, logs/, collections/) and one "
        "pipeline file per template, e.g. nexrad.yaml.",
    )
    init.add_argument(
        "directory", nargs="?", default=".", help="Store root (default: current directory)."
    )
    init.set_defaults(func=_init_cmd)

    config = commands.add_parser(
        "config",
        help="Write a pipeline file, or print or add module options.",
        description="Write ./NAME.yaml from a pipeline template (never over a file), "
        "print modules' options with their defaults, or add the options a pipeline "
        "file lacks. Adding keeps every line the file has, comments included, and "
        "adds nothing the second time.",
        epilog="examples:\n"
        "  adapt config --nexrad                      write ./nexrad.yaml, empty params:\n"
        "  adapt config --nexrad --params             ... with every module's options\n"
        "  adapt config --params tracking projection  print these modules' options\n"
        "  adapt config --params global               print a params section's options\n"
        "  adapt config --params --file nexrad.yaml tracking\n"
        "                                             add tracking's missing options\n"
        "  adapt config --params --file nexrad.yaml   ... of every module it lists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    template = config.add_mutually_exclusive_group()
    for name in _templates():
        template.add_argument(
            f"--{name}",
            dest="template",
            action="store_const",
            const=name,
            help=f"Write ./{name}.yaml from the {name} pipeline template; refused when "
            "it exists. With --params, every module's options go under params:.",
        )
    config.set_defaults(template=None)
    config.add_argument(
        "--params",
        action="store_true",
        help="Print the options of the modules (or params sections) NAME with their "
        "defaults, or with --file add the options the file lacks.",
    )
    config.add_argument(
        "--file",
        metavar="FILE",
        default=None,
        help="With --params: the pipeline file to add options to — of the modules "
        "NAME, or of every module it lists when no NAME is given. Settings of a module "
        "it does not list may stay in it; only listed modules run.",
    )
    config.add_argument(
        "names", nargs="*", metavar="NAME", help="Modules (or, to print, params sections)."
    )
    config.set_defaults(func=_config_cmd)

    run = commands.add_parser(
        "run",
        help="Run the pipeline file ./NAME.yaml.",
        description="Run ./NAME.yaml from the current folder, which must be an Adapt "
        "store: its modules over the scans its source provides. With run.source: "
        "stored_run they run over the stored run --run-id instead. Ctrl-C stops it.",
    )
    run.add_argument("name", help="Pipeline file ./NAME.yaml, given as NAME or NAME.yaml.")
    run.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="Run to resume from its last checkpoint (format YYYYMONDD-HHMM-RADAR; an "
        "unknown id ending with the file's radar starts a new run with it), or the "
        "stored run to run over.",
    )
    run.add_argument(
        "--skip-failed",
        dest="skip_failed",
        action="store_true",
        help="Resuming --run-id: do not try again the scans the run failed before.",
    )
    run.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging.")
    _add_set(run)
    run.set_defaults(func=_run_cmd)

    graph = commands.add_parser(
        "graph",
        help="Print the module graph of the pipeline file ./NAME.yaml.",
        description="Print the module graph of ./NAME.yaml as Mermaid text: its modules "
        "and the data each hands the next. Nothing runs and no store is needed. Save it "
        "with 'adapt graph NAME > NAME.mmd'.",
    )
    graph.add_argument("name", help="Pipeline file ./NAME.yaml, given as NAME or NAME.yaml.")
    _add_set(graph)
    graph.set_defaults(func=_graph_cmd)

    dashboard = commands.add_parser(
        "dashboard",
        help="Open the GUI dashboard.",
        description="Launch the Adapt radar dashboard (read-only consumer).",
    )
    dashboard.add_argument(
        "--repo",
        default=None,
        help="Path to the Adapt output repository (pre-populates the repo field).",
    )
    dashboard.set_defaults(func=_dashboard_cmd)
    return parser


def main() -> None:
    """Top-level CLI dispatcher."""
    # The run's log goes to stderr unbuffered; keep stdout in step with it when
    # both are redirected to one file, so the messages read in the order printed.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    args = _parser().parse_args()
    try:
        args.func(args)
    except ConfigError as exc:
        # A configuration value is invalid — show the clear, section/field-level
        # message instead of a pydantic traceback, and exit non-zero.
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    except StoreError as exc:
        # Not a store, already a store, or no such run: the message says which.
        print(str(exc), file=sys.stderr)
        sys.exit(1)
