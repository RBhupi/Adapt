# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline templates: the short, commented pipeline file ``adapt init`` /
``adapt config --NAME`` write.

Only names are listed: the module catalog, each module commented with its
one-line summary, the template's modules on and the others offered commented
out. The written file loads and runs as is.
"""

import textwrap
from datetime import UTC, datetime

import pytest

from adapt.configuration.pipeline_file import PipelineFile, RunSection, load_pipeline_file
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.pipeline_template import (
    pipeline_template,
    pipeline_template_names,
    write_pipeline_template,
)
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.module_resolver import resolve_module_configs
from adapt.execution.module_registry import registry
from adapt.execution.source_registry import catalog_source_names
from adapt.persistence.store import init_store

pytestmark = pytest.mark.unit

_NEXRAD = """\
# Adapt pipeline file. Run it with:  adapt run <this file's name>
# Turn a module on by uncommenting its line; Adapt orders the modules by what each reads.
# Everything not set here keeps its default. To see a module's options:
#   adapt config --params NAME
# and give only the ones you change, under params:. To add every option a listed
# module lacks, with its default:  adapt config --params --file <this file's name>
# To see how the listed modules feed each other:  adapt graph <this file's name>

run:
  source: aws_nexrad          # where scans come from: aws_nexrad | local_directory | stored_run
  radar: KLOT                 # radar / collection id
  mode: realtime              # realtime | historical
  start_time: null            # historical: ISO 8601 UTC, e.g. 2025-06-17T18:00:00Z
  end_time: null
  source_dir: null            # local_directory: folder of Level-II files
  max_runtime_minutes: null   # realtime: stop after this many minutes; null = until Ctrl-C

modules:                      # per-scan modules; Adapt orders them by their inputs and outputs
  - ingest                    # {ingest}
  - detection                 # {detection}
  - projection                # {projection}
  - analysis                  # {analysis}
  - tracking                  # {tracking}
  - cell_volume_stats         # {cell_volume_stats}
  # - target_selection        # {target_selection}
  # - arm_radar_comm          # {arm_radar_comm}
  # - xlma_stat               # {xlma_stat}

