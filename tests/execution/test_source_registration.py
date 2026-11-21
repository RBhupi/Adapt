# Copyright © 2026, UChicago Argonne, LLC
# See LICENSE for terms and disclaimer.

"""The source catalog: where a pipeline's scans may come from.

``pipeline.sources`` of ``configuration/defaults.yaml`` holds one line per
source, naming its class as ``package.file:Class``. Loading it registers each
class under its ``source_name``. It fails loudly, naming the line, when the list
is empty or a line names no source class, and may be repeated.
"""

import pytest
import yaml

from adapt.contracts.source import ScanSource
from adapt.execution import catalog, source_registry
from adapt.execution.catalog import CATALOG
from adapt.execution.source_registry import SourceRegistry, catalog_source_names

pytestmark = pytest.mark.unit

registry = source_registry.source_registry


class _StandIn:
    """A class that satisfies ScanSource, for a catalog line of a test."""

    source_name = "stand_in"
    summary = "a source of no scans"
    options_class = None

    @classmethod
    def check(cls, config) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass

    def is_historical_complete(self) -> bool:
        return True

    def get_historical_progress(self) -> tuple[int, int]:
        return 0, 0


def _source_lines() -> list[str]:
    with open(CATALOG, encoding="utf-8") as f:
        return yaml.safe_load(f)["pipeline"]["sources"]


def _catalog(monkeypatch, tmp_path, *lines: str) -> None:
    """Make ``lines`` the source list of the catalog for this test."""
    path = tmp_path / "defaults.yaml"
    with open(CATALOG, encoding="utf-8") as f:
        modules = yaml.safe_load(f)["pipeline"]["modules"]
    path.write_text(
        yaml.safe_dump({"pipeline": {"modules": modules, "sources": list(lines)}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "CATALOG", path)


def _stand_in(monkeypatch, **attributes) -> str:
    """Put a ScanSource class with ``attributes`` where a catalog line can name it."""
    cls = type("_Variant", (_StandIn,), attributes)
    monkeypatch.setattr(source_registry, "_Variant", cls, raising=False)
    return "adapt.execution.source_registry:_Variant"


def test_every_line_names_its_class_as_file_and_class():
    for line in _source_lines():
        path, _, class_name = line.partition(":")
        assert path and class_name, f"catalog line {line!r} is not package.file:Class"


def test_the_class_each_line_names_is_registered_under_its_source_name():
    names = catalog_source_names()

    for line, name in zip(_source_lines(), names, strict=True):
        cls = registry.get(name)
        assert issubclass(cls, ScanSource)
        assert f"{cls.__module__}:{cls.__name__}" == line
        assert registry.summary(name)


def test_loading_the_catalog_again_changes_nothing():
    catalog_source_names()
    before = [(name, registry.get(name)) for name in registry.names()]

    catalog_source_names()

    assert [(name, registry.get(name)) for name in registry.names()] == before


def test_an_empty_source_list_is_refused(monkeypatch, tmp_path):
    _catalog(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="No pipeline sources declared"):
        catalog_source_names()


@pytest.mark.parametrize(
    "line",
    [
        "adapt.contracts:ProductView",  # a class, but no source
        "adapt.contracts.source:ScanSource",  # the interface itself
    ],
)
def test_a_line_that_names_no_source_class_is_refused_naming_the_line(monkeypatch, tmp_path, line):
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_source_names()

    assert repr(line) in str(refusal.value)
    assert "names no source class" in str(refusal.value)


@pytest.mark.parametrize("attribute", ["source_name", "summary"])
def test_a_class_with_an_empty_name_or_summary_is_refused(monkeypatch, tmp_path, attribute):
    line = _stand_in(monkeypatch, **{attribute: ""})
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_source_names()

    assert repr(line) in str(refusal.value)
    assert attribute in str(refusal.value)


def test_the_registry_gives_each_source_its_options_model():
    from adapt.sources.aws_nexrad.config import AwsNexradOptions
    from adapt.sources.local_directory.config import LocalDirectoryOptions

    catalog_source_names()

    assert registry.options_class("aws_nexrad") is AwsNexradOptions
    assert registry.options_class("local_directory") is LocalDirectoryOptions
    assert registry.options_class("stored_run") is None


@pytest.mark.parametrize("value", [dict, "AwsNexradOptions", 3])
def test_an_options_class_that_is_no_pydantic_model_is_refused(monkeypatch, tmp_path, value):
    line = _stand_in(monkeypatch, options_class=value)
    _catalog(monkeypatch, tmp_path, line)

    with pytest.raises(RuntimeError) as refusal:
        catalog_source_names()

    assert repr(line) in str(refusal.value)
    assert "options_class" in str(refusal.value)


def test_a_class_without_an_options_class_is_refused():
    bare = type("_Bare", (), {k: v for k, v in vars(_StandIn).items() if k != "options_class"})

    with pytest.raises(ValueError, match="options_class"):
        SourceRegistry().register(bare)


def test_two_lines_giving_one_name_are_refused(monkeypatch, tmp_path):
    first = _source_lines()[0]
    taken = first.rpartition(".source:")[0].rpartition(".")[2]
    _catalog(monkeypatch, tmp_path, first, _stand_in(monkeypatch, source_name=taken))

    with pytest.raises(RuntimeError, match=f"'{taken}' is already registered"):
        catalog_source_names()

    assert f"{registry.get(taken).__module__}:{registry.get(taken).__name__}" == first


def test_an_unknown_name_is_refused():
    with pytest.raises(KeyError, match="unknown_source"):
        registry.get("unknown_source")


def test_a_second_registration_is_refused():
    reg = SourceRegistry()
    reg.register(_StandIn)

    with pytest.raises(RuntimeError, match="stand_in"):
        reg.register(_StandIn)


def test_a_source_registered_for_a_test_can_be_taken_out_again():
    reg = SourceRegistry()
    reg.register(_StandIn)

    reg.unregister("stand_in")

    assert "stand_in" not in reg
    assert reg.names() == []
