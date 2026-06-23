"""Thin HTTP client for the SUSI Translator Flask API.
The SUSI server is a separate Flask service (fossasia/susi_translator). 

Relevant SUSI endpoints used here:
    GET  /auth/api/status     -> {"authenticated": bool, "email", "name"}
    POST /auth/api/login      -> sets JWT cookie, body {status,email,name}
    POST /session             -> {"tenant_id", "source"}
    POST /api/v1/translate/configure
    GET  /api/v1/translate/status/<tenant_id>
    POST /stop_event/<tenant_id>
"""

from __future__ import annotations

import requests
from dataclasses import dataclass
from urllib.parse import urljoin

DEFAULT_TIMEOUT = 10


class SusiError(Exception):
    """Raised when the SUSI server returns an error or is unreachable."""


@dataclass
class SusiResult:
    ok: bool
    status_code: int | None
    data: dict
    message: str = ""


class SusiClient:
    """Minimal client for talking to a SUSI Translator server."""

    def __init__(
        self, base_url: str, auth_token: str = "", timeout: int = DEFAULT_TIMEOUT
    ):
        if not base_url:
            raise ValueError("base_url is required")
        # Ensure a single trailing slash so urljoin treats it as a directory.
        self.base_url = base_url.rstrip("/") + "/"
        self.auth_token = auth_token or ""
        self.timeout = timeout

    # -- internals -------------------------------------------------------

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def _request(self, method: str, path: str, **kwargs) -> SusiResult:
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", self._headers())
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise SusiError(f"Could not reach SUSI server at {url}: {exc}") from exc

        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        return SusiResult(
            ok=resp.ok,
            status_code=resp.status_code,
            data=data if isinstance(data, dict) else {"result": data},
        )

    # -- auth / health ---------------------------------------------------

    def verify(self) -> SusiResult:
        """Check reachability and token validity via ``/auth/api/status``.

        Returns a :class:`SusiResult` with ``ok=True`` only when the server is
        reachable AND the configured token authenticates successfully.
        """
        result = self._request("GET", "/auth/api/status")
        if result.status_code is None or result.status_code >= 500:
            result.ok = False
            result.message = "SUSI server error or unreachable."
            return result

        authenticated = bool(result.data.get("authenticated"))
        if not self.auth_token:
            result.ok = False
            result.message = "Server reachable but no authentication token configured."
        elif authenticated:
            result.ok = True
            result.message = "Connected and authenticated."
        else:
            result.ok = False
            result.message = "Server reachable but token is invalid or expired."
        return result

    def login(self, email: str, password: str) -> str:
        """Authenticate with email/password and return the JWT access token.
        stored and reused as a Bearer token.
        """
        url = self._url("/auth/api/login")
        try:
            resp = requests.post(
                url,
                json={"email": email, "password": password},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SusiError(f"Could not reach SUSI server at {url}: {exc}") from exc

        if not resp.ok:
            raise SusiError("Invalid SUSI credentials or server rejected login.")

        token = resp.cookies.get("access_token_cookie")
        if not token:
            raise SusiError("Login succeeded but no access token was returned.")
        self.auth_token = token
        return token

    # -- lifecycle ---------------------------------------------------

    def create_session(self, source: str = "url") -> str:
        """Mint a tenant/session ID for a given audio source."""
        result = self._request("POST", "/session", json={"source": source})
        if not result.ok:
            raise SusiError(f"Failed to create SUSI session: {result.data}")
        tenant_id = result.data.get("tenant_id")
        if not tenant_id:
            raise SusiError("SUSI did not return a tenant_id.")
        return tenant_id

    def configure(
        self,
        tenant_id: str,
        *,
        stream_url: str = "",
        source_type: str = "url",
        transcription: dict | None = None,
        translation: dict | None = None,
    ) -> SusiResult:
        """Configure providers for a tenant and optionally start the grabber."""
        payload: dict = {"tenant_id": tenant_id}
        if transcription:
            payload["transcription"] = transcription
        if translation:
            payload["translation"] = translation
        if stream_url:
            payload["stream_url"] = stream_url
            payload["source_type"] = source_type
        result = self._request("POST", "/api/v1/translate/configure", json=payload)
        if not result.ok:
            raise SusiError(f"Failed to configure SUSI tenant: {result.data}")
        return result

    def session_status(self, tenant_id: str) -> SusiResult:
        """Poll whether a tenant's models are warmed up and ready."""
        return self._request("GET", f"/api/v1/translate/status/{tenant_id}")

    def stop_session(self, tenant_id: str) -> SusiResult:
        """Stop the grabber and release resources for a tenant."""
        return self._request("POST", f"/stop_event/{tenant_id}")

    def latest_transcript(self, tenant_id: str, sentences: bool = True) -> SusiResult:
        """Fetch the most recent transcript for a tenant (non-destructive)."""
        params = {"tenant_id": tenant_id, "sentences": "true" if sentences else "false"}
        return self._request("GET", "/transcripts/latest", params=params)

    def open_translate_stream(
        self, tenant_id: str, target_lang: str = "", last_chunk_id: int = 0
    ):
        """Open SUSI's SSE caption stream and return the streaming response.
        """
        params = {"tenant_id": tenant_id, "last_chunk_id": last_chunk_id}
        if target_lang:
            params["target_lang"] = target_lang
        url = self._url("/api/v1/translate/stream")
        try:
            # (connect timeout, read timeout): no read timeout for a long stream.
            resp = requests.get(
                url,
                params=params,
                headers=self._headers(),
                stream=True,
                timeout=(self.timeout, None),
            )
        except requests.RequestException as exc:
            raise SusiError(
                f"Could not open SUSI caption stream at {url}: {exc}"
            ) from exc
        if not resp.ok:
            resp.close()
            raise SusiError(f"SUSI caption stream returned HTTP {resp.status_code}.")
        return resp
