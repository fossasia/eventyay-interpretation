"""SUSI provider and caption language choices for organizer forms."""

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

CAPTION_LANGUAGE_CHOICES = (
    ("en", _("English")),
    ("de", _("German")),
    ("fr", _("French")),
    ("es", _("Spanish")),
    ("it", _("Italian")),
    ("pt", _("Portuguese")),
    ("nl", _("Dutch")),
    ("ja", _("Japanese")),
    ("ko", _("Korean")),
    ("zh", _("Chinese")),
)