params:
"""


def _summaries() -> dict[str, str]:
    return {name: registry.get(name).module_summary() for name in registry.list_modules()}


def test_the_source_comment_lists_the_catalogs_sources_and_the_model_names_none():
    sources = catalog_source_names()
    written = pipeline_template("nexrad").splitlines()

    line = next(line for line in written if line.startswith("  source:"))
    assert line.endswith(f"# where scans come from: {' | '.join(sources)}")
    assert not any(s in RunSection.model_fields["source"].description for s in sources)


def test_nexrad_is_the_one_packaged_template():
    assert pipeline_template_names() == ("nexrad",)


def test_nexrad_template_writes_the_short_commented_file(tmp_path):
    path = tmp_path / "nexrad.yaml"

    write_pipeline_template("nexrad", path)

    expected = _NEXRAD.format(**_summaries())
    assert path.read_text(encoding="utf-8") == expected


def test_the_file_names_no_pipeline_so_a_copy_stays_right(tmp_path):
    # A pipeline is named by its file: a copy of nexrad.yaml as kdix.yaml is the kdix pipeline.
    written = []
    for name in ("nexrad", "kdix"):
        write_pipeline_template("nexrad", tmp_path / f"{name}.yaml")
        written.append((tmp_path / f"{name}.yaml").read_text(encoding="utf-8"))

    assert written[0] == written[1]
    assert "nexrad" not in written[0].split("\nrun:")[0]


def test_written_file_loads_with_the_templates_modules_on_and_the_rest_offered(tmp_path):
    path = tmp_path / "nexrad.yaml"
    write_pipeline_template("nexrad", path)

    pipeline = load_pipeline_file(path)

    assert pipeline.run == RunSection(source="aws_nexrad", radar="KLOT", mode="realtime")
    assert pipeline.modules == (
        "ingest",
        "detection",
        "projection",
        "analysis",
        "tracking",
        "cell_volume_stats",
    )
    assert pipeline.params == {}
    offered = [line for line in path.read_text(encoding="utf-8").splitlines() if "# - " in line]
    assert [line.split()[2] for line in offered] == [
        "target_selection",
        "arm_radar_comm",
        "xlma_stat",
    ]


def test_uncommenting_the_offered_radar_modules_gives_a_runnable_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ARM_ADAPT_USER", "adapt_ops")  # the variable the packaged example names
    root = init_store(tmp_path / "store")
    path = root / "nexrad.yaml"
    write_pipeline_template("nexrad", path)
    text = path.read_text(encoding="utf-8")
    for module in ("target_selection", "arm_radar_comm"):
        text = text.replace(f"  # - {module}", f"  - {module}")
    # arm_radar_comm has no defaults: its options are pasted under params:, the last line.
    text += textwrap.indent(registry.get("arm_radar_comm").options_preset(), "  ")
    path.write_text(text, encoding="utf-8")

    config = run_config(load_pipeline_file(path), path)

    assert config.modules[-2:] == ["target_selection", "arm_radar_comm"]
    assert set(config.module_params) == {
        "ingest",
        "detection",
        "projection",
        "analysis",
        "tracking",
        "cell_volume_stats",
        "target_selection",
        "arm_radar_comm",
    }


def test_written_file_resolves_to_a_runnable_config(tmp_path):
    root = init_store(tmp_path / "store")
    path = root / "nexrad.yaml"
    write_pipeline_template("nexrad", path)

    config = run_config(load_pipeline_file(path), path)

    assert config.base_dir == str(root)
    assert config.radar == "KLOT"
    assert config.modules == [
        "ingest",
        "detection",
        "projection",
        "analysis",
        "tracking",
        "cell_volume_stats",
    ]


def test_written_file_leaves_every_option_at_its_default(tmp_path):
    root = init_store(tmp_path / "store")
    path = root / "nexrad.yaml"
    write_pipeline_template("nexrad", path)

    config = run_config(load_pipeline_file(path), path)

    owners = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]
    nothing_set = PipelineFile.model_validate(
        {
            "run": {"source": "aws_nexrad", "radar": "KLOT", "mode": "realtime"},
            "modules": owners,
            "params": {},
        }
    )
    defaults = run_config(nothing_set, path)
    for section in ("source_params", "global_", "logging", "queue"):
        assert getattr(config, section) == getattr(defaults, section), section
    assert resolve_module_configs(config, owners) == resolve_module_configs(defaults, owners)


def test_unknown_template_lists_the_available_ones(tmp_path):
    with pytest.raises(ConfigError, match="nexrad"):
        write_pipeline_template("goes", tmp_path / "goes.yaml")


def test_unknown_template_writes_nothing(tmp_path):
    with pytest.raises(ConfigError):
        write_pipeline_template("goes", tmp_path / "goes.yaml")

    assert not (tmp_path / "goes.yaml").exists()


def test_run_values_replace_the_template_defaults(tmp_path):
    path = tmp_path / "dashboard.yaml"

    write_pipeline_template(
        "nexrad",
        path,
        radar="KTST",
        mode="historical",
        start_time="2025-06-17T18:00:00Z",
        end_time="2025-06-17T19:00:00Z",
    )

    assert load_pipeline_file(path).run == RunSection(
        source="aws_nexrad",
        radar="KTST",
        mode="historical",
        start_time=datetime(2025, 6, 17, 18, tzinfo=UTC),
        end_time=datetime(2025, 6, 17, 19, tzinfo=UTC),
    )


@pytest.mark.parametrize("folder", ["2025", "true", "null", "1.5"])
def test_run_values_that_look_like_other_types_reload_unchanged(tmp_path, folder):
    path = tmp_path / "local.yaml"

    write_pipeline_template(
        "nexrad",
        path,
        source="local_directory",
        source_dir=folder,
        mode="historical",
        start_time="2025-06-17T18:00:00Z",
        end_time="2025-06-17T19:00:00Z",
    )

    assert load_pipeline_file(path).run.source_dir == folder


@pytest.mark.parametrize(
    ("values", "key"),
    [
        ({"mode": "historical", "start_time": "2025-06-17 18:00"}, "start_time"),
        ({"mode": "sometimes"}, "mode"),
        ({"radar_id": "KTST"}, "radar_id"),
    ],
)
def test_invalid_run_values_are_refused_and_nothing_is_written(tmp_path, values, key):
    path = tmp_path / "dashboard.yaml"

    with pytest.raises(ConfigError, match=key):
        write_pipeline_template("nexrad", path, **values)

    assert not path.exists()


# ── A template is a pipeline file in the packaged templates folder ───────────


def _templates_folder(tmp_path, monkeypatch, files: dict[str, str]):
    """Point the templates folder at a new temporary one holding ``files``."""
    folder = tmp_path / "templates"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(textwrap.dedent(text), encoding="utf-8")
    monkeypatch.setattr("adapt.configuration.pipeline_template._TEMPLATES", folder)
    return folder


_OTHER = """\
    run:
      source: aws_nexrad
      radar: KXYZ
      mode: realtime
    modules:
      - ingest
      - detection
    params:
    """


def test_a_file_in_the_templates_folder_is_a_template(tmp_path, monkeypatch):
    _templates_folder(tmp_path, monkeypatch, {"other.yaml": _OTHER})
    path = tmp_path / "other.yaml"

    assert pipeline_template_names() == ("other",)
    write_pipeline_template("other", path)

    text = path.read_text(encoding="utf-8")
    assert "\n  radar: KXYZ " in text
    assert "\n  - ingest " in text
    assert "\n  - detection " in text
    assert "\n  # - projection " in text
    pipeline = load_pipeline_file(path)
    assert pipeline.run.radar == "KXYZ"
    assert pipeline.modules == ("ingest", "detection")


def test_templates_are_listed_in_name_order(tmp_path, monkeypatch):
    _templates_folder(tmp_path, monkeypatch, {"b.yaml": _OTHER, "a.yaml": _OTHER})

    assert pipeline_template_names() == ("a", "b")


def test_a_template_that_lists_an_unknown_module_is_refused_naming_its_file(tmp_path, monkeypatch):
    _templates_folder(
        tmp_path, monkeypatch, {"other.yaml": _OTHER.replace("- detection", "- no_such_module")}
    )

    with pytest.raises(ConfigError, match="no_such_module") as refused:
        pipeline_template("other")

    assert "other.yaml" in str(refused.value)


def test_a_template_that_sets_params_is_refused(tmp_path, monkeypatch):
    text = _OTHER.replace(
        "    params:\n", "    params:\n      tracking:\n        max_speed_ms: 35\n"
    )
    _templates_folder(tmp_path, monkeypatch, {"other.yaml": text})

    with pytest.raises(ConfigError, match="params") as refused:
        pipeline_template("other")

    assert "other.yaml" in str(refused.value)


def test_a_templates_folder_without_a_template_is_refused(tmp_path, monkeypatch):
    folder = _templates_folder(tmp_path, monkeypatch, {})

    with pytest.raises(RuntimeError, match="No pipeline template") as refused:
        pipeline_template_names()

    assert str(folder) in str(refused.value)
