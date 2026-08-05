"""SUSI provider choices for organizer forms."""

from django.utils.translation import gettext_lazy as _

# ponytail: static list; upgrade path is SUSI /providers API when exposed.
SUSI_TRANSCRIPTION_PROVIDERS = (
    ("whisper_local", _("Whisper (local)")),
    ("openai", _("OpenAI")),
    ("deepgram", _("Deepgram")),
)

SUSI_TRANSLATION_PROVIDERS = (
    ("nllb_local", _("NLLB (local)")),
    ("openai", _("OpenAI")),
)
