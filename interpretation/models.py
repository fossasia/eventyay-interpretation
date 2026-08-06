from django.db import models
from django.utils.translation import gettext_lazy as _
from eventyay.base.models import LoggedModel


class RoomInterpretation(LoggedModel):
    """Per-room interpretation configuration and session state.

    Each room selects its own interpreter backend, stores its own backend
    credentials in ``backend_config``, and runs sessions independently.
    Event-level settings only store the feature toggle (see
    :mod:`interpretation.settings`).
    """

    INTERPRETER_NONE = "none"
    INTERPRETER_SUSI = "susi"
    INTERPRETER_CHOICES = (
        (INTERPRETER_NONE, _("None")),
        (INTERPRETER_SUSI, _("SUSI Translator")),
    )

    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    # Legacy DB values; normalized to idle on read/write.
    STATUS_STOPPED = "stopped"
    STATUS_ERROR = "error"
    STATUS_CHOICES = (
        (STATUS_IDLE, _("Idle")),
        (STATUS_RUNNING, _("Running")),
    )

    room = models.OneToOneField(
        "base.Room",
        on_delete=models.CASCADE,
        related_name="interpretation",
    )
    interpreter = models.CharField(
        verbose_name=_("Interpreter"),
        max_length=32,
        choices=INTERPRETER_CHOICES,
        default=INTERPRETER_NONE,
    )
    room_enabled = models.BooleanField(
        verbose_name=_("Interpretation enabled for room"),
        default=False,
        help_text=_(
            "When enabled, this room can run interpretation using the "
            "selected interpreter."
        ),
    )
    stream_url = models.URLField(
        verbose_name=_("Stream URL"),
        blank=True,
        help_text=_(
            "Stream URL that the interpreter will ingest (YouTube, HLS, Vimeo, …). "
            "Defaults from the room configuration when empty."
        ),
    )
    source_language = models.CharField(
        verbose_name=_("Source language"),
        max_length=20,
        blank=True,
        help_text=_("Spoken language of the stream, e.g. 'en'."),
    )
    target_languages = models.JSONField(
        verbose_name=_("Target languages"),
        default=list,
        blank=True,
        help_text=_("Languages to translate into, e.g. ['de', 'fr']."),
    )
    transcription_provider = models.CharField(
        verbose_name=_("Transcription provider"),
        max_length=50,
        blank=True,
    )
    translation_provider = models.CharField(
        verbose_name=_("Translation provider"),
        max_length=50,
        blank=True,
    )
    backend_config = models.JSONField(
        verbose_name=_("Backend config"),
        default=dict,
        blank=True,
    )
    backend_session_id = models.CharField(
        verbose_name=_("Backend session ID"),
        max_length=64,
        blank=True,
        help_text=_("Session/tenant ID returned by the active interpreter backend."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_IDLE,
    )

    class Meta:
        verbose_name = _("Room interpretation")
        verbose_name_plural = _("Room interpretations")

    def __str__(self):
        return (
            f"RoomInterpretation(room={self.room_id}, "
            f"interpreter={self.interpreter}, status={self.status})"
        )
