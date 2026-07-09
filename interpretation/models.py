from django.db import models
from django.utils.translation import gettext_lazy as _
from eventyay.base.models import LoggedModel


class RoomInterpretation(LoggedModel):
    """Interpretation configuration and session state for a single room.

    Each room maps to one SUSI transcription session, fed by the room's
    stream URL and translated into the configured target languages. Per-event SUSI
    connection settings live in the event settings backend (see
    :mod:`interpretation.settings`).
    """

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
    stream_url = models.URLField(
        verbose_name=_("Stream URL"),
        blank=True,
        help_text=_(
            "Stream URL that SUSI will ingest (YouTube, HLS, Vimeo, …). "
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
    susi_session_id = models.CharField(
        verbose_name=_("SUSI session/tenant ID"),
        max_length=64,
        blank=True,
        help_text=_("Tenant ID returned by SUSI when the session starts."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_IDLE,
    )
    room_enabled = models.BooleanField(
        verbose_name=_("Interpretation enabled for room"),
        default=False,
        help_text=_(
            "When enabled, interpretation controls and the caption bar are "
            "shown for this room."
        ),
    )

    class Meta:
        verbose_name = _("Room interpretation")
        verbose_name_plural = _("Room interpretations")

    def __str__(self):
        return f"RoomInterpretation(room={self.room_id}, status={self.status})"
