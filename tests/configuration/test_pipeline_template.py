# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""Pipeline templates: the short, commented pipeline file ``adapt init`` /
``adapt config NAME`` write.

Only names are listed: the module catalog, each module commented with its
one-line summary, the template's modules on and the others offered commented
out. The written file loads and runs as is.
"""

from datetime import UTC, datetime

import pytest

from adapt.configuration.pipeline_file import RunSection, load_pipeline_file
from adapt.configuration.pipeline_run import run_config
from adapt.configuration.pipeline_template import PIPELINE_TEMPLATES, write_pipeline_template
from adapt.configuration.schemas.errors import ConfigError
from adapt.configuration.schemas.param import ParamConfig
from adapt.configuration.schemas.resolve import resolve_config
from adapt.configuration.schemas.user import UserConfig
from adapt.execution.module_registry import registry
from adapt.persistence.store import init_store

pytestmark = pytest.mark.unit

_NEXRAD = """\
# Adapt pipeline file. Run it with:  adapt run <this file's name>
# Turn a module on by uncommenting its line; Adapt orders the modules by what each reads.
# Everything not set here keeps its default. To see a module's options:
#   adapt config --show NAME
# and give only the ones you change, under params:.
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
  # - radar_command           # {radar_command}
  # - xlma_stat               # {xlma_stat}

params:
"""


def _summaries() -> dict[str, str]:
    return {name: registry.get(name).module_summary() for name in registry.list_modules()}


def test_nexrad_is_the_one_registered_template():
    assert list(PIPELINE_TEMPLATES) == ["nexrad"]


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

    assert pipeline.run == PIPELINE_TEMPLATES["nexrad"].run
    assert pipeline.modules == PIPELINE_TEMPLATES["nexrad"].modules
    assert pipeline.params == {}
    offered = [line for line in path.read_text(encoding="utf-8").splitlines() if "# - " in line]
    assert [line.split()[2] for line in offered] == [
        "target_selection",
        "radar_command",
        "xlma_stat",
    ]


def test_uncommenting_the_offered_radar_modules_gives_a_runnable_file(tmp_path):
    root = init_store(tmp_path / "store")
    path = root / "nexrad.yaml"
    write_pipeline_template("nexrad", path)
    text = path.read_text(encoding="utf-8")
    for module in ("target_selection", "radar_command"):
        text = text.replace(f"  # - {module}", f"  - {module}")
    path.write_text(text, encoding="utf-8")

    config = run_config(load_pipeline_file(path), path)

    assert config.modules[-2:] == ["target_selection", "radar_command"]
    assert set(config.module_params) == {"cell_volume_stats", "target_selection", "radar_command"}


def test_written_file_resolves_to_a_runnable_config(tmp_path):
    root = init_store(tmp_path / "store")
    path = root / "nexrad.yaml"
    write_pipeline_template("nexrad", path)

    config = run_config(load_pipeline_file(path), path)

    assert config.base_dir == str(root)
    assert config.downloader.radar == "KLOT"
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

    defaults = resolve_config(ParamConfig(), UserConfig(radar="KLOT", base_dir=str(root)))
    for section in (
        "reader",
        "downloader",
        "regridder",
        "segmenter",
        "global_",
        "projector",
        "analyzer",
        "tracker",
        "visualization",
        "output",
        "logging",
    ):
        assert getattr(config, section) == getattr(defaults, section), section
    cvs = registry.get("cell_volume_stats")
    assert cvs.build_config(config) == cvs.build_config(defaults)


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
