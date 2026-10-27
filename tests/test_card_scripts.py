import tarfile
from collections.abc import Callable

import anyio
import httpx2 as httpx
import pytest
from factories import HttpHandler, make_archive

from courtier.providers.card_scripts import (
    ScriptData,
    _extract_scripts,
    fetch_lua_scripts,
)


def test_prefers_earlier_folder_in_search_order() -> None:
    archive = make_archive(
        {"official/c89631139.lua": b"official", "pre-release/c89631139.lua": b"pre"}
    )

    scripts = _extract_scripts(archive, {"c89631139.lua": ("official", "pre-release")})

    assert scripts["c89631139.lua"] == "official"


def test_falls_back_to_later_folder() -> None:
    archive = make_archive({"pre-release/c89631139.lua": b"pre"})

    scripts = _extract_scripts(archive, {"c89631139.lua": ("official", "pre-release")})

    assert scripts["c89631139.lua"] == "pre"


def test_search_priority_wins_over_archive_order() -> None:
    archive = make_archive(
        {"pre-release/c89631139.lua": b"pre", "official/c89631139.lua": b"official"}
    )

    scripts = _extract_scripts(archive, {"c89631139.lua": ("official", "pre-release")})

    assert scripts["c89631139.lua"] == "official"


def test_invalid_utf8_is_replaced_without_losing_the_script() -> None:
    archive = make_archive({"official/c89631139.lua": b"-- invalid: \xff"})

    scripts = _extract_scripts(archive, {"c89631139.lua": ("official",)})

    assert scripts["c89631139.lua"] == "-- invalid: \ufffd"


def test_ignores_files_outside_the_search() -> None:
    archive = make_archive(
        {"official/c1.lua": b"x", "official/other/c89631139.lua": b"x"}
    )

    assert _extract_scripts(archive, {"c89631139.lua": ("official",)}) == {}


def test_extracts_root_scripts() -> None:
    archive = make_archive({"utility.lua": b"-- utility"})

    scripts = _extract_scripts(archive, {"utility.lua": ("",)})

    assert scripts["utility.lua"] == "-- utility"


LUA_ARCHIVE = make_archive(
    {
        "official/c89631139.lua": b"official",
        "official/c1.lua": b"unrelated",
        "utility.lua": b"-- utility",
        "unofficial/proc_unofficial.lua": b"-- unofficial",
        "README.md": b"# CardScripts",
    }
)


def serve(route_http: Callable[[HttpHandler], None], body: bytes) -> None:
    route_http(lambda _request: httpx.Response(httpx.codes.OK, content=body))


def test_fetch_extracts_requested_and_shared_scripts(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(
        lambda _request: httpx.Response(
            httpx.codes.OK,
            content=LUA_ARCHIVE,
            headers={"ETag": '"scripts-v1"'},
        )
    )

    scripts = anyio.run(fetch_lua_scripts, ["89631139"])

    assert scripts == ScriptData(
        '"scripts-v1"',
        {
            "c89631139.lua": "official",
            "utility.lua": "-- utility",
            "proc_unofficial.lua": "-- unofficial",
        },
    )


def test_unchanged_archive_skips_download_and_extraction(
    route_http: Callable[[HttpHandler], None],
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(httpx.codes.NOT_MODIFIED)

    route_http(handler)

    result = anyio.run(fetch_lua_scripts, ["89631139"], '"scripts-v1"')

    assert result is None
    assert len(seen) == 1
    assert seen[0].headers["If-None-Match"] == '"scripts-v1"'


def test_download_server_error_propagates(
    route_http: Callable[[HttpHandler], None],
) -> None:
    route_http(
        lambda _request: httpx.Response(httpx.codes.INTERNAL_SERVER_ERROR),
    )

    with pytest.raises(httpx.HTTPStatusError):
        anyio.run(fetch_lua_scripts, ["89631139"])


def test_archive_connection_error_propagates(
    route_http: Callable[[HttpHandler], None],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    route_http(refuse)

    with pytest.raises(httpx.ConnectError, match="connection refused"):
        anyio.run(fetch_lua_scripts, ["89631139"])


def test_unreadable_archive_fails(
    route_http: Callable[[HttpHandler], None],
) -> None:
    serve(route_http, b"not a tarball")

    with pytest.raises(tarfile.ReadError):
        anyio.run(fetch_lua_scripts, ["89631139"])


def test_archive_without_expected_scripts_fails(
    route_http: Callable[[HttpHandler], None],
) -> None:
    serve(route_http, make_archive({"README.md": b"# CardScripts"}))

    with pytest.raises(ValueError, match="none of the expected scripts"):
        anyio.run(fetch_lua_scripts, ["89631139"])
