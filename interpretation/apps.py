from django.utils.translation import gettext_lazy as _

from . import __version__

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
        from .operational_log import log_plugin_loaded

        log_plugin_loaded("interpretation")
        from . import signals  # NOQA
        from .video_integration import install_video_integration

        install_video_integration()
