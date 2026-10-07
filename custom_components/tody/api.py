"""Minimal client for Tody's Firebase backend (anonymous auth + Firestore REST).

Read-only by design: the only write this client can make is joining a data sync
with an invite code, which is the same write the Tody app makes when you join.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any

import aiohttp

from .const import (
    ANDROID_CERT_SHA1,
    ANDROID_PACKAGE,
    FIREBASE_API_KEY,
    FIREBASE_GMPID,
    FIREBASE_PROJECT_ID,
)

_LOGGER = logging.getLogger(__name__)

# Collections under masterdata/{id} that the integration reads.
SNAPSHOT_COLLECTIONS = ("tasks", "actions", "areas", "users", "dateRanges", "planSpecifications", "fbMetadata")

AUTH_URL = "https://identitytoolkit.googleapis.com/v1/accounts"
TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
DOCUMENTS_URL = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
DOCUMENT_PREFIX = f"projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"

PAGE_SIZE = 300
TIMEOUT = aiohttp.ClientTimeout(total=30)
# Refresh the ID token this long before it expires.
TOKEN_MARGIN = timedelta(minutes=5)
# Firebase Auth errors that mean the refresh token will never work again.
AUTH_FATAL = ("TOKEN_EXPIRED", "INVALID_REFRESH_TOKEN", "USER_DISABLED", "USER_NOT_FOUND", "INVALID_GRANT_TYPE")

_TS_FRACTION = re.compile(r"\.(\d{6})\d+")


class TodyError(Exception):
    """Base error."""


class TodyConnectionError(TodyError):
    """Network problem or unexpected response; retry later."""


class TodyAuthError(TodyError):
    """Refresh token rejected or access to the data sync revoked; needs a new invite code."""


class TodyInviteError(TodyError):
    """Invite code unknown or expired."""


@dataclass(frozen=True, slots=True)
class JoinResult:
    """Outcome of joining a data sync."""

    uid: str
    refresh_token: str
    masterdata_id: str
    participant_id: str
    participant_name: str


def decode_value(value: dict[str, Any]) -> Any:
    """Convert a Firestore REST value to plain Python."""
    (kind, raw), = value.items()
    if kind == "nullValue":
        return None
    if kind == "integerValue":
        return int(raw)
    if kind == "doubleValue":
        return float(raw)
    if kind == "timestampValue":
        # Firestore may send nanoseconds; Python handles at most microseconds.
        return datetime.fromisoformat(_TS_FRACTION.sub(r".\1", raw)).astimezone(UTC)
    if kind == "arrayValue":
        return [decode_value(item) for item in raw.get("values", [])]
    if kind == "mapValue":
        return decode_fields(raw.get("fields", {}))
    # stringValue, booleanValue, referenceValue, bytesValue, geoPointValue
    return raw


def decode_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Convert a Firestore REST fields map to a plain dict."""
    return {key: decode_value(value) for key, value in fields.items()}


def decode_document(document: dict[str, Any]) -> dict[str, Any]:
    """Convert a Firestore REST document to a plain dict with its id under "_id"."""
    doc = decode_fields(document.get("fields", {}))
    doc["_id"] = document["name"].rsplit("/", 1)[-1]
    return doc


