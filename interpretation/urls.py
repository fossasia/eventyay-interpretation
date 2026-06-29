from django.urls import path
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .views import (
    InterpretationDashboard,
    InterpretationRoomConfig,
    InterpretationRoomList,
    InterpretationRoomStart,
    InterpretationRoomStatus,
    InterpretationRoomStop,
    InterpretationRoomTranscript,
)

_PREFIX = "common/event/<orgslug:organizer>/<slug:event>/interpretation/"

urlpatterns = [
    path(
        _PREFIX,
        InterpretationDashboard.as_view(),
        name="dashboard",
    ),
    path(
        _PREFIX + "rooms/",
        InterpretationRoomList.as_view(),
        name="rooms",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/",
        InterpretationRoomConfig.as_view(),
        name="room.config",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/start/",
        InterpretationRoomStart.as_view(),
        name="room.start",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/stop/",
        InterpretationRoomStop.as_view(),
        name="room.stop",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/status/",
        InterpretationRoomStatus.as_view(),
        name="room.status",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/transcript/",
        InterpretationRoomTranscript.as_view(),
        name="room.transcript",
    ),
]
