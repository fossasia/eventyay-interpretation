import logging

import requests

logger = logging.getLogger(__name__)


import hashlib

from django.core.cache import cache


def validate_provider_key(provider: str, api_key: str) -> bool:
    """
    Validates a third-party AI provider API key by making a lightweight
    authenticated request to their models or auth endpoint.
    Returns True if valid (or if we can't definitively prove it's invalid due to network).
    Returns False if the provider explicitly rejects the key (401/403).
    """
    if not api_key:
        return False

    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    cache_key = f"interpretation_api_key_{provider}_{key_hash}"

    cached_result = cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        resp = None
        if provider in ["openai", "translation_openai"]:
            resp = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
        elif provider == "deepgram":
            resp = requests.get(
                "https://api.deepgram.com/v1/projects",
                headers={"Authorization": f"Token {api_key}"},
                timeout=5.0,
            )
        elif provider == "nvidia":
            resp = requests.get(
                "https://integrate.api.nvidia.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
        elif provider == "elevenlabs":
            resp = requests.get(
                "https://api.elevenlabs.io/v1/models",
                headers={"xi-api-key": api_key},
                timeout=5.0,
            )
        elif provider == "openrouter":
            resp = requests.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
        elif provider == "gemini":
            resp = requests.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key},
                timeout=5.0,
            )
        elif provider == "anthropic":
            resp = requests.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=5.0,
            )
        elif provider == "groq":
            resp = requests.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )

        if resp is not None:
            # Explicit auth rejection
            if resp.status_code in (401, 403, 400):
                cache.set(cache_key, False, timeout=300)
                return False
            # Valid response
            if 200 <= resp.status_code < 300:
                cache.set(cache_key, True, timeout=300)
                return True
            # Transient failures (rate limits, server errors) - fail open so users aren't locked out
            if resp.status_code == 429 or resp.status_code >= 500:
                cache.set(cache_key, True, timeout=300)
                return True
            # For 404s or other unexpected client errors, assume invalid endpoint/config
            cache.set(cache_key, False, timeout=300)
            return False

    except requests.RequestException as e:
        logger.warning(f"Failed to reach {provider} API for key validation: {e}")
        # Default to True on network error to avoid blocking the user from saving
        # Cache for a shorter time on network error
        cache.set(cache_key, True, timeout=60)
        return True

    cache.set(cache_key, True, timeout=300)
    return True
