from django.urls import path
from eventyay.api.urls import room_router
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .api import RoomInterpretationViewSet
from .views import InterpretationDashboard, InterpretationRoomSettings

room_router.register(
    "interpretation",
    RoomInterpretationViewSet,
    basename="room-interpretation",
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
        InterpretationRoomSettings.as_view(),
        name="rooms",
    ),
]
