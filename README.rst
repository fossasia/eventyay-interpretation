Eventyay Interpretation
=======================

This repository contains the `eventyay`_ plugin for integrating the `fossasia/voxbento`_ live interpretation console.

It allows Eventyay organizers to seamlessly connect their events to a VoxBento instance, enabling live interpretation of video streams, language selection, and live captions.

Language streams: human and AI
------------------------------

Each language stream is either carried by a human interpreter in a VoxBento booth or spoken by
VoxBento's AI (TTS) voice. Entries therefore carry a ``stream_type`` of ``human`` or ``ai``::

    {"language": "German", "stream_type": "ai", "youtube_id": "", "use_video": false}

The field is optional on write and defaults to ``human``, so existing payloads keep working.
Anything other than ``human`` or ``ai`` is rejected with HTTP 400 by
``PATCH .../rooms/{room}/interpretation/config/``.

Changes API consumers need to know about:

* **AI entries carry no source of their own.** When ``stream_type`` is ``ai``, the plugin stores
  ``youtube_id`` as ``""`` and ``use_video`` as ``false``, discarding any value sent for them.
  The audio arrives over a WebSocket instead (see below).
* **AI entries reach players as** ``tts_ws_url``. In ``attendee_language_streams`` and in the video
  room config (``interpretation_language_streams``), an AI entry has a ``tts_ws_url`` pointing at
  VoxBento's ``/ws/tts/{booth_id}`` endpoint rather than a WHEP URL or YouTube id. Players must
  route those entries to that WebSocket; the eventyay video app does this in ``MediaSource.vue``.
* **AI entries are hidden until the room is synced.** A ``tts_ws_url`` can only be built once
  VoxBento has returned a room id, so AI languages are omitted from the attendee list until the
  event has a live VoxBento connection and the room has been synced.
* **The VoxBento room sync payload is split.** ``target_languages`` now lists human languages only,
  and AI languages are sent in a new ``ai_languages`` key. A VoxBento instance without AI booth
  support (see `fossasia/voxbento#496 <https://github.com/fossasia/voxbento/pull/496>`_) ignores
  ``ai_languages``, so any language switched to AI is treated as removed and its interpreter booth
  is deleted. Deploy the matching VoxBento version before marking languages as AI.

Development setup
-----------------

1. Make sure that you have a working `eventyay development setup`_.

2. Clone this repository, e.g., to ``local/eventyay-interpretation``.

3. Activate the `virtual environment <https://github.com/fossasia/eventyay?tab=readme-ov-file#getting-started>`_ you use for eventyay development.

4. Execute ``uv pip install -e .`` within this directory to register this application with the eventyay plugin registry.

5. Execute ``make`` within this directory to compile translations.

6. Restart your local eventyay server. You can now use the plugin from this repository for your events by enabling it in
   the 'plugins' tab in the settings.

This plugin has CI set up to enforce a few code style rules. To check locally, you need ruff installed::

    pip install ruff

To check your plugin for rule violations, run::

    ruff check .
    ruff format --check .

You can auto-fix many of these issues by running::

    ruff check . --fix
    ruff format .

To automatically check for these issues before you commit, you can run ``.install-hooks``.


License
-------


Copyright 2026 FOSSASIA

Released under the terms of the Apache License 2.0



.. _eventyay: https://github.com/fossasia/eventyay
.. _fossasia/voxbento: https://github.com/fossasia/voxbento
.. _eventyay development setup: https://github.com/fossasia/eventyay?tab=readme-ov-file#getting-started
