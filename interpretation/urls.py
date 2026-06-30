from django.urls import path
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .views import InterpretationDashboard


urlpatterns = [
    path(
        "common/event/<orgslug:organizer>/<slug:event>/interpretation/",
        InterpretationDashboard.as_view(),
        name="dashboard",
    ),
]
