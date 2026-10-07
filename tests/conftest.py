"""Fixtures for the Tody Home Assistant integration tests.

TodyClient is always mocked: these tests never talk to Firebase/Tody.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tody.api import JoinResult
from custom_components.tody.const import (
    CONF_MASTERDATA_ID,
    CONF_PARTICIPANT_ID,
    CONF_REFRESH_TOKEN,
    CONF_UID,
    DOMAIN,
)
from custom_components.tody.model import Area, Participant, Task, TodyData

# Local time in the test HA instance (US/Pacific) is 2026-10-07 05:00.
NOW = "2026-10-07 12:00:00+00:00"
TODAY = date(2026, 10, 7)

MASTERDATA_ID = "sync-1"
OWN_PARTICIPANT = "p-hass"
ANNA = "p-anna"
BOB = "p-bob"

ENTRY_DATA = {
    CONF_UID: "uid-1",
    CONF_REFRESH_TOKEN: "refresh-1",
    CONF_MASTERDATA_ID: MASTERDATA_ID,
    CONF_PARTICIPANT_ID: OWN_PARTICIPANT,
}

JOIN_RESULT = JoinResult(
    uid="uid-1",
    refresh_token="refresh-1",
    masterdata_id=MASTERDATA_ID,
    participant_id=OWN_PARTICIPANT,
    participant_name="Hass",
)

SNAPSHOT = {"fbMetadata": [{"_id": "meta", "dataSyncName": "Our House"}]}


def make_task(task_id: str, name: str, area_id: str, due: date | None, turn: set[str], **kw) -> Task:
    """Build a Task with sensible defaults."""
    return Task(
        id=task_id,
        name=name,
        area_id=area_id,
        frequency=1,
        frequency_type=4,
        frequency_minutes=7 * 24 * 60,
        due=due,
        last_done=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
        last_done_by=ANNA,
        turn_participant_ids=frozenset(turn),
        paused=kw.get("paused", False),
        archived=kw.get("archived", False),
    )


def make_data() -> TodyData:
    """A data sync with two areas, three participants (one is our own) and a spread of due dates."""
    tasks = [
        make_task("t-overdue", "Vacuum", "a-kitchen", TODAY - timedelta(days=2), {ANNA}),
        make_task("t-today", "Dishes", "a-kitchen", TODAY, {BOB}),
        make_task("t-today2", "Mirror", "a-bath", TODAY, {ANNA, BOB}),
        make_task("t-tomorrow", "Toilet", "a-bath", TODAY + timedelta(days=1), {BOB}),
        make_task("t-later", "Windows", "a-bath", TODAY + timedelta(days=3), {ANNA}),
        make_task("t-paused", "Oven", "a-kitchen", TODAY - timedelta(days=5), {ANNA}, paused=True),
        make_task("t-own", "Hass task", "a-kitchen", TODAY, {OWN_PARTICIPANT}),
    ]
    return TodyData(
        sync_name="Our House",
        areas={"a-kitchen": Area("a-kitchen", "Kitchen"), "a-bath": Area("a-bath", "Bathroom")},
        participants={
            OWN_PARTICIPANT: Participant(OWN_PARTICIPANT, "Hass"),
            ANNA: Participant(ANNA, "Anna"),
            BOB: Participant(BOB, "Bob"),
        },
        tasks={t.id: t for t in tasks},
        on_vacation=False,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components/tody."""


@pytest.fixture(autouse=True)
def frozen_time(freezer) -> None:
    """Freeze time so due dates are deterministic."""
    freezer.move_to(NOW)


@pytest.fixture
def tody_data() -> TodyData:
    """Parsed data returned by the (mocked) parse_snapshot."""
    return make_data()


@pytest.fixture
def mock_client(tody_data: TodyData) -> Generator[MagicMock]:
    """Mock TodyClient everywhere it is imported, and parse_snapshot."""
    client = MagicMock()
    client.join = AsyncMock(return_value=JOIN_RESULT)
    client.fetch_snapshot = AsyncMock(return_value=SNAPSHOT)
    with (
        patch("custom_components.tody.coordinator.TodyClient", return_value=client) as coord_cls,
        patch("custom_components.tody.config_flow.TodyClient", return_value=client),
        patch("custom_components.tody.model.parse_snapshot", return_value=tody_data) as parse,
    ):
        client.coordinator_cls = coord_cls
        client.parse = parse
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A Tody config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Our House",
        unique_id=MASTERDATA_ID,
        data=dict(ENTRY_DATA),
        options={},
    )


@pytest.fixture
async def setup_integration(hass, config_entry: MockConfigEntry, mock_client: MagicMock) -> MockConfigEntry:
    """Set up the integration with mocked data."""
    hass.config.language = "en"
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry



@pytest.fixture(autouse=True)
def fake_api_key() -> Generator[None]:
    """Pretend the Firebase API key has been filled in (the repo only has a placeholder)."""
    with patch("custom_components.tody.config_flow.FIREBASE_API_KEY", "test-key"):
        yield
