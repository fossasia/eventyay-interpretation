from django.utils.translation import gettext_lazy as _

from . import __version__

try:
    from eventyay.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use a later version of eventyay-tickets")


def _configure_logging():
    """Attach a console handler for interpretation caption/SUSI logs."""
    import logging
    import sys

    from django.conf import settings

    level = logging.DEBUG if settings.DEBUG else logging.INFO
    logger = logging.getLogger("interpretation")
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


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
        _configure_logging()
        from . import signals  # NOQA
