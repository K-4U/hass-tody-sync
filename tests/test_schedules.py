"""Fixed schedules (weekdays, days of the month, months) and seasonal tasks.

The first four cases mirror test tasks created in the Tody app on Wed 7 Oct 2026, including the
backdated system action Tody adds when a fixed-schedule task is created.
"""

from __future__ import annotations

from datetime import date

from .model_helpers import AMS, dt, load_model
from .test_model import make_action, make_task, parse, snapshot

m = load_model()

NOW = dt("2026-10-07T18:45")  # Wednesday


def _due(task, start, now=NOW, tz=AMS):
    """Due date of a task whose only action is Tody's backdated system action at `start` (UTC)."""
    data = parse(snapshot([task], [make_action("a1", task["_id"], dt(start), system=True)]), now=now, tz=tz)
    return data.tasks[task["_id"]]


def fixed(**overrides):
    return make_task("t1", taskTypeStoreVal=m.TASK_TYPE_FIXED, **overrides)


def test_weekdays_mon_wed_fri() -> None:
    """Created on Wednesday; Tody backdates to Monday, so it's due today (Wednesday)."""
    task = fixed(fixedDueWeekDaysStoreVal=[1, 3, 5])
    assert _due(task, "2026-10-05T18:41:43.841").due == date(2026, 10, 7)


def test_first_of_the_month() -> None:
    task = fixed(frequencyTypeStoreVal=m.FREQUENCY_MONTHS, frequencyMinutes=43200, fixedDueMonthDaysStoreVal=[1])
    assert _due(task, "2026-10-01T18:42:01.432").due == date(2026, 11, 1)


def test_every_october() -> None:
    """Backdated to the end of last October, so it's due from 1 October this year."""
    task = fixed(frequencyTypeStoreVal=m.FREQUENCY_YEARS, frequencyMinutes=518400, fixedDueMonthsStoreVal=[10])
    assert _due(task, "2025-10-31T22:59:59").due == date(2026, 10, 1)


def test_seasonal_tuesdays_in_december() -> None:
    task = fixed(fixedDueWeekDaysStoreVal=[2], isSeasonal=True, activeMonths=[12])
    result = _due(task, "2026-10-06T18:42:28.554")
    assert result.due == date(2026, 12, 1)  # first Tuesday in December
    assert result.active_months == (12,)


def test_completion_moves_to_next_scheduled_day() -> None:
    task = fixed(fixedDueWeekDaysStoreVal=[1, 3, 5])
    actions = [
        make_action("a1", "t1", dt("2026-10-05T18:41"), system=True),
        make_action("a2", "t1", dt("2026-10-07T19:00")),  # done Wednesday evening
    ]
    data = parse(snapshot([task], actions), now=dt("2026-10-07T20:00"), tz=AMS)
    assert data.tasks["t1"].due == date(2026, 10, 9)  # Friday


def test_sunday_as_zero_or_seven() -> None:
    for sunday in (0, 7):
        task = fixed(fixedDueWeekDaysStoreVal=[sunday])
        assert _due(task, "2026-10-05T10:00").due == date(2026, 10, 11)


def test_31st_falls_on_last_day_of_short_months() -> None:
    task = fixed(frequencyTypeStoreVal=m.FREQUENCY_MONTHS, fixedDueMonthDaysStoreVal=[31])
    assert _due(task, "2026-10-31T12:00").due == date(2026, 11, 30)


def test_every_two_weeks_on_monday() -> None:
    task = fixed(frequency=2, fixedDueWeekDaysStoreVal=[1])
    assert _due(task, "2026-10-05T10:00").due == date(2026, 10, 19)


def test_local_date_of_last_completion() -> None:
    """Done at 00:30 on Monday in Amsterdam (still Sunday in UTC): counts for Monday."""
    task = fixed(fixedDueWeekDaysStoreVal=[1])
    assert _due(task, "2026-10-04T22:30").due == date(2026, 10, 12)


def test_seasonal_interval_task_waits_for_season() -> None:
    """A regular every-week task that is only active in December is due on 1 December."""
    task = make_task("t1", isSeasonal=True, activeMonths=[12])
    assert _due(task, "2026-10-01T10:00").due == date(2026, 12, 1)


def test_seasonal_flag_without_months_is_all_year() -> None:
    task = make_task("t1", isSeasonal=True, activeMonths=[])
    assert _due(task, "2026-10-01T10:00").due == date(2026, 10, 8)


def test_schedule_description() -> None:
    task = _due(fixed(fixedDueWeekDaysStoreVal=[1, 3, 5]), "2026-10-05T18:41")
    data = parse(snapshot([fixed(fixedDueWeekDaysStoreVal=[1, 3, 5])]), now=NOW, tz=AMS)
    assert m.describe(task, data, "en").startswith("Every week on Mon, Wed and Fri · ")
    assert m.describe(task, data, "nl").startswith("Elke week op ma, wo en vr · ")
    seasonal = _due(fixed(fixedDueWeekDaysStoreVal=[2], isSeasonal=True, activeMonths=[12]), "2026-10-06T18:42")
    assert m.describe(seasonal, data, "en").startswith("Every week on Tue (Dec only) · ")
    october = _due(fixed(frequencyTypeStoreVal=m.FREQUENCY_YEARS, fixedDueMonthsStoreVal=[10]), "2025-10-31T22:59")
    assert m.describe(october, data, "nl").startswith("Elk jaar in okt · ")
