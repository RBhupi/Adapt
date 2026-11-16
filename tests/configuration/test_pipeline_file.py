# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The pipeline file: one YAML file per pipeline, validated before anything runs.

Module names are checked against the module catalog, ``params:`` keys are routed
to ParamConfig sections or to the listed modules that own their options, and
every mistake fails loudly with the file, the key, and what would be valid.
"""

from datetime import UTC, datetime

import pytest
import yaml

from adapt.configuration.pipeline_file import PipelineFile, load_pipeline_file
from adapt.configuration.schemas.errors import ConfigError

pytestmark = pytest.mark.unit

_MODULES = ["ingest", "detection", "projection", "analysis", "tracking", "cell_volume_stats"]


def _file(**overrides) -> dict:
    data = {
        "run": {"source": "aws_nexrad", "radar": "KLOT", "mode": "realtime"},
        "modules": list(_MODULES),
        "params": {},
    }
    data.update(overrides)
    return data


def _write(tmp_path, data: dict | str, name: str = "nexrad.yaml"):
    path = tmp_path / name
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path, data) -> PipelineFile:
    return load_pipeline_file(_write(tmp_path, data))


class TestSections:
    def test_params_whose_entries_are_all_commented_out_are_empty(self, tmp_path):
        text = (
            "run:\n"
            "  source: aws_nexrad\n"
            "  radar: KLOT\n"
            "  mode: realtime\n"
            "modules:\n"
            "  - ingest\n"
            "  # - detection\n"
            "params:\n"
            "  # tracker:\n"
        )

        pipeline = _load(tmp_path, text)

        assert pipeline.modules == ("ingest",)
        assert pipeline.params == {}

    def test_modules_keep_file_order(self, tmp_path):
        pipeline = _load(tmp_path, _file(modules=["tracking", *_MODULES[:-2]]))

        assert pipeline.modules == ("tracking", *_MODULES[:-2])

    def test_unknown_top_level_key_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="pipelines"):
            _load(tmp_path, _file(pipelines=["x"]))

    @pytest.mark.parametrize("section", ["core", "extended", "consumers", "postprocessing"])
    def test_the_sections_of_the_old_format_are_refused(self, tmp_path, section):
        with pytest.raises(ConfigError, match=section):
            _load(tmp_path, _file(**{section: []}))

    def test_missing_section_is_rejected(self, tmp_path):
        data = _file()
        del data["modules"]

        with pytest.raises(ConfigError, match="modules"):
            _load(tmp_path, data)

    def test_error_names_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match="nexrad.yaml"):
            _load(tmp_path, _file(pipelines=["x"]))

    def test_file_that_is_not_a_mapping_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="nexrad.yaml"):
            _load(tmp_path, "- ingest\n- detection\n")

    def test_file_that_is_not_yaml_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="nexrad.yaml"):
            _load(tmp_path, "run: [unclosed\n")

    def test_a_section_given_twice_is_refused_naming_its_lines(self, tmp_path):
        # E.g. a second `params:` line, with an entry under it, appended to the file.
        text = yaml.safe_dump(_file(), sort_keys=False) + "params:\n  tracker: {}\n"
        first = text.splitlines().index("params: {}") + 1
        last = len(text.splitlines()) - 1

        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, text)

        message = str(exc.value)
        assert "'params'" in message
        assert f"lines {first} and {last}" in message

    def test_an_option_given_twice_is_refused(self, tmp_path):
        text = yaml.safe_dump(_file(), sort_keys=False).replace(
            "params: {}\n", "params:\n  tracker:\n    max_speed: 1\n    max_speed: 2\n"
        )

        with pytest.raises(ConfigError, match="'max_speed'"):
            _load(tmp_path, text)

    def test_loaded_pipeline_is_immutable(self, tmp_path):
        pipeline = _load(tmp_path, _file())

        with pytest.raises(ValueError, match="frozen"):
            pipeline.modules = ()  # type: ignore[misc]


class TestModules:
    def test_unknown_module_names_the_module_and_the_catalog(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(modules=[*_MODULES, "segmentation"]))

        message = str(exc.value)
        assert "unknown module 'segmentation'" in message
        assert all(name in message for name in (*_MODULES, "target_selection", "xlma_stat"))

    def test_module_listed_twice_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="tracking"):
            _load(tmp_path, _file(modules=[*_MODULES, "tracking"]))

    def test_a_file_that_lists_no_module_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="lists no module"):
            _load(tmp_path, _file(modules=None))

    def test_any_catalog_module_may_be_listed(self, tmp_path):
        modules = [*_MODULES, "target_selection", "radar_command", "xlma_stat"]

        assert _load(tmp_path, _file(modules=modules)).modules == tuple(modules)


class TestParams:
    @pytest.mark.parametrize("section", ["segmenter", "tracker", "global", "downloader"])
    def test_paramconfig_section_names_are_accepted(self, tmp_path, section):
        pipeline = _load(tmp_path, _file(params={section: {}}))

        assert section in pipeline.params

    @pytest.mark.parametrize(
        ("module", "options"),
        [("cell_volume_stats", {"thresholds": [30.0]}), ("xlma_stat", {"search_radius_m": 250.0})],
    )
    def test_the_options_of_a_module_that_owns_them_are_accepted(self, tmp_path, module, options):
        pipeline = _load(
            tmp_path, _file(modules=[*_MODULES, "xlma_stat"], params={module: options})
        )

        assert pipeline.params[module] == options

    @pytest.mark.parametrize("module", ["cell_volume_stats", "xlma_stat"])
    def test_a_mistyped_module_option_lists_the_modules_options(self, tmp_path, module):
        with pytest.raises(ConfigError) as exc:
            _load(
                tmp_path,
                _file(modules=[*_MODULES, "xlma_stat"], params={module: {"input_dirr": "/data"}}),
            )

        message = str(exc.value)
        assert f"params.{module}" in message
        assert "input_dirr" in message
        assert f"adapt config --show {module}" in message

    def test_a_module_option_filled_from_global_is_refused(self, tmp_path):
        # cell_volume_stats takes its reflectivity variable from params.global.
        with pytest.raises(ConfigError, match="reflectivity_var"):
            _load(tmp_path, _file(params={"cell_volume_stats": {"reflectivity_var": "dbz"}}))

    @pytest.mark.parametrize(
        ("module", "sections"),
        [
            ("ingest", ["reader", "regridder"]),
            ("detection", ["segmenter"]),
            ("projection", ["projector"]),
            ("analysis", ["analyzer"]),
            ("tracking", ["tracker"]),
        ],
    )
    def test_a_modules_name_points_at_the_sections_holding_its_options(
        self, tmp_path, module, sections
    ):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={module: {}}))

        message = str(exc.value)
        assert all(section in message for section in sections)
        assert f"adapt config --show {module}" in message

    def test_unknown_key_lists_the_valid_keys(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={"segmentor": {}}))

        message = str(exc.value)
        assert "segmentor" in message
        assert "segmenter" in message
        assert "cell_volume_stats" in message

    def test_a_section_whose_options_are_all_commented_out_has_empty_options(self, tmp_path):
        pipeline = _load(tmp_path, _file(params={"tracker": None}))

        assert pipeline.params == {"tracker": {}}

    @pytest.mark.parametrize("key", ["radar", "mode", "start_time", "end_time"])
    def test_downloader_settings_owned_by_run_are_rejected(self, tmp_path, key):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={"downloader": {key: "x"}}))

        assert f"run.{key}" in str(exc.value)

    @pytest.mark.parametrize(
        ("section", "options", "path"),
        [
            ("regridder", {"min_radius": -5}, "regridder.min_radius"),
            ("segmenter", {"h_maxima": -1}, "segmenter.h_maxima"),
            ("segmenter", {"min_cellsize_gridpoint": 0}, "segmenter.min_cellsize_gridpoint"),
            ("projector", {"flow_params": {"poly_n": 1}}, "projector.flow_params.poly_n"),
            ("projector", {"flow_params": {"pyr_scale": 5}}, "projector.flow_params.pyr_scale"),
            ("projector", {"max_projection_steps": 25}, "projector.max_projection_steps"),
            ("downloader", {"min_file_size": 0}, "downloader.min_file_size"),
            ("downloader", {"queue_resume_fraction": 3}, "downloader.queue_resume_fraction"),
            ("visualization", {"dpi": 10}, "visualization.dpi"),
        ],
    )
    def test_a_section_option_out_of_its_bounds_is_refused(self, tmp_path, section, options, path):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={section: options}))

        assert "nexrad.yaml" in str(exc.value)
        assert f"params.{path} — value" in str(exc.value)
        assert "out of range" in str(exc.value)

    @pytest.mark.parametrize(
        ("section", "key"),
        [
            ("segmenter", "threshhold"),
            ("segmenter", "seed_carry"),  # retired
            ("segmenter", "closing_kernel"),  # retired
            ("logging", "progress_every"),  # never offered
            ("global", "bogus"),
        ],
    )
    def test_an_unknown_section_option_is_refused_naming_the_file(self, tmp_path, section, key):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={section: {key: 1}}))

        assert "nexrad.yaml" in str(exc.value)
        assert f"params.{section}.{key} — unrecognized option" in str(exc.value)

    @pytest.mark.parametrize(
        ("section", "options", "path"),
        [
            ("output", {"compression": "gzip"}, "output.compression"),
            ("visualization", {"enabled": False}, "visualization.enabled"),
            ("tracker", {"cell_uid": {"alphabet": "base36_upper"}}, "tracker.cell_uid.alphabet"),
            ("downloader", {"output_dir": "/elsewhere"}, "downloader.output_dir"),
        ],
    )
    def test_an_obsolete_option_is_refused_as_having_no_effect(
        self, tmp_path, section, options, path
    ):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(params={section: options}))

        message = str(exc.value)
        assert f"params.{path}" in message
        assert "obsolete option" in message

    @pytest.mark.parametrize(
        ("module", "options"),
        [
            ("cell_volume_stats", {"thresholds": [99.0]}),
            ("xlma_stat", {"input_dir": "/does/not/exist"}),
            ("target_selection", {"duration": {"max_minutes": 15}}),
        ],
    )
    def test_params_of_a_module_the_file_does_not_list_are_refused(self, tmp_path, module, options):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(modules=_MODULES[:-1], params={module: options}))

        message = str(exc.value)
        assert f"params.{module}" in message
        assert "'modules:'" in message

    def test_a_part_of_a_packaged_rule_may_be_given_alone(self, tmp_path):
        params = {
            "target_selection": {"duration": {"max_minutes": 15}},
            "radar_command": {"api": {"base_url_env": "ADAPT_TEST_URL"}},
        }
        modules = [*_MODULES, "target_selection", "radar_command"]

        assert _load(tmp_path, _file(modules=modules, params=params)).params == params

    def test_an_unknown_part_of_a_packaged_rule_is_refused(self, tmp_path):
        modules = [*_MODULES, "target_selection"]

        with pytest.raises(ConfigError, match="unknown option 'entree'"):
            _load(tmp_path, _file(modules=modules, params={"target_selection": {"entree": {}}}))


class TestRunSection:
    def _run(self, **fields) -> dict:
        return {"source": "aws_nexrad", "radar": "KLOT", "mode": "historical", **fields}

    def test_unquoted_utc_timestamps_are_accepted(self, tmp_path):
        text = yaml.safe_dump(_file(run=self._run()), sort_keys=False).replace(
            "  mode: historical\n",
            "  mode: historical\n"
            "  start_time: 2025-06-17T18:00:00Z\n"
            "  end_time: 2025-06-17T22:00:00+02:00\n",
        )

        run = _load(tmp_path, text).run

        assert run.start_time == datetime(2025, 6, 17, 18, 0, tzinfo=UTC)
        assert run.end_time == datetime(2025, 6, 17, 20, 0, tzinfo=UTC)

    def test_timestamp_without_utc_offset_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="start_time"):
            _load(tmp_path, _file(run=self._run(start_time="2025-06-17T18:00:00")))

    def test_a_stored_run_source_walks_the_whole_run(self, tmp_path):
        run = {"source": "stored_run", "radar": "KLOT", "mode": "historical"}

        assert _load(tmp_path, _file(run=run)).run.source == "stored_run"

    @pytest.mark.parametrize(
        "fields",
        [{"mode": "realtime"}, {"mode": "historical", "start_time": "2025-06-17T18:00:00Z"}],
    )
    def test_a_stored_run_source_takes_no_mode_or_window(self, tmp_path, fields):
        run = {"source": "stored_run", "radar": "KLOT", **fields}

        with pytest.raises(ConfigError, match="source: stored_run walks the scans"):
            _load(tmp_path, _file(run=run))

    def test_max_runtime_minutes_must_be_positive(self, tmp_path):
        with pytest.raises(ConfigError, match="max_runtime_minutes"):
            _load(tmp_path, _file(run=self._run(mode="realtime", max_runtime_minutes=0)))

    @pytest.mark.parametrize("key", ["start_time", "end_time"])
    def test_scan_times_are_refused_in_realtime(self, tmp_path, key):
        run = self._run(mode="realtime", **{key: "2025-06-17T18:00:00Z"})

        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(run=run))

        assert f"run.{key}" in str(exc.value)
        assert "historical" in str(exc.value)

    def test_max_runtime_minutes_is_refused_in_historical(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(run=self._run(max_runtime_minutes=30)))

        assert "run.max_runtime_minutes" in str(exc.value)
        assert "realtime" in str(exc.value)

    def test_end_time_must_be_after_start_time(self, tmp_path):
        run = self._run(start_time="2026-10-21T16:00:00Z", end_time="2026-10-21T15:00:00Z")

        with pytest.raises(ConfigError, match="run.end_time must be after run.start_time"):
            _load(tmp_path, _file(run=run))

    def test_local_directory_needs_its_folder(self, tmp_path):
        with pytest.raises(ConfigError, match="run.source_dir"):
            _load(tmp_path, _file(run=self._run(source="local_directory")))

    def test_local_directory_needs_historical_mode(self, tmp_path):
        run = self._run(source="local_directory", mode="realtime", source_dir=str(tmp_path))

        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(run=run))

        assert "source: local_directory" in str(exc.value)
        assert "mode: historical" in str(exc.value)

    def test_a_folder_is_refused_for_a_downloading_source(self, tmp_path):
        with pytest.raises(ConfigError, match="run.source_dir"):
            _load(tmp_path, _file(run=self._run(source_dir=str(tmp_path))))

    def test_a_folder_for_a_mistyped_source_is_not_said_to_be_unneeded(self, tmp_path):
        # 'local_dir' may be a typo of local_directory: never say the source fetches scans.
        with pytest.raises(ConfigError) as exc:
            _load(tmp_path, _file(run=self._run(source="local_dir", source_dir=str(tmp_path))))

        message = str(exc.value)
        assert "run.source_dir applies to source: local_directory only" in message
        assert "fetches" not in message

    def test_unknown_run_key_is_rejected(self, tmp_path):
        with pytest.raises(ConfigError, match="radr"):
            _load(tmp_path, _file(run=self._run(radr="KLOT")))

    @pytest.mark.parametrize("key", ["source", "radar", "mode"])
    def test_where_what_and_how_are_required(self, tmp_path, key):
        run = self._run()
        del run[key]

        with pytest.raises(ConfigError, match=key):
            _load(tmp_path, _file(run=run))
