from django.db import models
from django.utils.translation import gettext_lazy as _
from eventyay.base.models import LoggedModel


class SusiConnection(LoggedModel):
    """Per-event connection settings for a SUSI Translator instance.

    A single SUSI server is configured per event. Individual rooms reference
    this connection and run their own transcription/translation session against
    it (see :class:`RoomInterpretation`).
    """

    event = models.OneToOneField(
        "base.Event",
        on_delete=models.CASCADE,
        related_name="susi_connection",
    )
    base_url = models.URLField(
        verbose_name=_("SUSI server URL"),
        help_text=_(
            "Base URL of the SUSI Translator Flask server, "
            "e.g. https://susi.example.com"
        ),
    )
    auth_token = models.TextField(
        verbose_name=_("Authentication token"),
        blank=True,
        help_text=_(
            "JWT or long-lived token used to authenticate against the SUSI "
            "server. Sent as a Bearer token; never exposed to attendees."
        ),
    )
    is_enabled = models.BooleanField(
        verbose_name=_("Enable interpretation"),
        default=False,
        help_text=_("Master switch for SUSI interpretation on this event."),
    )

    class Meta:
        verbose_name = _("SUSI connection")
        verbose_name_plural = _("SUSI connections")

    def __str__(self):
        return f"SusiConnection(event={self.event_id}, url={self.base_url})"

    @property
    def normalized_base_url(self):
        """Base URL without a trailing slash, for safe path joining."""
        return self.base_url.rstrip("/")


class RoomInterpretation(LoggedModel):
    """Interpretation configuration and session state for a single room.

    Each room maps to one SUSI transcription session, fed by the room's HLS
    stream and translated into the configured target languages.
    """

    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    STATUS_STOPPED = "stopped"
    STATUS_ERROR = "error"
    STATUS_CHOICES = (
        (STATUS_IDLE, _("Idle")),
        (STATUS_RUNNING, _("Running")),
        (STATUS_STOPPED, _("Stopped")),
        (STATUS_ERROR, _("Error")),
    )

    connection = models.ForeignKey(
        SusiConnection,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    room = models.OneToOneField(
        "base.Room",
        on_delete=models.CASCADE,
        related_name="interpretation",
    )
    hls_url = models.URLField(
        verbose_name=_("HLS stream URL"),
        blank=True,
        help_text=_(
            "HLS (.m3u8) URL of the room's audio/video stream that SUSI will "
            "ingest. Defaults from the room's stream configuration when empty."
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

    class Meta:
        verbose_name = _("Room interpretation")
        verbose_name_plural = _("Room interpretations")

    def __str__(self):
        return f"RoomInterpretation(room={self.room_id}, status={self.status})"
