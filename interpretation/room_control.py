"""Shared per-room interpretation helpers for commons views and video admin API."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from .backends import (
    get_backend,
    is_known_interpreter,
    list_available_interpreters,
)
from .interpreter_credentials import (
    SUSI_CREDENTIAL_KEYS,
    strip_room_credential_keys,
)
from .language_streams import (
    attendee_language_streams,
    validate_language_streams,
)
from .models import RoomInterpretation
from .settings import is_interpretation_enabled, use_plugin_language_streams
from .susi import SusiError
from .utils import (
    get_room_stream_url,
    interpretation_dashboard_url,
    normalize_target_languages,
    validate_backend_config,
    validate_target_language_codes,
)

PLUGIN_MODULE = "interpretation"


def _parse_bool(val) -> bool:
    if isinstance(val, str):
        return val.lower() in ("true", "t", "1", "yes", "y", "on")
    return bool(val)


def notify_video_room_config_changed(event) -> None:
    """Push event.updated so video SPA reloads room config (plugin stream fields)."""
    try:
        from asgiref.sync import async_to_sync
        from eventyay.base.services.event import notify_event_change

        async_to_sync(notify_event_change)(event.id)
    except Exception:
        pass


def plugin_enabled(event) -> bool:
    return PLUGIN_MODULE in event.get_plugins()


def get_interpretation(room) -> RoomInterpretation | None:
    return RoomInterpretation.objects.filter(room=room).first()


def normalize_session_status(status: str) -> str:
    """Map legacy stopped/error rows to idle for display and API."""
    if status == RoomInterpretation.STATUS_RUNNING:
        return RoomInterpretation.STATUS_RUNNING
    return RoomInterpretation.STATUS_IDLE


def _public_session_id(interpretation: RoomInterpretation | None) -> str:
    if interpretation is None:
        return ""
    status = normalize_session_status(interpretation.status)
    if status != RoomInterpretation.STATUS_RUNNING:
        return ""
    return interpretation.backend_session_id or ""


def is_room_interpretation_ready(room, event, interpretation: RoomInterpretation | None = None) -> bool:
    if interpretation is None:
        interpretation = get_interpretation(room)
    if interpretation is None or not interpretation.room_enabled:
        return False
    if interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE:
        return False
    return get_backend(interpretation.interpreter).is_configured(event)


def _public_backend_config(interpretation: RoomInterpretation | None) -> dict:
    if interpretation is None:
        return {}
    return strip_room_credential_keys(interpretation.backend_config)



def get_language_display_names(codes):
    lang_map = {"ab": "Abkhaz", "aa": "Afar", "af": "Afrikaans", "ak": "Akan", "sq": "Albanian", "am": "Amharic", "ar": "Arabic", "an": "Aragonese", "hy": "Armenian", "as": "Assamese", "av": "Avaric", "ae": "Avestan", "ay": "Aymara", "az": "Azerbaijani", "bm": "Bambara", "ba": "Bashkir", "eu": "Basque", "be": "Belarusian", "bn": "Bengali", "bi": "Bislama", "bs": "Bosnian", "br": "Breton", "bg": "Bulgarian", "my": "Burmese", "ca": "Catalan", "ch": "Chamorro", "ce": "Chechen", "ny": "Chichewa", "zh": "Chinese", "cv": "Chuvash", "kw": "Cornish", "co": "Corsican", "cr": "Cree", "hr": "Croatian", "cs": "Czech", "da": "Danish", "dv": "Divehi", "nl": "Dutch", "dz": "Dzongkha", "en": "English", "eo": "Esperanto", "et": "Estonian", "ee": "Ewe", "fo": "Faroese", "fj": "Fijian", "fi": "Finnish", "fr": "French", "ff": "Fula", "gl": "Galician", "lg": "Ganda", "ka": "Georgian", "de": "German", "el": "Greek", "gn": "Guaran\u00ed", "gu": "Gujarati", "ht": "Haitian", "ha": "Hausa", "he": "Hebrew", "hz": "Herero", "hi": "Hindi", "ho": "Hiri Motu", "hu": "Hungarian", "is": "Icelandic", "io": "Ido", "ig": "Igbo", "id": "Indonesian", "ia": "Interlingua", "ie": "Interlingue", "iu": "Inuktitut", "ik": "Inupiaq", "ga": "Irish", "it": "Italian", "ja": "Japanese", "jv": "Javanese", "kl": "Kalaallisut", "kn": "Kannada", "kr": "Kanuri", "ks": "Kashmiri", "kk": "Kazakh", "km": "Khmer", "ki": "Kikuyu", "rw": "Kinyarwanda", "rn": "Kirundi", "kv": "Komi", "kg": "Kongo", "ko": "Korean", "ku": "Kurdish", "kj": "Kwanyama", "ky": "Kyrgyz", "lo": "Lao", "la": "Latin", "lv": "Latvian", "li": "Limburgish", "ln": "Lingala", "lt": "Lithuanian", "lu": "Luba-Katanga", "lb": "Luxembourgish", "mi": "M\u0101ori", "mk": "Macedonian", "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam", "mt": "Maltese", "gv": "Manx", "mr": "Marathi", "mh": "Marshallese", "mn": "Mongolian", "na": "Nauru", "nv": "Navajo", "ng": "Ndonga", "ne": "Nepali", "nd": "Northern Ndebele", "se": "Northern Sami", "no": "Norwegian", "nb": "Norwegian Bokm\u00e5l", "nn": "Norwegian Nynorsk", "ii": "Nuosu", "oc": "Occitan", "oj": "Ojibwe", "cu": "Old Church Slavonic", "or": "Oriya", "om": "Oromo", "os": "Ossetian", "pi": "P\u0101li", "pa": "Panjabi", "ps": "Pashto", "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "qu": "Quechua", "ro": "Romanian", "rm": "Romansh", "ru": "Russian", "sm": "Samoan", "sg": "Sango", "sa": "Sanskrit", "sc": "Sardinian", "gd": "Scottish Gaelic", "sr": "Serbian", "sn": "Shona", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali", "nr": "Southern Ndebele", "st": "Southern Sotho", "es": "Spanish", "su": "Sundanese", "sw": "Swahili", "ss": "Swati", "sv": "Swedish", "tl": "Tagalog", "ty": "Tahitian", "tg": "Tajik", "ta": "Tamil", "tt": "Tatar", "te": "Telugu", "th": "Thai", "bo": "Tibetan", "ti": "Tigrinya", "to": "Tonga", "ts": "Tsonga", "tn": "Tswana", "tr": "Turkish", "tk": "Turkmen", "tw": "Twi", "uk": "Ukrainian", "ur": "Urdu", "ug": "Uyghur", "uz": "Uzbek", "ve": "Venda", "vi": "Vietnamese", "vo": "Volap\u00fck", "wa": "Walloon", "cy": "Welsh", "fy": "Western Frisian", "wo": "Wolof", "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "za": "Zhuang", "zu": "Zulu"}
    return [lang_map.get(c, c.title()) for c in codes]

def serialize_room_interpretation(room, event, interpretation=None) -> dict:
    if interpretation is None:
        interpretation = get_interpretation(room)
    detected_stream_url = get_room_stream_url(room)
    stream_url = ""
    interpreter = RoomInterpretation.INTERPRETER_NONE
    room_enabled = False
    if interpretation:
        stream_url = interpretation.stream_url or ""
        interpreter = interpretation.interpreter
        room_enabled = interpretation.room_enabled
    backend = get_backend(interpreter)
    stored_language_streams = list(getattr(interpretation, "language_streams", None) or []) if interpretation else []
    return {
        "ui_sync_supported": True,
        "interpreter": interpreter,
        "interpreter_label": str(backend.label),
        "room_enabled": room_enabled,
        "interpreter_ready": is_room_interpretation_ready(room, event, interpretation),
        "available_interpreters": list_available_interpreters(event),
        "target_languages": list(interpretation.target_languages or []) if interpretation else [],
        "target_languages_display": get_language_display_names(interpretation.target_languages or []) if interpretation else [],
        "transcription_provider": interpretation.transcription_provider if interpretation else "",
        "transcription_model": interpretation.transcription_model if interpretation else "",
        "enable_transcription": interpretation.enable_transcription if interpretation else False,
        "translation_provider": interpretation.translation_provider if interpretation else "",
        "translation_model": interpretation.translation_model if interpretation else "",
        "enable_translation": interpretation.enable_translation if interpretation else False,
        "source_language": interpretation.source_language if interpretation else "",
        "backend_config": _public_backend_config(interpretation),
        "status": normalize_session_status(interpretation.status if interpretation else RoomInterpretation.STATUS_IDLE),
        "session_id": _public_session_id(interpretation),
        "stream_url": stream_url or detected_stream_url,
        "detected_stream_url": detected_stream_url,
        "language_streams": stored_language_streams,
        "attendee_language_streams": attendee_language_streams(stored_language_streams, event=event, room=room),
        "use_plugin_language_streams": use_plugin_language_streams(event),
        "plugin_enabled": plugin_enabled(event),
        "dashboard_url": interpretation_dashboard_url(event.organizer.slug, event.slug),
    }


def _merge_public_backend_config(interpretation: RoomInterpretation, incoming: dict) -> dict:
    """Merge non-credential backend_config keys; credentials are sign-in only."""
    config = strip_room_credential_keys(interpretation.backend_config)
    for key, value in validate_backend_config(incoming).items():
        if key in SUSI_CREDENTIAL_KEYS:
            continue
        config[key] = value
    return config


def _apply_backend_config(interpretation: RoomInterpretation, data: dict) -> None:
    if "backend_config" in data:
        interpretation.backend_config = _merge_public_backend_config(
            interpretation,
            data["backend_config"],
        )
    if "enable_transcription" in data:
        interpretation.enable_transcription = _parse_bool(data.get("enable_transcription"))
    if "transcription_provider" in data:
        interpretation.transcription_provider = (data.get("transcription_provider") or "").strip()
    if "transcription_model" in data:
        interpretation.transcription_model = (data.get("transcription_model") or "").strip()
    if "source_language" in data:
        interpretation.source_language = (data.get("source_language") or "").strip()
    if "enable_translation" in data:
        interpretation.enable_translation = _parse_bool(data.get("enable_translation"))
    if "translation_provider" in data:
        interpretation.translation_provider = (data.get("translation_provider") or "").strip()
    if "translation_model" in data:
        interpretation.translation_model = (data.get("translation_model") or "").strip()


def update_room_interpretation(room, event, data: dict) -> RoomInterpretation:
    if hasattr(data, "copy"):
        data = data.copy()

    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    was_running = bool(interpretation.backend_session_id)
    old_interpreter = interpretation.interpreter

    if "interpreter" in data:
        interpreter = (data.get("interpreter") or RoomInterpretation.INTERPRETER_NONE).strip()
        if not is_known_interpreter(interpreter):
            raise ValueError(_("Unknown interpreter."))
        interpretation.interpreter = interpreter

    changed_room_enabled = False
    if "room_enabled" in data:
        new_room_enabled = _parse_bool(data.get("room_enabled"))
        if interpretation.room_enabled != new_room_enabled:
            changed_room_enabled = True
        interpretation.room_enabled = new_room_enabled

    if interpretation.room_enabled and interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE:
        changed_interpreter = "interpreter" in data and interpretation.interpreter != old_interpreter
        if changed_room_enabled or changed_interpreter:
            raise ValueError(_("An interpreter must be selected to enable interpretation for this room."))

    # Validation: Enforce AI Configuration Invariants
    trans_enabled = _parse_bool(data.get("enable_transcription", interpretation.enable_transcription))
    trans_provider = (data.get("transcription_provider", interpretation.transcription_provider) or "").strip()
    trans_model = (data.get("transcription_model", interpretation.transcription_model) or "").strip()

    transl_enabled = _parse_bool(data.get("enable_translation", interpretation.enable_translation))
    transl_provider = (data.get("translation_provider", interpretation.translation_provider) or "").strip()
    transl_model = (data.get("translation_model", interpretation.translation_model) or "").strip()

    if trans_enabled:
        if not trans_provider:
            raise ValueError(_("Transcription provider is required when transcription is enabled."))
        if not trans_model:
            raise ValueError(_("Transcription model is required when transcription is enabled."))
    else:
        # Force clear them if the feature is disabled
        data["transcription_provider"] = ""
        data["transcription_model"] = ""

    if transl_enabled:
        if not trans_enabled:
            raise ValueError(_("Floor Audio Transcription must be enabled to use translation."))
        if not transl_provider:
            raise ValueError(_("Translation provider is required when translation is enabled."))
        if not transl_model:
            raise ValueError(_("Translation model is required when translation is enabled."))
    else:
        # Force clear them if the feature is disabled
        data["translation_provider"] = ""
        data["translation_model"] = ""

    if "target_languages" in data:
        interpretation.target_languages = validate_target_language_codes(
            normalize_target_languages(data.get("target_languages"))
        )

    if "language_streams" in data:
        try:
            validated_streams = validate_language_streams(data.get("language_streams"))
            interpretation.language_streams = validated_streams
            if interpretation.interpreter == RoomInterpretation.INTERPRETER_VOXBENTO:
                from .language_map import language_code_for_name

                langs = [
                    language_code_for_name(s["language"]) for s in validated_streams if s["language"] != "Original"
                ]
                interpretation.target_languages = [lang_code for lang_code in langs if lang_code]
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    _apply_backend_config(interpretation, data)
    interpretation.save()

    changed_interpreter = "interpreter" in data and interpretation.interpreter != old_interpreter
    needs_broadcast = changed_room_enabled or changed_interpreter or "language_streams" in data

    if needs_broadcast:
        transaction.on_commit(lambda: notify_video_room_config_changed(event))

    if was_running and (
        not interpretation.room_enabled
        or interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE
        or interpretation.interpreter != old_interpreter
    ):
        result = stop_room_session(room, event)
        interpretation.refresh_from_db()
        if not result.ok:
            raise ValueError(result.error)

    return interpretation


@dataclass
class SessionResult:
    ok: bool
    error: str = ""
    warning: str = ""
    interpretation: RoomInterpretation | None = None


def start_room_session(room, event, *, stream_url_override: str = "") -> SessionResult:
    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)

    if not is_interpretation_enabled(event):
        return SessionResult(
            ok=False,
            error=str(_("Live interpretation is turned off for this event.")),
            interpretation=interpretation,
        )

    if not interpretation.room_enabled:
        return SessionResult(
            ok=False,
            error=str(_("Interpretation is disabled for this room.")),
            interpretation=interpretation,
        )

    if interpretation.interpreter == RoomInterpretation.INTERPRETER_NONE:
        return SessionResult(
            ok=False,
            error=str(_("Select an interpreter for this room before starting.")),
            interpretation=interpretation,
        )

    backend = get_backend(interpretation.interpreter)
    if not backend.is_configured(event):
        return SessionResult(
            ok=False,
            error=str(_("Configure %(name)s under Configure interpreters before starting.") % {"name": backend.label}),
            interpretation=interpretation,
        )

    override = (stream_url_override or "").strip()
    stream_url = override or interpretation.stream_url or get_room_stream_url(room)
    if not stream_url:
        return SessionResult(
            ok=False,
            error=str(_("No stream URL is configured for this room.")),
            interpretation=interpretation,
        )

    if (
        interpretation.backend_session_id
        and normalize_session_status(interpretation.status) == RoomInterpretation.STATUS_RUNNING
    ):
        return SessionResult(ok=True, interpretation=interpretation)

    try:
        session_id = backend.start(event, interpretation, stream_url=stream_url)
    except (SusiError, ValueError) as exc:
        interpretation.status = normalize_session_status(RoomInterpretation.STATUS_IDLE)
        interpretation.backend_session_id = ""
        interpretation.stream_url = stream_url
        interpretation.save()
        return SessionResult(ok=False, error=str(exc), interpretation=interpretation)

    interpretation.backend_session_id = session_id
    interpretation.stream_url = stream_url
    interpretation.status = normalize_session_status(RoomInterpretation.STATUS_RUNNING)
    interpretation.save()
    if hasattr(interpretation, "log_action"):
        interpretation.log_action(
            "interpretation.room.started",
            data={
                "interpreter": interpretation.interpreter,
                "session_id": session_id,
                "stream_url": stream_url,
            },
        )
    return SessionResult(ok=True, interpretation=interpretation)


def clear_room_interpretation_setup(room, event) -> RoomInterpretation:
    """Stop this room's session and reset interpreter selection."""
    interpretation, _created = RoomInterpretation.objects.get_or_create(room=room)
    if interpretation.backend_session_id:
        stop_room_session(room, event)
        interpretation.refresh_from_db()
    return update_room_interpretation(
        room,
        event,
        {
            "interpreter": RoomInterpretation.INTERPRETER_NONE,
            "room_enabled": False,
            "enable_transcription": False,
            "transcription_provider": "",
            "transcription_model": "",
            "source_language": "",
            "enable_translation": False,
            "translation_provider": "",
            "translation_model": "",
            "target_languages": [],
            "target_languages_display": [],
            "language_streams": [],
        },
    )


