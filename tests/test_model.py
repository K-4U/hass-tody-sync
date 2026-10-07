"""Tests for custom_components/tody/model.py (pure Python, no Home Assistant)."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_helpers import AMS, FIXTURE_NOW, due_table, dt, load_fixture, load_model  # noqa: E402

m = load_model()

A, B, C = "user-a", "user-b", "user-c"
NOW = dt("2026-10-07T12:00")


# --- synthetic snapshot builders ---------------------------------------------

def make_task(task_id: str = "t1", **overrides):
    doc = {
        "_id": task_id,
        "taskID": task_id,
        "taskName": overrides.pop("name", "Task " + task_id),
        "belongsToAreaID": "area1",
        "createdDate": dt("2026-01-01T00:00"),
        "frequency": 1,
        "frequencyTypeStoreVal": m.FREQUENCY_WEEKS,
        "frequencyMinutes": 7 * 1440,
        "taskActions": [],
        "taskAssignments": [A, B, C],
        "assignsAll": False,
        "assignmentRotationOff": False,
        "taskPauses": [],
        "forcedDueOn": None,
        "archivedOn": None,
        "deadline": None,
        "fixedDueWeekDaysStoreVal": [],
        "fixedDueMonthDaysStoreVal": [],
        "fixedDueMonthsStoreVal": [],
    }
    doc.update(overrides)
    return doc


def make_action(action_id: str, task_id: str, when: datetime, by: str = A, system: bool = False):
    return {
        "_id": action_id,
        "actionID": action_id,
        "belongsToTaskID": task_id,
        "actionTime": when,
        "doneByUserID": "" if system else by,
        "systemAction": system,
        "lastModifiedDate": when,
    }


def make_range(range_id: str, start: datetime, end: datetime, kind: int = 1):
    return {"_id": range_id, "dateRangeID": range_id, "dateRangeTypeStoreVal": kind, "startDate": start, "endDate": end}


def snapshot(tasks, actions=(), ranges=(), vacation_list=(), users=(A, B, C)):
    return {
        "tasks": list(tasks),
        "actions": list(actions),
        "areas": [{"_id": "area1", "areaID": "area1", "areaName": "Kitchen"}],
        "users": [{"_id": u, "userID": u, "userName": "Person " + u[-1].upper(), "authUserIDs": []} for u in users],
        "dateRanges": list(ranges),
        "planSpecifications": [{"_id": "defaultPlanSpecification", "vacationList": list(vacation_list)}],
        "fbMetadata": [{"_id": "metaDataID", "dataSyncName": "Home"}],
        "masterdata": [{"_id": "md"}],
    }


def parse(snap, now=NOW, tz=UTC):
    return m.parse_snapshot(snap, now=now, tz=tz)


# --- fixture -------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture_data():
    return m.parse_snapshot(load_fixture(), now=FIXTURE_NOW, tz=AMS)


def test_fixture_parses(fixture_data):
    d = fixture_data
    assert d.sync_name == "Test home"
    assert len(d.tasks) == 26
    assert len(d.areas) == 7
    assert {p.name for p in d.participants.values()} == {"Hass", "Person A", "Person B"}
    assert d.on_vacation is False
    for t in d.tasks.values():
        assert t.area_id in d.areas
        assert t.due is not None
        assert t.last_done is None or t.last_done.utcoffset() is not None


def test_fixture_vacuum_floor_by_hand(fixture_data):
    # Last action 2024-07-01T14:43:59.258Z (Person A), 2 months = 60 days, no pauses.
    t = fixture_data.tasks["07b9eb64-22b0-4958-a70a-8bd05360c800"]
    assert t.name == "Vacuum floor"
    assert t.frequency == 2 and t.frequency_type == m.FREQUENCY_MONTHS and t.frequency_minutes == 86400
    assert t.due == date(2024, 8, 30)
    assert t.last_done == datetime(2024, 7, 1, 14, 43, 59, 258000, tzinfo=UTC)
    assert t.last_done.tzinfo == AMS  # converted to local in parse_snapshot
    assert fixture_data.participants[t.last_done_by].name == "Person A"
    assert not t.paused and not t.archived
    assert describe_en(t, fixture_data) == "Every 2 months · Last done 1 Jul 2024 by Person A"


def test_fixture_flags(fixture_data):
    by_name = {}
    for t in fixture_data.tasks.values():
        by_name.setdefault(t.name, []).append(t)
    assert by_name["Humidifier filters"][0].paused  # pause until 2030
    assert by_name["Humidifier filters"][0].last_done is None  # only a system action
    assert by_name["Kitty Litter"][0].archived


def describe_en(task, data):
    return m.describe(task, data, "en")


def test_print_due_table(capsys):
    table = due_table()
    with capsys.disabled():
        print("\n" + table)
    assert "Vacuum floor" in table


# --- rule 1/2: baseline and due --------------------------------------------------

def test_due_from_created_date_without_actions():
    d = parse(snapshot([make_task()]))
    assert d.tasks["t1"].due == date(2026, 1, 8)
    assert d.tasks["t1"].last_done is None


def test_baseline_uses_latest_action_including_system():
    actions = [
        make_action("a1", "t1", dt("2026-09-01T10:00"), by=A),
        make_action("a2", "t1", dt("2026-09-10T10:00"), system=True),  # e.g. reset
        make_action("a3", "t1", dt("2026-08-01T10:00"), by=B),
    ]
    t = parse(snapshot([make_task()], actions)).tasks["t1"]
    assert t.due == date(2026, 9, 17)
    assert t.last_done == dt("2026-09-01T10:00")
    assert t.last_done_by == A


def test_due_local_date_uses_tz():
    # 2026-10-01T23:30Z + 7d = 2026-10-08T23:30Z = 2026-10-09 01:30 in Amsterdam.
    snap = snapshot([make_task()], [make_action("a1", "t1", dt("2026-10-01T23:30"))])
    assert parse(snap, tz=UTC).tasks["t1"].due == date(2026, 10, 8)
    assert parse(snap, tz=ZoneInfo("Europe/Amsterdam")).tasks["t1"].due == date(2026, 10, 9)


def test_pause_overlap_pushes_due():
    # Done 2026-09-01, pause 2026-09-03..2026-09-06 (3 days) fully inside [baseline, now].
    snap = snapshot(
        [make_task(taskPauses=["p1"])],
        [make_action("a1", "t1", dt("2026-09-01T00:00"))],
        [make_range("p1", dt("2026-09-03T00:00"), dt("2026-09-06T00:00"))],
    )
    t = parse(snap).tasks["t1"]
    assert t.due == date(2026, 9, 11)
    assert not t.paused


def test_pause_overlap_is_clipped_and_ignores_other_tasks_ranges():
    snap = snapshot(
        [make_task(taskPauses=["p_before", "p_partial", "p_game"])],
        [make_action("a1", "t1", dt("2026-09-01T00:00"))],
        [
            make_range("p_before", dt("2026-08-01T00:00"), dt("2026-08-20T00:00")),  # before baseline
            make_range("p_partial", dt("2026-08-30T00:00"), dt("2026-09-03T00:00")),  # 2 days overlap
            make_range("p_game", dt("2026-09-01T00:00"), dt("2026-09-20T00:00"), kind=7),  # not a pause
            make_range("p_unused", dt("2026-09-01T00:00"), dt("2026-09-20T00:00")),  # not linked
        ],
    )
    assert parse(snap).tasks["t1"].due == date(2026, 9, 10)


def test_current_pause_marks_paused_and_counts_until_now():
    snap = snapshot(
        [make_task(taskPauses=["p1"])],
        [make_action("a1", "t1", dt("2026-10-01T12:00"))],
        [make_range("p1", dt("2026-10-05T12:00"), dt("2026-12-01T00:00"))],
    )
    t = parse(snap).tasks["t1"]  # now = 2026-10-07T12:00 -> 2 days of pause so far
    assert t.paused
    assert t.due == date(2026, 10, 10)


def test_overlapping_pauses_counted_once():
    snap = snapshot(
        [make_task(taskPauses=["p1", "p2"])],
        [make_action("a1", "t1", dt("2026-09-01T00:00"))],
        [
            make_range("p1", dt("2026-09-02T00:00"), dt("2026-09-05T00:00")),
            make_range("p2", dt("2026-09-04T00:00"), dt("2026-09-06T00:00")),
        ],
    )
    assert parse(snap).tasks["t1"].due == date(2026, 9, 12)  # 4 days, not 5


def test_vacation_overlap_by_id_and_inline_dict():
    for vac in (["v1"], [{"startDate": dt("2026-09-02T00:00"), "endDate": dt("2026-09-07T00:00")}]):
        snap = snapshot(
            [make_task()],
            [make_action("a1", "t1", dt("2026-09-01T00:00"))],
            [make_range("v1", dt("2026-09-02T00:00"), dt("2026-09-07T00:00"), kind=99)],
            vacation_list=vac,
        )
        d = parse(snap)
        assert d.tasks["t1"].due == date(2026, 9, 13)
        assert not d.on_vacation


def test_vacation_unknown_shapes_tolerated():
    snap = snapshot([make_task()], vacation_list=["missing-id", 42, None, {"foo": 1}, {"startDate": "x"}])
    assert parse(snap).on_vacation is False


def test_on_vacation_now():
    snap = snapshot(
        [make_task()],
        [make_action("a1", "t1", dt("2026-09-01T00:00"))],
        vacation_list=[{"startDate": dt("2026-10-01T00:00"), "endDate": dt("2026-10-20T00:00")}],
    )
    d = parse(snap)
    assert d.on_vacation
    assert m.tasks_due_by(d, date(2030, 1, 1)) == []


def test_forced_due_overrides_when_after_baseline():
    snap = snapshot([make_task(forcedDueOn=dt("2026-09-03T10:00"))], [make_action("a1", "t1", dt("2026-09-01T00:00"))])
    assert parse(snap).tasks["t1"].due == date(2026, 9, 3)


def test_forced_due_ignored_when_before_baseline():
    snap = snapshot([make_task(forcedDueOn=dt("2026-08-03T10:00"))], [make_action("a1", "t1", dt("2026-09-01T00:00"))])
    assert parse(snap).tasks["t1"].due == date(2026, 9, 8)


def test_archived():
    t = parse(snapshot([make_task(archivedOn=dt("2026-01-02T00:00"))])).tasks["t1"]
    assert t.archived


# --- rule 5: last done -----------------------------------------------------------

def test_last_done_empty_user_is_none():
    snap = snapshot([make_task()], [make_action("a1", "t1", dt("2026-09-01T00:00"), by="")])
    t = parse(snap).tasks["t1"]
    assert t.last_done == dt("2026-09-01T00:00") and t.last_done_by is None


# --- rule 6: whose turn ----------------------------------------------------------

def turn(task_overrides=None, last_by=None, users=(A, B, C)):
    actions = [make_action("a1", "t1", dt("2026-09-01T00:00"), by=last_by)] if last_by is not None else []
    snap = snapshot([make_task(**(task_overrides or {}))], actions, users=users)
    return parse(snap).tasks["t1"].turn_participant_ids


def test_turn_assigns_all():
    assert turn({"assignsAll": True}, last_by=A) == {A, B, C}


def test_turn_rotation_off():
    assert turn({"assignmentRotationOff": True}, last_by=A) == {A}


@pytest.mark.parametrize(("last_by", "expected"), [(A, B), (B, C), (C, A), (None, A), ("stranger", A)])
def test_turn_rotation(last_by, expected):
    assert turn(last_by=last_by) == {expected}


def test_turn_filters_unknown_users():
    assert turn({"assignsAll": True}, users=(A, B)) == {A, B}
    assert turn(last_by=B, users=(A, B)) == frozenset()  # C's turn, but C no longer exists


def test_turn_no_assignments():
    assert turn({"taskAssignments": []}) == frozenset()


# --- rule 7: tasks_due_by --------------------------------------------------------

def test_tasks_due_by_filters_and_sorts():
    tasks = [
        make_task("t1", name="b task"),
        make_task("t2", name="A task"),
        make_task("t3", name="later"),
        make_task("t4", name="paused", taskPauses=["p1"]),
        make_task("t5", name="archived", archivedOn=dt("2026-01-01T00:00")),
        make_task("t6", name="no interval", frequencyMinutes=0),
        make_task("t7", name="earlier"),
    ]
    actions = [
        make_action("a1", "t1", dt("2026-10-01T12:00")),
        make_action("a2", "t2", dt("2026-10-01T12:00")),
        make_action("a3", "t3", dt("2026-10-03T12:00")),
        make_action("a4", "t4", dt("2026-09-01T12:00")),
        make_action("a5", "t5", dt("2026-09-01T12:00")),
        make_action("a7", "t7", dt("2026-09-20T12:00")),
    ]
    ranges = [make_range("p1", dt("2026-10-06T00:00"), dt("2026-10-20T00:00"))]
    d = parse(snapshot(tasks, actions, ranges))
    assert d.tasks["t6"].due is None
    assert [t.name for t in m.tasks_due_by(d, date(2026, 10, 8))] == ["earlier", "A task", "b task"]
    assert [t.name for t in m.tasks_due_by(d, date(2026, 10, 10))] == ["earlier", "A task", "b task", "later"]


# --- rule 8: describe ------------------------------------------------------------

@pytest.mark.parametrize(
    ("freq", "ftype", "en", "nl"),
    [
        (1, m.FREQUENCY_WEEKS, "Every week", "Elke week"),
        (3, m.FREQUENCY_WEEKS, "Every 3 weeks", "Elke 3 weken"),
        (1, m.FREQUENCY_MONTHS, "Every month", "Elke maand"),
        (2, m.FREQUENCY_MONTHS, "Every 2 months", "Elke 2 maanden"),
        (1, m.FREQUENCY_YEARS, "Every year", "Elk jaar"),
        (2, m.FREQUENCY_YEARS, "Every 2 years", "Elke 2 jaren"),
    ],
)
def test_describe_frequency(freq, ftype, en, nl):
    d = parse(snapshot([make_task(frequency=freq, frequencyTypeStoreVal=ftype)]))
    t = d.tasks["t1"]
    assert m.describe(t, d, "en") == f"{en} · Never done yet"
    assert m.describe(t, d, "nl") == f"{nl} · Nog nooit gedaan"


def test_describe_last_done_local_date():
    # 2024-06-30T22:30Z is 1 Jul 2024 00:30 in Amsterdam.
    snap = snapshot(
        [make_task(frequency=2, frequencyTypeStoreVal=m.FREQUENCY_MONTHS, frequencyMinutes=86400)],
        [make_action("a1", "t1", dt("2024-06-30T22:30"), by=B)],
    )
    d = parse(snap, tz=ZoneInfo("Europe/Amsterdam"))
    t = d.tasks["t1"]
    assert m.describe(t, d, "en") == "Every 2 months · Last done 1 Jul 2024 by Person B"
    assert m.describe(t, d, "nl") == "Elke 2 maanden · Laatst gedaan 1 jul 2024 door Person B"
    d_utc = parse(snap, tz=UTC)
    assert m.describe(d_utc.tasks["t1"], d_utc, "en") == "Every 2 months · Last done 30 Jun 2024 by Person B"


def test_describe_last_done_without_person():
    snap = snapshot([make_task()], [make_action("a1", "t1", dt("2026-03-05T10:00"), by="")])
    d = parse(snap)
    assert m.describe(d.tasks["t1"], d, "en") == "Every week · Last done 5 Mar 2026"
    assert m.describe(d.tasks["t1"], d, "nl") == "Elke week · Laatst gedaan 5 mrt 2026"


# --- robustness ------------------------------------------------------------------

def test_missing_collections_and_bad_docs():
    d = m.parse_snapshot({"tasks": [{"_id": "x"}, "garbage", {}]}, now=NOW, tz=UTC)
    assert d.sync_name == ""
    assert d.tasks["x"].due is None
    assert d.tasks["x"].name == ""
    assert m.tasks_due_by(d, date(2030, 1, 1)) == []


def test_naive_now_treated_as_utc():
    snap = snapshot([make_task()])
    assert parse(snap, now=NOW.replace(tzinfo=None)).tasks["t1"].due == date(2026, 1, 8)


def test_due_shift_is_exact():
    snap = snapshot(
        [make_task(taskPauses=["p1"])],
        [make_action("a1", "t1", dt("2026-09-01T00:00"))],
        [make_range("p1", dt("2026-09-02T00:00"), dt("2026-09-02T12:00"))],
    )
    # 7d + 12h -> 2026-09-08T12:00
    assert parse(snap).tasks["t1"].due == (dt("2026-09-01T00:00") + timedelta(days=7, hours=12)).date()
