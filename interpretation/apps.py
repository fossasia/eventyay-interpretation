from django.utils.translation import gettext_lazy as _

from . import __version__
from .operational_log import OUTCOME_FAILURE, log_operation, log_plugin_loaded

try:
    from eventyay.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use a later version of eventyay-tickets")


class InterpretationApp(PluginConfig):
    default = True
    name = "interpretation"
    verbose_name = _("Interpretation")

    class EventyayPluginMeta:
        name = _("Interpretation")
        author = "FOSSASIA"
        description = _("A plugin for live interpretation of video streams")
        visible = True
        version = __version__
        category = "FEATURE"

    def ready(self):
        try:
            from . import signals  # NOQA
            from .video_integration import install_video_integration

            install_video_integration()
        except Exception:
            log_operation(
                "plugin.load",
                OUTCOME_FAILURE,
                backend="interpretation",
                error_code="startup_failed",
            )
            raise
        log_plugin_loaded("interpretation")
