import logging
from importlib.resources import files
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PACKAGE_LANGUAGE_MAP = files(__package__).joinpath("language_map.yml")
LEGACY_LANGUAGE_MAP = Path(__file__).resolve().parent.parent / "language_map.yml"


def iter_language_map_sources():
    yield PACKAGE_LANGUAGE_MAP
    yield LEGACY_LANGUAGE_MAP


def load_language_map():
    """Load name→code mappings shipped with the plugin.

    Prefer the packaged file so pip/uv installs work. Fall back to the old
    repository-root path so existing editable checkouts keep working.
    """
    last_error = None
    for source in iter_language_map_sources():
        try:
            with source.open("r", encoding="utf-8") as handle:
                catalog = yaml.safe_load(handle) or {}
        except FileNotFoundError as exc:
            last_error = exc
            continue
        except (OSError, yaml.YAMLError) as exc:
            last_error = exc
            continue
        if isinstance(catalog, dict):
            return catalog
    logger.error("Failed to load language_map.yml: %s", last_error)
    return {}


LANGUAGE_NAME_TO_CODE = load_language_map()


def language_code_for_name(name):
    cleaned = str(name).strip()
    return LANGUAGE_NAME_TO_CODE.get(cleaned, cleaned.lower())
