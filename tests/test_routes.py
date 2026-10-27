import asyncio
import hashlib
import re
from collections.abc import Callable
from threading import Event

import obstore
import pytest
from factories import make_card
from fastapi.testclient import TestClient
from obstore.exceptions import GenericError
from obstore.store import MemoryStore

import courtier.app as app_module
from courtier import database
from courtier.app import app
from courtier.cache_keys import ROOT_SCRIPTS

IMAGE = b"\xff\xd8fake-jpeg"
PUBLIC_CACHE_CONTROL = "public, max-age=900, s-maxage=900"


def test_startup_does_not_wait_for_sync_and_cancels_it_on_shutdown(
    bucket: MemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    cancelled = Event()

    async def pending_sync(_bucket: MemoryStore) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(app_module, "create_bucket_client", lambda: bucket)
    monkeypatch.setattr(app_module, "sync_periodically", pending_sync)
    with TestClient(app) as restarted:
        assert started.wait(timeout=5)
        response = restarted.get("/v1/cards?ids=89631139")
        assert response.status_code == 404
    assert cancelled.wait(timeout=5)


@pytest.mark.parametrize(
    "path", ["/v1/cards?ids=99999999", "/v1/scripts/cards?ids=99999999"]
)
def test_batch_lookups_reject_unknown_ids(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Cards not found: 99999999."}
    assert response.headers["cache-control"] == "no-store"


def test_cards_returns_requested_cards_with_labels(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("89631139"), make_card("00423705", name="Other"))

    response = client.get("/v1/cards", params={"ids": ["89631139", "423705"]})

    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    cards = {card["id"]: card for card in response.json()}
    assert cards.keys() == {"89631139", "00423705"}
    assert cards["89631139"]["name"] == "Blue-Eyes White Dragon"
    assert cards["89631139"]["labels"] == {
        "password": "89631139",
        "name": {"en": "Blue-Eyes White Dragon"},
    }


def test_cards_rejects_any_unknown_id(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))

    response = client.get("/v1/cards", params={"ids": ["89631139", "1", "2"]})

    assert response.status_code == 404
    assert response.json() == {"detail": "Cards not found: 00000001, 00000002."}


def test_cards_pads_unpadded_ids(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("00423705", name="Dark Magician"))

    response = client.get("/v1/cards", params={"ids": "423705"})

    assert [card["id"] for card in response.json()] == ["00423705"]


def test_cards_exposes_defense_under_its_alias(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))

    [card] = client.get("/v1/cards", params={"ids": "89631139"}).json()

    assert card["def"] == "2500"
    assert "def_" not in card


def test_cards_requires_at_least_one_id(client: TestClient) -> None:
    assert client.get("/v1/cards").status_code == 422


def test_cards_caps_the_number_of_ids(
    client: TestClient, store: Callable[..., None]
) -> None:
    ids = [f"{number:08d}" for number in range(1, 102)]
    store(*(make_card(card_id) for card_id in ids[:100]))

    assert client.get("/v1/cards", params={"ids": ids}).status_code == 422
    assert client.get("/v1/cards", params={"ids": ids[:100]}).status_code == 200


def test_card_is_served_by_id(client: TestClient, store: Callable[..., None]) -> None:
    store(make_card("89631139"))

    response = client.get("/v1/cards/89631139")

    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert response.json()["name"] == "Blue-Eyes White Dragon"
    assert response.json()["def"] == "2500"


