"""HTTP client for the FlightWeb cloud wire contract.

Uses the standard library (``urllib``) — no third-party HTTP dependency on the
Pi. The low-level transport is injectable (``request_fn``) so tests run without
the network.

The contract lives entirely in this module; nothing here imports FlightWeb
code. See docs/openflight-cloud-uploader-spec.md.
"""

import json
import urllib.error
import urllib.request
from collections import namedtuple
from dataclasses import dataclass
from typing import Callable, Dict, Optional

API_PREFIX = "/v1"
DEFAULT_TIMEOUT = 30

HttpResponse = namedtuple("HttpResponse", ["status", "headers", "body"])


class CloudNetworkError(Exception):
    """Raised when the request never reached the server (DNS, TLS, offline)."""


class RateLimited(Exception):
    """Raised on a 429 for link endpoints; carries Retry-After seconds."""

    def __init__(self, retry_after: Optional[int]):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class LinkError(Exception):
    """Raised when a link request is rejected (e.g. 422 invalid device name)."""

    def __init__(self, message: str, status: int, reason: Optional[str] = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


@dataclass
class LinkStart:
    """Response from ``device-link/start``: code to show + token to poll."""

    link_code: str
    poll_token: str
    interval_s: int
    expires_s: int


@dataclass
class LinkPoll:
    """Response from ``device-link/poll``: pending/expired/linked/unknown."""

    status: str
    device_token: Optional[str] = None
    device_id: Optional[str] = None


@dataclass
class UploadResult:
    """Outcome of an upload, with the action the spool layer should take."""

    status_code: int
    # one of: success, relink, quota, park, rate_limited, retry
    action: str
    reason: Optional[str] = None
    retry_after: Optional[int] = None
    session_id: Optional[str] = None
    shot_count: Optional[int] = None


def _header(headers: Dict[str, str], name: str) -> Optional[str]:
    """Case-insensitive header lookup."""
    lowered = {k.lower(): v for k, v in (headers or {}).items()}
    return lowered.get(name.lower())


def _retry_after(headers: Dict[str, str]) -> Optional[int]:
    value = _header(headers, "Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def urllib_request(
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResponse:
    """Default transport. Returns HttpResponse for any HTTP status (including
    4xx/5xx); raises CloudNetworkError only when the server was unreachable."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(
                status=resp.status,
                headers=dict(resp.headers.items()),
                body=resp.read(),
            )
    except urllib.error.HTTPError as exc:
        # HTTP error responses are valid contract responses, not failures.
        return HttpResponse(
            status=exc.code,
            headers=dict(exc.headers.items()) if exc.headers else {},
            body=exc.read(),
        )
    except urllib.error.URLError as exc:
        raise CloudNetworkError(str(exc.reason)) from exc
    except (TimeoutError, OSError) as exc:
        raise CloudNetworkError(str(exc)) from exc


class CloudClient:
    """Thin client over the ``/v1`` wire contract."""

    def __init__(
        self,
        endpoint: str,
        token: Optional[str] = None,
        request_fn: Callable[..., HttpResponse] = urllib_request,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self._request = request_fn
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{API_PREFIX}{path}"

    def _json_request(
        self, method: str, path: str, payload: Optional[dict] = None, headers: Optional[dict] = None
    ) -> HttpResponse:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        hdrs = {"Accept": "application/json"}
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        if headers:
            hdrs.update(headers)
        return self._request(method, self._url(path), data=body, headers=hdrs, timeout=self.timeout)

    @staticmethod
    def _body_json(resp: HttpResponse) -> dict:
        if not resp.body:
            return {}
        try:
            parsed = json.loads(resp.body.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def health(self) -> bool:
        """Cheap connectivity probe; True iff the server reports healthy."""
        try:
            resp = self._request(
                "GET", self._url("/health"), data=None, headers=None, timeout=self.timeout
            )
        except CloudNetworkError:
            return False
        return resp.status == 200 and self._body_json(resp).get("status") == "ok"

    def device_link_start(self, device_name: str, client_version: str) -> LinkStart:
        """Begin pairing; returns a link code to show the user and a poll token."""
        resp = self._json_request(
            "POST",
            "/device-link/start",
            {"device_name": device_name, "client_version": client_version},
        )
        if resp.status == 429:
            raise RateLimited(_retry_after(resp.headers))
        body = self._body_json(resp)
        if resp.status != 200:
            raise LinkError(f"link start failed ({resp.status})", resp.status, body.get("reason"))
        return LinkStart(
            link_code=body["link_code"],
            poll_token=body["poll_token"],
            interval_s=int(body.get("interval_s", 5)),
            expires_s=int(body.get("expires_s", 900)),
        )

    def device_link_poll(self, poll_token: str) -> LinkPoll:
        """Poll for pairing completion; on ``linked`` returns the device token."""
        resp = self._json_request("POST", "/device-link/poll", {"poll_token": poll_token})
        if resp.status == 429:
            raise RateLimited(_retry_after(resp.headers))
        if resp.status == 404:
            # Unknown OR already-consumed poll token.
            return LinkPoll(status="unknown")
        body = self._body_json(resp)
        if resp.status != 200:
            raise LinkError(f"link poll failed ({resp.status})", resp.status, body.get("reason"))
        return LinkPoll(
            status=body.get("status", "pending"),
            device_token=body.get("device_token"),
            device_id=body.get("device_id"),
        )

    def upload_session(self, session_id: str, gzipped_body: bytes) -> UploadResult:
        """PUT a filtered, gzipped session. Maps status -> spool action."""
        headers = {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self._request(
            "PUT",
            self._url(f"/sessions/{session_id}"),
            data=gzipped_body,
            headers=headers,
            timeout=self.timeout,
        )
        body = self._body_json(resp)
        reason = body.get("reason")

        if resp.status in (200, 201):
            return UploadResult(
                status_code=resp.status,
                action="success",
                session_id=body.get("session_id"),
                shot_count=body.get("shot_count"),
            )
        if resp.status == 401:
            return UploadResult(resp.status, action="relink", reason=reason)
        if resp.status == 402:
            return UploadResult(resp.status, action="quota", reason=reason)
        if resp.status in (413, 422):
            return UploadResult(resp.status, action="park", reason=reason)
        if resp.status == 429:
            return UploadResult(
                resp.status,
                action="rate_limited",
                reason=reason,
                retry_after=_retry_after(resp.headers),
            )
        # 5xx and anything unexpected: back off and retry.
        return UploadResult(resp.status, action="retry", reason=reason)

    def upload_capture(
        self,
        session_id: str,
        kind: str,
        shot_number: int,
        data: bytes,
        filename: Optional[str] = None,
    ) -> UploadResult:
        """PUT one shot's raw capture (e.g. an IWR6843 L3 dump) to its session.

        Only valid after the session itself uploaded; the server matches the
        capture to the shot by ``shot_number``. A 404 means the server doesn't
        have the session (yet), so it maps to ``retry``.
        """
        headers = {"Content-Type": "application/octet-stream"}
        if filename:
            headers["X-Capture-Filename"] = filename
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self._request(
            "PUT",
            self._url(f"/sessions/{session_id}/captures/{kind}/{int(shot_number)}"),
            data=data,
            headers=headers,
            timeout=self.timeout,
        )
        reason = self._body_json(resp).get("reason")
        if resp.status in (200, 201):
            return UploadResult(resp.status, action="success", session_id=session_id)
        if resp.status == 401:
            return UploadResult(resp.status, action="relink", reason=reason)
        if resp.status in (413, 422):
            return UploadResult(resp.status, action="park", reason=reason)
        if resp.status == 429:
            return UploadResult(
                resp.status,
                action="rate_limited",
                reason=reason,
                retry_after=_retry_after(resp.headers),
            )
        # 404 (session not stored yet), 5xx and anything unexpected: retry later.
        return UploadResult(resp.status, action="retry", reason=reason)
