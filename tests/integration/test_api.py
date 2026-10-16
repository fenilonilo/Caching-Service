from uuid import uuid4

import pytest

from caching_service.api.schemas import MAX_ITEMS, MAX_STRING_LENGTH
from tests.support import SAMPLE_BODY, SAMPLE_OUTPUT


def test_post_creates_payload_and_get_returns_sample_output(client) -> None:
    created = client.post("/payload", json=SAMPLE_BODY)
    assert created.status_code == 201
    assert created.json()["message"] == "Payload created"

    fetched = client.get(f"/payload/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == {"output": SAMPLE_OUTPUT}


def test_repeated_post_returns_same_id_without_transformer_calls(client, transformer) -> None:
    first = client.post("/payload", json=SAMPLE_BODY).json()
    transformer.calls.clear()
    second = client.post("/payload", json=SAMPLE_BODY)
    assert second.status_code == 200
    assert second.json() == {"id": first["id"], "message": "Payload already exists"}
    assert transformer.calls == []


def test_transformations_are_reused_across_different_payloads(client, transformer) -> None:
    client.post("/payload", json={"list_1": ["a"], "list_2": ["b"]})
    transformer.calls.clear()
    created = client.post("/payload", json={"list_1": ["b"], "list_2": ["a"]})
    assert created.status_code == 201  # new payload, yet both strings came from the cache
    assert transformer.calls == []


def test_non_ascii_strings_round_trip(client) -> None:
    created = client.post("/payload", json={"list_1": ["straße"], "list_2": ["café ☕"]}).json()
    assert client.get(f"/payload/{created['id']}").json() == {"output": "STRASSE, CAFÉ ☕"}


def test_get_unknown_id_returns_404(client) -> None:
    assert client.get(f"/payload/{uuid4()}").status_code == 404


def test_get_malformed_id_returns_422(client) -> None:
    assert client.get("/payload/not-a-uuid").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"list_1": ["a", "b"], "list_2": ["c"]}, id="different-lengths"),
        pytest.param({"list_1": [], "list_2": []}, id="empty-lists"),
        pytest.param({"list_1": ["a"]}, id="missing-list"),
        pytest.param({"list_1": [1], "list_2": ["b"]}, id="non-string-item"),
        pytest.param({"list_1": ["a"], "list_2": ["b"], "list_3": ["c"]}, id="unknown-field"),
        pytest.param(
            {"list_1": ["x" * (MAX_STRING_LENGTH + 1)], "list_2": ["b"]}, id="string-too-long"
        ),
        pytest.param(
            {"list_1": ["a"] * (MAX_ITEMS + 1), "list_2": ["b"] * (MAX_ITEMS + 1)},
            id="too-many-items",
        ),
    ],
)
def test_invalid_body_is_rejected_before_any_transformer_call(client, transformer, body) -> None:
    assert client.post("/payload", json=body).status_code == 422
    assert transformer.calls == []


def test_lone_surrogate_is_rejected_before_any_transformer_call(client, transformer) -> None:
    body = b'{"list_1": ["\\ud800"], "list_2": ["b"]}'
    response = client.post("/payload", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert transformer.calls == []


def test_transformer_failure_returns_502_and_keeps_successful_results(client, transformer) -> None:
    assert client.post("/payload", json={"list_1": ["ok"], "list_2": ["boom"]}).status_code == 502
    transformer.calls.clear()
    assert client.post("/payload", json={"list_1": ["ok"], "list_2": ["fine"]}).status_code == 201
    assert transformer.calls == ["fine"]