def _clear_local_session(interpretation: RoomInterpretation, *, session_id: str = "") -> None:
    if hasattr(interpretation, "log_action") and session_id:
        interpretation.log_action(
            "interpretation.room.stopped",
            data={
                "interpreter": interpretation.interpreter,
                "session_id": session_id,
            },
        )
    interpretation.status = normalize_session_status(RoomInterpretation.STATUS_IDLE)
    interpretation.backend_session_id = ""
    interpretation.save()


def stop_room_session(room, event) -> SessionResult:
    interpretation = get_interpretation(room)
    if interpretation is None or not interpretation.backend_session_id:
        return SessionResult(
            ok=False,
            error=str(_("No running interpretation session for this room.")),
        )

    backend = get_backend(interpretation.interpreter)
    session_id = interpretation.backend_session_id
    remote_error = ""
    try:
        backend.stop(event, interpretation)
    except SusiError as exc:
        remote_error = str(exc)

    _clear_local_session(interpretation, session_id=session_id)
    if remote_error:
        return SessionResult(
            ok=True,
            warning=str(
                _("Stopped interpretation for this room locally, but the interpreter backend reported: %(error)s")
                % {"error": remote_error}
            ),
            interpretation=interpretation,
        )
    return SessionResult(ok=True, interpretation=interpretation)


def stop_all_event_sessions(event) -> None:
    """Stop every room session for an event (e.g. when interpretation is disabled)."""
    interpretations = (
        RoomInterpretation.objects.filter(room__event=event).exclude(backend_session_id="").select_related("room")
    )
    for interpretation in interpretations:
        stop_room_session(interpretation.room, event)
