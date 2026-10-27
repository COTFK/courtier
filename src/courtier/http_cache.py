"""Conditional request helpers shared by the cached routes."""

import hashlib
import re

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

_ETAG_PATTERN = re.compile(r'(?:W/)?"[^"]*"')
PUBLIC_CACHE_CONTROL = "public, max-age=900, s-maxage=900"


def _opaque_tag(tag: str) -> str:
    return tag.removeprefix("W/").strip('"')


def weak_etag(value: str) -> str:
    """Weak tags stay valid when gzip re-encodes the body."""
    return f'W/"{_opaque_tag(value)}"'


def is_not_modified(request: Request, etag: str) -> bool:
    """Weakly compares `If-None-Match` with `etag`, as RFC 9110 requires for GET."""
    condition = request.headers.get("if-none-match")
    if condition is None:
        return False
    if condition.strip() == "*":
        return True
    return any(
        _opaque_tag(tag) == _opaque_tag(etag)
        for tag in _ETAG_PATTERN.findall(condition)
    )


def content_etag(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def cache_headers(cache_control: str, etag: str | None = None) -> dict[str, str]:
    headers = {"Cache-Control": cache_control}
    if etag:
        headers["ETag"] = weak_etag(etag)
    return headers


def not_modified(request: Request, headers: dict[str, str]) -> Response | None:
    """A 304 response carrying `headers` when the client already holds the response's ETag."""
    etag = headers.get("ETag")
    if etag is None or not is_not_modified(request, etag):
        return None
    return Response(status_code=304, headers=headers)


def cached_json_response(request: Request, content: object) -> Response:
    json_response = JSONResponse(content=jsonable_encoder(content))
    headers = cache_headers(PUBLIC_CACHE_CONTROL, content_etag(json_response.body))
    if not_modified_response := not_modified(request, headers):
        return not_modified_response
    json_response.headers.update(headers)
    return json_response
