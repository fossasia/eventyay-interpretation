import logging
from pathlib import Path

from interpretation.language_map import (
    LANGUAGE_NAME_TO_CODE,
    PACKAGE_LANGUAGE_MAP,
    language_code_for_name,
    load_language_map,
)


def test_language_map_is_not_required_at_repo_root():
    repo_root = Path(__file__).resolve().parents[1]
    assert not (repo_root / "language_map.yml").exists()
    assert PACKAGE_LANGUAGE_MAP.is_file()


def test_language_map_loads_english_and_german():
    catalog = load_language_map()
    assert catalog["English"] == "en"
    assert catalog["German"] == "de"
    assert LANGUAGE_NAME_TO_CODE["English"] == "en"
    assert language_code_for_name("German") == "de"
    assert language_code_for_name("unknown") == "unknown"


def test_language_map_load_does_not_log_error(caplog):
    with caplog.at_level(logging.ERROR, logger="interpretation.language_map"):
        catalog = load_language_map()
    assert catalog["English"] == "en"
    assert not caplog.records


def test_language_map_falls_back_to_legacy_root(tmp_path, monkeypatch):
    from interpretation import language_map as module

    missing = tmp_path / "missing.yml"
    legacy = tmp_path / "language_map.yml"
    legacy.write_text("English: en\nKlingon: tlh\n", encoding="utf-8")
    monkeypatch.setattr(module, "PACKAGE_LANGUAGE_MAP", missing)
    monkeypatch.setattr(module, "LEGACY_LANGUAGE_MAP", legacy)
    catalog = module.load_language_map()
    assert catalog["Klingon"] == "tlh"
