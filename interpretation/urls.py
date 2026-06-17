from django.urls import path

# Registering the converter import side-effect lets us use <orgslug:...>
# in plugin URL patterns, exactly like the built-in plugins do.
from eventyay.common.urls import OrganizerSlugConverter  # noqa: F401

from .views import InterpretationDashboard

# eventyay auto-discovers this module (eventyay.multidomain.maindomain_urlconf
# iterates over every app config that has an ``EventyayPluginMeta`` and includes
# its ``urlpatterns`` under the ``plugins:<app_label>`` namespace).
#
# The path is mounted under ``/common/...`` on purpose: the eventyay_common
# context processor only builds the dashboard chrome (sidebar/nav) for requests
# whose path starts with ``/common``. Keeping our page there lets it render
# inside the same dashboard layout as the Settings/Plugins pages instead of the
# legacy control layout.
#
# Reverse name for the dashboard: ``plugins:interpretation:dashboard``.
urlpatterns = [
    path(
        "common/event/<orgslug:organizer>/<slug:event>/interpretation/",
        InterpretationDashboard.as_view(),
        name="dashboard",
    ),
]
