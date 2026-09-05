from django.urls import path
from eventyay.api.urls import room_router
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .api import RoomInterpretationViewSet
from .views import (
    InterpretationInterpreters,
    InterpretationOverview,
    InterpretationRoomSettings,
)
from .views_oauth import VoxbentoOAuthCallbackView, VoxbentoOAuthConnectView
from .views_webhooks import VoxbentoWebhookReceiverView

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
        _PREFIX + "voxbento/connect/",
        VoxbentoOAuthConnectView.as_view(),
        name="oauth_connect",
    ),
    path(
        "interpretation/voxbento/oauth/callback/",
        VoxbentoOAuthCallbackView.as_view(),
        name="oauth_callback",
    ),
    path(
        "interpretation/voxbento/webhook/",
        VoxbentoWebhookReceiverView.as_view(),
        name="voxbento_webhook",
    ),
]
