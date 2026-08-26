from importlib.resources import files

from interpretation.tasks import LANGUAGE_MAP_RESOURCE, LANGUAGE_NAME_TO_CODE, _load_language_map


def test_language_map_is_packaged_with_plugin():
    assert files("interpretation").joinpath("language_map.yml").is_file()
    assert LANGUAGE_MAP_RESOURCE.is_file()


def test_language_map_loads_english_and_german():
    catalog = _load_language_map()
    assert catalog["English"] == "en"
    assert catalog["German"] == "de"
    assert LANGUAGE_NAME_TO_CODE["English"] == "en"
    assert LANGUAGE_NAME_TO_CODE["German"] == "de"