@pytest.mark.parametrize("path", ["/v1/cards/89631139", "/v1/cards?ids=89631139"])
def test_card_data_supports_conditional_requests(
    client: TestClient, store: Callable[..., None], path: str
) -> None:
    store(make_card("89631139"))
    etag = client.get(path).headers["etag"]

    response = client.get(path, headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL


def test_card_accepts_unpadded_id(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("00423705", name="Dark Magician"))

    assert client.get("/v1/cards/423705").json()["id"] == "00423705"


def test_card_404_when_unknown(client: TestClient) -> None:
    response = client.get("/v1/cards/89631139")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Card not found."}


def test_card_rejects_malformed_id(client: TestClient) -> None:
    response = client.get("/v1/cards/abc")

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("password", ["423705", "00423705"])
def test_script_is_served_by_card_password(
    client: TestClient, store: Callable[..., None], password: str
) -> None:
    store(scripts={"c423705.lua": "-- script"})

    response = client.get(f"/v1/scripts/cards/{password}")

    assert response.status_code == 200
    assert response.text == "-- script"
    assert response.headers["content-type"].startswith("text/x-lua")
    assert (
        response.headers["etag"]
        == f'W/"{hashlib.sha256(response.content).hexdigest()}"'
    )
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL


def test_card_password_script_404_when_card_has_none(client: TestClient) -> None:
    assert client.get("/v1/scripts/cards/89631139").status_code == 404


def test_card_script_rejects_malformed_id(client: TestClient) -> None:
    response = client.get("/v1/scripts/cards/abc")

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_script_is_served_with_weak_etag(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(scripts={"utility.lua": "-- utility"})

    response = client.get("/v1/scripts/system/utility.lua")

    assert response.status_code == 200
    assert response.text == "-- utility"
    assert response.headers["content-type"].startswith("text/x-lua")
    assert (
        response.headers["etag"]
        == f'W/"{hashlib.sha256(response.content).hexdigest()}"'
    )
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL


@pytest.mark.parametrize(
    ("condition", "status"),
    [
        ("{strong}", 304),
        ("{etag}", 304),
        ('"other", {etag}', 304),
        ("*", 304),
        ('"other"', 200),
    ],
)
def test_script_conditional_requests(
    client: TestClient, store: Callable[..., None], condition: str, status: int
) -> None:
    store(scripts={"utility.lua": "-- utility"})
    etag = client.get("/v1/scripts/system/utility.lua").headers["etag"]
    condition = condition.format(etag=etag, strong=etag.removeprefix("W/"))

    response = client.get(
        "/v1/scripts/system/utility.lua", headers={"If-None-Match": condition}
    )

    assert response.status_code == status
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    if status == 304:
        assert response.content == b""


def test_compressed_script_keeps_its_etag(
    client: TestClient, store: Callable[..., None]
) -> None:
    content = "-- long script\n" * 100
    store(scripts={"utility.lua": content})

    response = client.get(
        "/v1/scripts/system/utility.lua", headers={"Accept-Encoding": "gzip"}
    )

    assert response.text == content
    assert response.headers["content-encoding"] == "gzip"
    assert (
        response.headers["etag"]
        == f'W/"{hashlib.sha256(content.encode()).hexdigest()}"'
    )
    assert (
        client.get(
            "/v1/scripts/system/utility.lua",
            headers={"If-None-Match": response.headers["etag"]},
        ).status_code
        == 304
    )


def test_script_404_when_missing(client: TestClient) -> None:
    assert client.get("/v1/scripts/system/utility.lua").status_code == 404


@pytest.mark.parametrize("name", ["bad-name.lua", "abc"])
def test_script_rejects_invalid_name(client: TestClient, name: str) -> None:
    assert client.get(f"/v1/scripts/system/{name}").status_code == 422


def test_card_script_is_not_served_as_a_shared_script(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(scripts={"c89631139.lua": "-- script"})

    assert client.get("/v1/scripts/system/c89631139.lua").status_code == 404


def test_system_script_files_lists_root_scripts(client: TestClient) -> None:
    response = client.get("/v1/scripts/system/files")

    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert response.json() == list(ROOT_SCRIPTS)


def test_system_scripts_returns_every_script_in_one_request(
    client: TestClient, store: Callable[..., None]
) -> None:
    contents = {name: f"-- {name}" for name in ROOT_SCRIPTS}
    store(scripts=contents)

    response = client.get("/v1/scripts/system", params={"file": list(ROOT_SCRIPTS)})

    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert response.json() == contents


def test_system_scripts_returns_requested_scripts(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(scripts={"utility.lua": "-- utility", "chain.lua": "-- chain"})

    response = client.get(
        "/v1/scripts/system", params={"file": ["utility.lua", "chain.lua"]}
    )

    assert response.json() == {"utility.lua": "-- utility", "chain.lua": "-- chain"}


def test_system_scripts_maps_names_without_content_to_null(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(scripts={"utility.lua": "-- utility"})

    response = client.get(
        "/v1/scripts/system", params={"file": ["utility.lua", "chain.lua"]}
    )

    assert response.status_code == 200
    assert response.json() == {"utility.lua": "-- utility", "chain.lua": None}


def test_system_scripts_rejects_unknown_names(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(scripts={"utility.lua": "-- utility"})

    response = client.get(
        "/v1/scripts/system", params={"file": ["utility.lua", "c89631139.lua"]}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Scripts not found: c89631139.lua."}
    assert response.headers["cache-control"] == "no-store"


def test_system_scripts_requires_at_least_one_name(client: TestClient) -> None:
    assert client.get("/v1/scripts/system").status_code == 422


def test_system_scripts_rejects_malformed_names(client: TestClient) -> None:
    response = client.get("/v1/scripts/system", params={"file": "bad-name.lua"})

    assert response.status_code == 422


def test_system_scripts_caps_the_number_of_names(client: TestClient) -> None:
    names = [*ROOT_SCRIPTS, "utility.lua"]

    assert client.get("/v1/scripts/system", params={"file": names}).status_code == 422


def test_scripts_batch_is_keyed_by_card_id(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(
        make_card("89631139"),
        make_card("00423705"),
        scripts={"c89631139.lua": "-- script", "c423705.lua": "-- other"},
    )

    response = client.get("/v1/scripts/cards", params={"ids": ["89631139", "423705"]})

    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert response.json() == {"89631139": "-- script", "00423705": "-- other"}


def test_scripts_batch_maps_cards_without_scripts_to_null(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(
        make_card("89631139"),
        make_card("00423705"),
        scripts={"c89631139.lua": "-- script"},
    )

    response = client.get("/v1/scripts/cards", params={"ids": ["89631139", "423705"]})

    assert response.status_code == 200
    assert response.json() == {"89631139": "-- script", "00423705": None}


def test_scripts_batch_rejects_unknown_ids(
    client: TestClient, store: Callable[..., None]
) -> None:
    store(make_card("89631139"), scripts={"c89631139.lua": "-- script"})

    response = client.get("/v1/scripts/cards", params={"ids": ["89631139", "00000001"]})

    assert response.status_code == 404
    assert response.json() == {"detail": "Cards not found: 00000001."}
    assert response.headers["cache-control"] == "no-store"


def test_scripts_requires_at_least_one_id(client: TestClient) -> None:
    assert client.get("/v1/scripts/cards").status_code == 422


def test_scripts_batch_caps_the_number_of_ids(
    client: TestClient, store: Callable[..., None]
) -> None:
    ids = [f"{number:08d}" for number in range(1, 102)]
    store(*(make_card(card_id) for card_id in ids[:100]))

    assert client.get("/v1/scripts/cards", params={"ids": ids}).status_code == 422
    assert client.get("/v1/scripts/cards", params={"ids": ids[:100]}).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/v1/scripts/system/files",
        "/v1/scripts/system?file=utility.lua",
        "/v1/scripts/cards?ids=89631139",
        "/v1/scripts/cards/89631139",
    ],
)
def test_scripts_support_conditional_requests(
    client: TestClient,
    store: Callable[..., None],
    path: str,
) -> None:
    store(
        make_card("89631139"),
        scripts={"c89631139.lua": "-- script", "utility.lua": "-- utility"},
    )
    etag = client.get(path).headers["etag"]

    response = client.get(path, headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL


def test_card_image_is_served_from_bucket(
    client: TestClient, bucket: MemoryStore, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))
    obstore.put(bucket, "89631139.jpg", IMAGE)

    response = client.get("/v1/images/89631139")

    assert response.status_code == 200
    assert response.content == IMAGE
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == PUBLIC_CACHE_CONTROL
    assert response.headers["etag"].startswith('W/"')


def test_card_image_not_modified_when_etag_matches(
    client: TestClient, bucket: MemoryStore, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))
    obstore.put(bucket, "89631139.jpg", IMAGE)
    etag = client.get("/v1/images/89631139").headers["etag"]

    response = client.get("/v1/images/89631139", headers={"If-None-Match": etag})

    assert response.status_code == 304
    assert response.headers["etag"] == etag


def test_card_image_not_modified_for_wildcard_etag(
    client: TestClient, bucket: MemoryStore, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))
    obstore.put(bucket, "89631139.jpg", IMAGE)

    response = client.get("/v1/images/89631139", headers={"If-None-Match": "*"})

    assert response.status_code == 304


def test_card_image_503_when_storage_fails(
    client: TestClient,
    store: Callable[..., None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store(make_card("89631139"))

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        raise GenericError("storage is down")

    monkeypatch.setattr("courtier.routes.get_card_images.get_async", unavailable)

    response = client.get("/v1/images/89631139")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"] == "Image storage unavailable."


@pytest.mark.parametrize(
    "path", ["/v1/images/89631139", "/v1/images/89631139/thumbnail"]
)
def test_card_image_404_when_not_in_bucket(
    client: TestClient, store: Callable[..., None], path: str
) -> None:
    store(make_card("89631139"))

    assert client.get(path).status_code == 404


def test_card_image_404_for_unknown_card_even_if_in_bucket(
    client: TestClient, bucket: MemoryStore
) -> None:
    obstore.put(bucket, "89631139.jpg", IMAGE)

    assert client.get("/v1/images/89631139").status_code == 404
    assert client.get("/v1/images/89631139/thumbnail").status_code == 404


def test_thumbnail_is_served_from_thumbnails_prefix(
    client: TestClient, bucket: MemoryStore, store: Callable[..., None]
) -> None:
    store(make_card("89631139"))
    obstore.put(bucket, "thumbnails/89631139.jpg", IMAGE)

    response = client.get("/v1/images/89631139/thumbnail")

    assert response.status_code == 200
    assert response.content == IMAGE


def test_image_accepts_unpadded_id(
    client: TestClient, bucket: MemoryStore, store: Callable[..., None]
) -> None:
    store(make_card("00423705"))
    obstore.put(bucket, "423705.jpg", IMAGE)

    assert client.get("/v1/images/423705").content == IMAGE


def test_image_rejects_malformed_id(client: TestClient) -> None:
    assert client.get("/v1/images/abc").status_code == 422


@pytest.mark.parametrize("path", ["/v1/cards/89631139", "/v1/images/89631139"])
def test_head_is_not_supported(client: TestClient, path: str) -> None:
    assert client.head(path).status_code == 405


def test_unknown_route_uses_fastapi_errors(client: TestClient) -> None:
    response = client.get("/v1/nothing-here")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Not Found"}


def test_wrong_method_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/v1/cards")

    assert response.status_code == 405
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": "Method Not Allowed"}
    assert "GET" in response.headers["allow"]


def test_validation_error_uses_fastapi_errors(client: TestClient) -> None:
    response = client.get("/v1/cards/abc")

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"][0]["loc"] == ["path", "card_id"]


@pytest.mark.parametrize(
    ("path", "status"),
    [("/v1/scripts/system/files", 200), ("/v1/cards/abc", 422), ("/v1/no", 404)],
)
def test_cors_headers_survive_api_simplification(
    client: TestClient, path: str, status: int
) -> None:
    response = client.get(path, headers={"Origin": "https://example.com"})

    assert response.status_code == status
    assert response.headers["access-control-allow-origin"] == "*"


def test_unversioned_paths_are_gone(client: TestClient) -> None:
    for path in (
        "/ready",
        "/cards?ids=89631139",
        "/card/data",
        "/card/89631139/image",
        "/scripts/system",
        "/script/utility.lua",
    ):
        assert client.get(path).status_code == 404, path


def test_unhandled_errors_do_not_leak_details(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable_card(_card_id: str) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(database, "get_card", unavailable_card)
    error_client = TestClient(app, raise_server_exceptions=False)
    response = error_client.get("/v1/cards/89631139")

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.text == "Internal Server Error"


def test_openapi_documents_lua_and_image_media_types(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert (
        "text/x-lua"
        in paths["/v1/scripts/cards/{card_id}"]["get"]["responses"]["200"]["content"]
    )
    assert (
        "image/jpeg"
        in paths["/v1/images/{card_id}"]["get"]["responses"]["200"]["content"]
    )


def test_openapi_lists_all_get_endpoints(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    assert spec["info"]["title"] == "Courtier"
    assert spec["info"]["version"] == "1"
    assert set(spec["paths"]) == {
        "/v1/cards",
        "/v1/cards/{card_id}",
        "/v1/images/{card_id}",
        "/v1/images/{card_id}/thumbnail",
        "/v1/scripts/cards",
        "/v1/scripts/cards/{card_id}",
        "/v1/scripts/system",
        "/v1/scripts/system/files",
        "/v1/scripts/system/{filename}",
    }
    operations = [path["get"] for path in spec["paths"].values()]
    assert len({operation["operationId"] for operation in operations}) == len(
        operations
    )


def test_openapi_card_id_input_accepts_unpadded_ids(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    schema = spec["paths"]["/v1/cards/{card_id}"]["get"]["parameters"][0]["schema"]

    assert "minLength" not in schema
    assert re.fullmatch(schema["pattern"], "423705")
    assert not re.fullmatch(schema["pattern"], "4237x5")
    output = spec["components"]["schemas"]["Card"]["properties"]["id"]
    assert re.fullmatch(output["pattern"], "00423705")


def test_default_api_docs_are_available(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
