"""Tests for the Tody Firebase client. All HTTP is mocked; nothing reaches Firebase."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tody.api import (
    AUTH_URL,
    DOCUMENTS_URL,
    TOKEN_URL,
    TodyAuthError,
    TodyClient,
    TodyConnectionError,
    TodyInviteError,
    decode_value,
)

MD = "md-1"
PARTICIPANT = "p-1"
SIGNUP = f"{AUTH_URL}:signUp"
DELETE = f"{AUTH_URL}:delete"
RUN_QUERY = f"{DOCUMENTS_URL}:runQuery"
COMMIT = f"{DOCUMENTS_URL}:commit"
BASE = f"{DOCUMENTS_URL}/masterdata/{MD}"
TOKEN_BODY = {"id_token": "id-2", "refresh_token": "refresh-1", "expires_in": "3600"}
SIGNUP_BODY = {"idToken": "id-1", "refreshToken": "refresh-1", "expiresIn": "3600", "localId": "uid-1"}


def invite_row(expires: datetime) -> list[dict]:
    return [
        {
            "document": {
                "name": "projects/x/databases/(default)/documents/inviteInfo/inv-1",
                "fields": {
                    "inviteID": {"stringValue": "abc123"},
                    "inviteMasterdataID": {"stringValue": MD},
                    "inviteParticipantID": {"stringValue": PARTICIPANT},
                    "inviteParticipantName": {"stringValue": "Hass"},
                    "inviteExpirationDate": {"timestampValue": expires.isoformat().replace("+00:00", "Z")},
                },
            },
            "readTime": "2026-10-07T13:53:50.456869Z",
        }
    ]


def bodies_to(mock: AiohttpClientMocker, url: str) -> list:
    """JSON bodies of the calls made to `url` (query string ignored)."""
    return [data for _, u, data, _ in mock.mock_calls if str(u.with_query(None)) == url]


@pytest.fixture
def session(hass, aioclient_mock: AiohttpClientMocker):
    return aioclient_mock.create_session(hass.loop)


def mock_collections(aioclient_mock: AiohttpClientMocker, status: int = 200, json: dict | None = None) -> None:
    aioclient_mock.get(re.compile(re.escape(DOCUMENTS_URL) + r"/.*"), status=status, json=json or {})


def test_decode_value() -> None:
    assert decode_value({"integerValue": "86400"}) == 86400
    assert decode_value({"nullValue": None}) is None
    assert decode_value({"timestampValue": "2024-07-01T14:43:59.258123456Z"}) == datetime(
        2024, 7, 1, 14, 43, 59, 258123, tzinfo=UTC
    )
    assert decode_value({"arrayValue": {}}) == []
    assert decode_value({"arrayValue": {"values": [{"stringValue": "a"}]}}) == ["a"]
    assert decode_value({"mapValue": {"fields": {"x": {"booleanValue": True}}}}) == {"x": True}


async def test_join_makes_only_the_two_safe_writes(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(SIGNUP, json=SIGNUP_BODY)
    aioclient_mock.post(RUN_QUERY, json=invite_row(datetime.now(UTC) + timedelta(days=7)))
    aioclient_mock.post(COMMIT, json={"writeResults": [{}]})

    result = await TodyClient(session).join(" abc123 ")

    assert result.uid == "uid-1"
    assert result.refresh_token == "refresh-1"
    assert result.masterdata_id == MD
    assert result.participant_id == PARTICIPANT
    assert result.participant_name == "Hass"

    query = bodies_to(aioclient_mock, RUN_QUERY)[0]["structuredQuery"]
    assert query["where"]["fieldFilter"]["value"] == {"stringValue": "abc123"}

    writes = [body["writes"] for body in bodies_to(aioclient_mock, COMMIT)]
    assert len(writes) == 2 and all(len(w) == 1 for w in writes)
    doc = f"projects/tody-96d33/databases/(default)/documents/masterdata/{MD}/users/{PARTICIPANT}"
    # 1: transform-only append; must never be an "update" (that could replace the document).
    assert "update" not in writes[0][0]
    assert writes[0][0]["transform"] == {
        "document": doc,
        "fieldTransforms": [
            {"fieldPath": "authUserIDs", "appendMissingElements": {"values": [{"stringValue": "uid-1"}]}}
        ],
    }
    assert writes[0][0]["currentDocument"] == {"exists": True}
    # 2: masked update of a single field.
    assert writes[1][0]["update"]["name"] == doc
    assert writes[1][0]["updateMask"] == {"fieldPaths": ["fbMasterdataID"]}
    assert list(writes[1][0]["update"]["fields"]) == ["fbMasterdataID"]
    assert writes[1][0]["currentDocument"] == {"exists": True}


async def test_join_unknown_code_deletes_anonymous_user(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(SIGNUP, json=SIGNUP_BODY)
    aioclient_mock.post(RUN_QUERY, json=[{"readTime": "2026-10-07T13:53:50Z"}])
    aioclient_mock.post(DELETE, json={})

    with pytest.raises(TodyInviteError):
        await TodyClient(session).join("nope")

    assert not bodies_to(aioclient_mock, COMMIT)
    assert bodies_to(aioclient_mock, DELETE) == [{"idToken": "id-1"}]


async def test_join_expired_code(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(SIGNUP, json=SIGNUP_BODY)
    aioclient_mock.post(RUN_QUERY, json=invite_row(datetime.now(UTC) - timedelta(minutes=1)))
    aioclient_mock.post(DELETE, json={})

    with pytest.raises(TodyInviteError):
        await TodyClient(session).join("abc123")

    assert not bodies_to(aioclient_mock, COMMIT)


async def test_fetch_snapshot_pages_and_rotated_token(session, aioclient_mock: AiohttpClientMocker) -> None:
    saved: list[str] = []
    doc = lambda i: {"name": f"x/tasks/t{i}", "fields": {"taskName": {"stringValue": f"T{i}"}}}  # noqa: E731
    aioclient_mock.post(TOKEN_URL, json={**TOKEN_BODY, "refresh_token": "refresh-2"})
    # First matching mock wins, so the specific ones go first.
    aioclient_mock.get(f"{BASE}/tasks?pageToken=n", json={"documents": [doc(2)]})
    aioclient_mock.get(f"{BASE}/tasks", json={"documents": [doc(1)], "nextPageToken": "n"})
    aioclient_mock.get(BASE, json={"name": f"x/masterdata/{MD}", "fields": {"masterDataID": {"stringValue": MD}}})
    mock_collections(aioclient_mock)

    snap = await TodyClient(session, "refresh-1", saved.append).fetch_snapshot(MD)

    assert [t["taskName"] for t in snap["tasks"]] == ["T1", "T2"]
    assert snap["tasks"][0]["_id"] == "t1"
    assert snap["masterdata"] == [{"masterDataID": MD, "_id": MD}]
    assert snap["actions"] == []
    assert saved == ["refresh-2"]
    # Only reads: no Firestore writes.
    assert all(method.lower() == "get" for method, url, _, _ in aioclient_mock.mock_calls if "firestore" in str(url))


async def test_revoked_refresh_token_is_auth_error(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(TOKEN_URL, status=400, json={"error": {"message": "TOKEN_EXPIRED"}})
    with pytest.raises(TodyAuthError):
        await TodyClient(session, "refresh-1").fetch_snapshot(MD)


async def test_permission_denied_is_auth_error(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(TOKEN_URL, json=TOKEN_BODY)
    mock_collections(aioclient_mock, status=403, json={"error": {"status": "PERMISSION_DENIED"}})
    with pytest.raises(TodyAuthError):
        await TodyClient(session, "refresh-1").fetch_snapshot(MD)


async def test_server_error_is_connection_error(session, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(TOKEN_URL, json=TOKEN_BODY)
    mock_collections(aioclient_mock, status=503, json={"error": {"message": "busy"}})
    with pytest.raises(TodyConnectionError):
        await TodyClient(session, "refresh-1").fetch_snapshot(MD)
