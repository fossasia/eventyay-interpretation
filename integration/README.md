# Eventyay video integration (language streams)

These files patch the Eventyay video SPA so attendee language streams can come
from the interpretation plugin when `interpretation_use_plugin_streams` is
enabled on the event.

## Apply to a local Eventyay checkout

From the Eventyay app root (directory containing `eventyay/webapp/video`):

```bash
PLUGIN_ROOT=/path/to/eventyay-interpretation

# Attendee room (language dropdown)
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/interpretation-streams.js" \
   eventyay/webapp/video/src/interpretation-streams.js
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/components/AudioTranslationDropdown.vue" \
   eventyay/webapp/video/src/components/AudioTranslationDropdown.vue
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/views/rooms/item.vue" \
   eventyay/webapp/video/src/views/rooms/item.vue

# Video admin (duplicate language/audio editor when plugin flag is on)
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/lib/interpretation-language-streams.js" \
   eventyay/webapp/video/src/lib/interpretation-language-streams.js
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/components/LanguageAudioSourceList.vue" \
   eventyay/webapp/video/src/components/LanguageAudioSourceList.vue
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/views/admin/rooms/types-edit/stage.vue" \
   eventyay/webapp/video/src/views/admin/rooms/types-edit/stage.vue
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/views/admin/rooms/StreamSchedule.vue" \
   eventyay/webapp/video/src/views/admin/rooms/StreamSchedule.vue
cp "$PLUGIN_ROOT/integration/eventyay/webapp/video/src/views/admin/rooms/EditForm.vue" \
   eventyay/webapp/video/src/views/admin/rooms/EditForm.vue
```

Requires `lib/interpretation-api.js` from Eventyay (already present on
interpretation-plugin branches).

Rebuilt or restart the video dev server after copying.

For a local Eventyay app, build into the path Django actually serves:

```bash
cd /path/to/eventyay/app
OUT_DIR="$(pwd)/eventyay/data/compiled-frontend/" npm run build --prefix=eventyay/webapp/video
EVY_RUNNING_ENVIRONMENT=development .venv/bin/python manage.py collectstatic --noinput
```

Django serves video assets from `static.dist/video/` (via `collectstatic`), not
from `webapp/video/dist/` directly.

## Behaviour

- **Flag off** (default): video admin shows only core **Languages and Audio
  Source**; attendee room uses core `languageUrls`.
- **Flag on**: video admin shows **two** sections — core unchanged plus
  **Languages and Audio Source (Interpretation plugin)**. Saving the room writes
  both core room config and plugin `language_streams`. Attendees use the plugin
  list for the dropdown.

The plugin injects stream data via a runtime hook on
`eventyay.base.services.event.get_room_config` — no Python changes in Eventyay
core are required beyond installing/upgrading the plugin.
