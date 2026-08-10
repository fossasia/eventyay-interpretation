from django.urls import path
from eventyay.api.urls import room_router
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .api import RoomInterpretationViewSet
from .views import (
    InterpretationCaptionPreview,
    InterpretationCaptionPreviewStream,
    InterpretationInterpreters,
    InterpretationOverview,
    InterpretationRoomCaptions,
    InterpretationRoomSettings,
)

room_router.register(
    "interpretation",
    RoomInterpretationViewSet,
    basename="room-interpretation",
)

_PREFIX = "common/event/<orgslug:organizer>/<slug:event>/interpretation/"

urlpatterns = [
    path(
        _PREFIX,
        InterpretationOverview.as_view(),
        name="dashboard",
    ),
    path(
        _PREFIX + "interpreters/",
        InterpretationInterpreters.as_view(),
        name="interpreters",
    ),
    path(
        _PREFIX + "rooms/",
        InterpretationRoomSettings.as_view(),
        name="rooms",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/preview/",
        InterpretationCaptionPreview.as_view(),
        name="room.preview",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/preview/stream/",
        InterpretationCaptionPreviewStream.as_view(),
        name="room.preview.stream",
    ),
    path(
        _PREFIX + "rooms/<int:pk>/captions/",
        InterpretationRoomCaptions.as_view(),
        name="room.captions",
    ),
]
