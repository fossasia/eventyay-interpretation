"""SUSI provider choices for organizer forms (voxbento_translator)."""

from django.utils.translation import gettext_lazy as _

DEFAULT_SUSI_TRANSCRIPTION_PROVIDER = "faster_whisper"
DEFAULT_SUSI_TRANSLATION_PROVIDER = "nllb_ctranslate2"

# ponytail: map pre-release stored values; drop after first prod deploy.
_LEGACY_PROVIDER_ALIASES = {
    "whisper_local": DEFAULT_SUSI_TRANSCRIPTION_PROVIDER,
    "nllb_local": DEFAULT_SUSI_TRANSLATION_PROVIDER,
}

SUSI_TRANSCRIPTION_PROVIDERS = ((DEFAULT_SUSI_TRANSCRIPTION_PROVIDER, _("Faster Whisper (local)")),)

SUSI_TRANSLATION_PROVIDERS = ((DEFAULT_SUSI_TRANSLATION_PROVIDER, _("NLLB CTranslate2 (local)")),)


def resolve_transcription_provider(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return DEFAULT_SUSI_TRANSCRIPTION_PROVIDER
    return _LEGACY_PROVIDER_ALIASES.get(raw, raw)


def resolve_translation_provider(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return DEFAULT_SUSI_TRANSLATION_PROVIDER
    return _LEGACY_PROVIDER_ALIASES.get(raw, raw)