class TodyClient:
    """Talks to Firebase Auth and Firestore on behalf of an anonymous Tody participant."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        on_refresh_token: Callable[[str], None] | None = None,
    ) -> None:
        """Create a client. on_refresh_token is called when Firebase hands out a new refresh token."""
        self._session = session
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._id_token: str | None = None
        self._id_token_expires = datetime.min.replace(tzinfo=UTC)
        self._token_lock = asyncio.Lock()

    # --- Firebase Auth -------------------------------------------------------

    @staticmethod
    def _app_headers() -> dict[str, str]:
        return {
            "X-Android-Package": ANDROID_PACKAGE,
            "X-Android-Cert": ANDROID_CERT_SHA1,
            "X-Firebase-GMPID": FIREBASE_GMPID,
        }

    async def _auth_post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with self._session.post(
                url, params={"key": FIREBASE_API_KEY}, headers=self._app_headers(), timeout=TIMEOUT, **kwargs
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status == 200:
                    return body
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise TodyConnectionError(f"Firebase Auth request failed: {err}") from err
        message = str((body or {}).get("error", {}).get("message", resp.status))
        if message.startswith(AUTH_FATAL):
            raise TodyAuthError(message)
        raise TodyConnectionError(f"Firebase Auth error: {message}")

    def _store_tokens(self, id_token: str, expires_in: str | int, refresh_token: str) -> None:
        self._id_token = id_token
        self._id_token_expires = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        if refresh_token != self._refresh_token:
            self._refresh_token = refresh_token
            if self._on_refresh_token:
                self._on_refresh_token(refresh_token)

    async def _sign_up(self) -> str:
        """Create a new anonymous Firebase user and return its uid."""
        body = await self._auth_post(f"{AUTH_URL}:signUp", json={"returnSecureToken": True})
        self._store_tokens(body["idToken"], body["expiresIn"], body["refreshToken"])
        return body["localId"]

    async def _delete_account(self) -> None:
        """Best effort: remove the anonymous user again after a failed join (the app does the same)."""
        if not self._id_token:
            return
        try:
            await self._auth_post(f"{AUTH_URL}:delete", json={"idToken": self._id_token})
        except TodyError as err:
            _LOGGER.debug("Could not delete unused anonymous account: %s", err)

    async def _get_id_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if not force_refresh and self._id_token and datetime.now(UTC) < self._id_token_expires - TOKEN_MARGIN:
                return self._id_token
            if not self._refresh_token:
                raise TodyAuthError("No refresh token")
            body = await self._auth_post(
                TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self._refresh_token}
            )
            self._store_tokens(body["id_token"], body["expires_in"], body["refresh_token"])
            return body["id_token"]

    # --- Firestore -----------------------------------------------------------

    async def _firestore(self, method: str, url: str, **kwargs: Any) -> Any:
        """Authenticated Firestore REST call; refreshes the token once on 401."""
        for attempt in range(2):
            token = await self._get_id_token(force_refresh=attempt > 0)
            try:
                async with self._session.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}", "X-Firebase-GMPID": FIREBASE_GMPID},
                    timeout=TIMEOUT,
                    **kwargs,
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    body = await resp.json(content_type=None)
                    status = resp.status
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise TodyConnectionError(f"Firestore request failed: {err}") from err
            if status == 200:
                return body
            error = body[0].get("error", {}) if isinstance(body, list) and body else (body or {}).get("error", {})
            if status in (401, 403):
                # Security rules deny access: this user is no longer part of the data sync.
                raise TodyAuthError(f"Firestore access denied: {error.get('status', status)}")
            raise TodyConnectionError(f"Firestore error {status}: {error.get('message', '')}")
        raise TodyAuthError("Firestore rejected a fresh token")

    async def _list_documents(self, path: str) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        params: dict[str, Any] = {"pageSize": PAGE_SIZE}
        while True:
            body = await self._firestore("GET", f"{DOCUMENTS_URL}/{path}", params=params)
            docs.extend(decode_document(doc) for doc in body.get("documents", []))
            if not (page_token := body.get("nextPageToken")):
                return docs
            params["pageToken"] = page_token

    async def _find_invite(self, invite_code: str) -> dict[str, Any] | None:
        body = await self._firestore(
            "POST",
            f"{DOCUMENTS_URL}:runQuery",
            json={
                "structuredQuery": {
                    "from": [{"collectionId": "inviteInfo"}],
                    "where": {
                        "fieldFilter": {
                            "field": {"fieldPath": "inviteID"},
                            "op": "EQUAL",
                            "value": {"stringValue": invite_code},
                        }
                    },
                }
            },
        )
        for row in body:
            if "document" in row:
                return decode_document(row["document"])
        return None

    async def _commit(self, write: dict[str, Any]) -> None:
        await self._firestore("POST", f"{DOCUMENTS_URL}:commit", json={"writes": [write]})

    # --- Public API ----------------------------------------------------------

    async def join(self, invite_code: str) -> JoinResult:
        """Sign up anonymously, look up the invite and add this user to the invited participant."""
        invite_code = invite_code.strip()
        uid = await self._sign_up()
        try:
            invite = await self._find_invite(invite_code)
            if invite is None:
                raise TodyInviteError("Unknown invite code")
            expires = invite.get("inviteExpirationDate")
            if expires is not None and expires < datetime.now(UTC):
                raise TodyInviteError("Invite code expired")
            masterdata_id = invite["inviteMasterdataID"]
            participant_id = invite["inviteParticipantID"]

            user_doc = f"{DOCUMENT_PREFIX}/masterdata/{masterdata_id}/users/{participant_id}"
            # Same two writes the app makes. The first is a transform-only write so it can
            # never replace the participant document; it only adds our uid to authUserIDs.
            await self._commit(
                {
                    "transform": {
                        "document": user_doc,
                        "fieldTransforms": [
                            {"fieldPath": "authUserIDs", "appendMissingElements": {"values": [{"stringValue": uid}]}}
                        ],
                    },
                    "currentDocument": {"exists": True},
                }
            )
            await self._commit(
                {
                    "update": {"name": user_doc, "fields": {"fbMasterdataID": {"stringValue": masterdata_id}}},
                    "updateMask": {"fieldPaths": ["fbMasterdataID"]},
                    "currentDocument": {"exists": True},
                }
            )
        except TodyAuthError as err:
            # A fresh anonymous user can't be denied unless the invite flow itself was refused.
            await self._delete_account()
            raise TodyInviteError(str(err)) from err
        except TodyError:
            await self._delete_account()
            raise

        assert self._refresh_token is not None
        return JoinResult(
            uid=uid,
            refresh_token=self._refresh_token,
            masterdata_id=masterdata_id,
            participant_id=participant_id,
            participant_name=invite.get("inviteParticipantName", ""),
        )

    async def fetch_snapshot(self, masterdata_id: str) -> dict[str, list[dict[str, Any]]]:
        """Read all SNAPSHOT_COLLECTIONS plus the masterdata document itself (key "masterdata").

        Every document is returned as a plain dict of decoded Firestore values plus "_id"
        (the document id): timestamps become aware UTC datetimes, integers become int,
        nulls become None, arrays become lists, maps become dicts.
        """
        base = f"masterdata/{masterdata_id}"
        results = await asyncio.gather(
            self._firestore("GET", f"{DOCUMENTS_URL}/{base}"),
            *(self._list_documents(f"{base}/{collection}") for collection in SNAPSHOT_COLLECTIONS),
        )
        snapshot = dict(zip(SNAPSHOT_COLLECTIONS, results[1:], strict=True))
        snapshot["masterdata"] = [decode_document(results[0])]
        return snapshot
