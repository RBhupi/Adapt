# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Adapt command-line interface.

Entry point: ``adapt``. The project folder is the store, and each pipeline is a
file ``NAME.yaml`` in it::

    mkdir case && cd case
    adapt init                  # the store + one pipeline file per template (nexrad.yaml)
    adapt config nexrad         # (re)write ./nexrad.yaml from the template
    adapt config --show NAME    # NAME's options with their defaults, ready to paste
    adapt run nexrad            # run ./nexrad.yaml: its modules, scan by scan
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
    from adapt.configuration.pipeline_template import PIPELINE_TEMPLATES, write_pipeline_template
    from adapt.persistence.store import init_store
    from adapt.runtime.run_reporter import format_banner

    root = init_store(args.directory)
    print(format_banner(__version__) + "\n")
    print(f"Initialized empty Adapt store at {root}")
    for name in PIPELINE_TEMPLATES:
        path = root / f"{name}.yaml"
        if path.exists():
            print(f"Kept the existing pipeline file {path} (adapt config {name} rewrites it)")
            continue
        write_pipeline_template(name, path)
        print(f"Wrote the pipeline file {path}; run it from {root} with:  adapt run {name}")


# ---------------------------------------------------------------------------
# adapt config
# ---------------------------------------------------------------------------


def _config_cmd(args: argparse.Namespace) -> None:
    """Write ``./NAME.yaml`` from template NAME, or print an option fragment (``--show``)."""
    if args.show is not None:
        from adapt.cli._show import show

        print(show(args.show), end="")
        return

    from adapt.configuration.pipeline_template import PIPELINE_TEMPLATES, write_pipeline_template
    from adapt.persistence.store import store_root

    if args.name not in PIPELINE_TEMPLATES:
        raise ConfigError(
            f"Unknown pipeline template '{args.name}'. Available: {', '.join(PIPELINE_TEMPLATES)}"
        )
    path = Path.cwd() / f"{args.name}.yaml"
    if path.exists():
        print(f"[adapt config] File already exists: {path}")
        if not _confirm("Overwrite? [y/N] "):
            print("Aborted.")
            return
    write_pipeline_template(args.name, path)
    print(f"Pipeline file written: {path}")
    if store_root(path.parent) == path.parent:
        print(f"Edit it, then run it from this folder with:  adapt run {args.name}")
    else:
        print(
            "Edit it. This folder is not an Adapt store yet: run  adapt init  here, "
            f"then  adapt run {args.name}"
        )


def _confirm(prompt: str) -> bool:
    """Ask a yes/no question; no input (EOF) is a no, Ctrl-C aborts the command."""
    try:
        return input(prompt).strip().lower() == "y"
    except EOFError:
        return False
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# adapt run
# ---------------------------------------------------------------------------


def _run_cmd(args: argparse.Namespace) -> None:
    from adapt.cli._run import run

    if args.skip_failed and args.run_id is None:
        raise ConfigError("--skip-failed applies to a run that is resumed: name it with --run-id")
    if not run(args.name, args.run_id, verbose=args.verbose, skip_failed=args.skip_failed):
        sys.exit(1)


# ---------------------------------------------------------------------------
# adapt graph
# ---------------------------------------------------------------------------


def _graph_cmd(args: argparse.Namespace) -> None:
    from adapt.cli._graph import graph

    print(graph(args.name), end="")


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
        help="Write a pipeline file, or show options to paste into one.",
        description="Write ./NAME.yaml from pipeline template NAME, or print the "
        "options of a module or params section with their defaults.",
    )
    which = config.add_mutually_exclusive_group(required=True)
    which.add_argument("name", nargs="?", help="Pipeline template to write, e.g. nexrad.")
    which.add_argument(
        "--show", metavar="NAME", help="Print NAME's options as YAML to paste into the file."
    )
    config.set_defaults(func=_config_cmd)

    run = commands.add_parser(
        "run",
        help="Run the pipeline file ./NAME.yaml.",
        description="Run ./NAME.yaml from the current folder, which must be an Adapt "
        "store: its modules over the scans its source provides. With run.source: "
        "stored_run they run over the stored run --run-id instead. Ctrl-C stops it.",
    )
    run.add_argument("name", help="Pipeline name: the file ./NAME.yaml.")
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
    run.set_defaults(func=_run_cmd)

    graph = commands.add_parser(
        "graph",
        help="Print the module graph of the pipeline file ./NAME.yaml.",
        description="Print the module graph of ./NAME.yaml as Mermaid text: its modules "
        "and the data each hands the next. Nothing runs and no store is needed. Save it "
        "with 'adapt graph NAME > NAME.mmd'.",
    )
    graph.add_argument("name", help="Pipeline name: the file ./NAME.yaml.")
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
