from interpretation.utils import normalize_target_languages


def test_normalize_target_languages_from_comma_string():
    assert normalize_target_languages("de, fr, de, es") == ["de", "fr", "es"]


def test_normalize_target_languages_from_list():
    assert normalize_target_languages(["de", "fr"]) == ["de", "fr"]


def test_normalize_target_languages_empty():
    assert normalize_target_languages("") == []
    assert normalize_target_languages([]) == []
