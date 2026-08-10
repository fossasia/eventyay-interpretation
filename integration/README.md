# Eventyay video integration (language streams)

These files patch the Eventyay video SPA so attendee language streams come
from the interpretation plugin when `interpretation_use_plugin_streams` is
enabled on the event.

## Apply to a local Eventyay checkout

From the Eventyay app root (directory containing `eventyay/webapp/video`):

```bash
PLUGIN_ROOT=/path/to/eventyay-interpretation
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/interpretation-streams.js" \
   eventyay/webapp/video/src/interpretation-streams.js
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/views/rooms/item.vue" \
   eventyay/webapp/video/src/views/rooms/item.vue
```

Rebuild or restart the video dev server after copying.

## Behaviour

- **Flag off** (default): core `languageUrls` on the YouTube stage module — unchanged.
- **Flag on**: room config from the plugin (`interpretation_language_streams` on
  the world room object). Core URLs are ignored for the dropdown; one picker only.
- Playback still uses core `MediaSource` (YouTube swap + WHEP client).

The plugin injects stream data via a runtime hook on
`eventyay.base.services.event.get_room_config` — no Python changes in Eventyay
core are required beyond installing/upgrading the plugin.
