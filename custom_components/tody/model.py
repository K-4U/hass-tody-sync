"""Tody data model and due-date logic. Pure Python: no Home Assistant or network imports."""

from __future__ import annotations

import calendar
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

# frequencyTypeStoreVal -> unit. Tody stores the full interval in frequencyMinutes as well.
FREQUENCY_WEEKS = 4
FREQUENCY_MONTHS = 5  # 30 days
FREQUENCY_YEARS = 6  # 360 days

DATE_RANGE_TASK_PAUSE = 1

# taskTypeStoreVal: 0 = every N days/weeks/..., 1 = fixed schedule (weekdays, days of the month, months).
TASK_TYPE_FIXED = 1
# How far ahead to look for the next scheduled day.
_MAX_SCAN_DAYS = 3 * 366


@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Participant:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    name: str
    area_id: str | None
    frequency: int
    frequency_type: int
    frequency_minutes: int
    # Local calendar date the task is due; None if it can't be determined.
    due: date | None
    # Latest completion by a person (systemAction == False).
    last_done: datetime | None
    last_done_by: str | None  # participant id
    # Participants whose turn it is; all assignees when the task is shared by everyone.
    turn_participant_ids: frozenset[str]
    paused: bool
    archived: bool
    # Fixed schedule: ISO weekdays (1 = Monday), days of the month, months (1-12). Empty if not used.
    weekdays: tuple[int, ...] = ()
    month_days: tuple[int, ...] = ()
    months: tuple[int, ...] = ()
    # Seasonal tasks only count in these months (1-12); empty = all year.
    active_months: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TodyData:
    sync_name: str
    areas: Mapping[str, Area]
    participants: Mapping[str, Participant]
    tasks: Mapping[str, Task]
    on_vacation: bool


# ---------------------------------------------------------------------------
# Implementation. Each Tody rule lives in its own small function so it can be
# adjusted once verified against the app.
# ---------------------------------------------------------------------------

_Range = tuple[datetime, datetime]


def _as_datetime(value: Any) -> datetime | None:
    """Return an aware UTC datetime, or None for anything that isn't a datetime."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _range_from_doc(doc: Any) -> _Range | None:
    if not isinstance(doc, Mapping):
        return None
    start = _as_datetime(doc.get("startDate"))
    end = _as_datetime(doc.get("endDate"))
    if start is None or end is None or end <= start:
        return None
    return (start, end)


def _resolve_ranges(refs: Any, date_ranges: Mapping[str, Mapping[str, Any]], *, type_filter: int | None) -> list[_Range]:
    """Resolve a list of dateRange ids (or inline dicts with startDate/endDate) to ranges.

    Unknown shapes are ignored. `type_filter` only applies to id references.
    """
    ranges: list[_Range] = []
    for ref in _as_list(refs):
        if isinstance(ref, str):
            doc = date_ranges.get(ref)
            if doc is None:
                continue
            if type_filter is not None and doc.get("dateRangeTypeStoreVal") != type_filter:
                continue
            rng = _range_from_doc(doc)
        else:
            rng = _range_from_doc(ref)
        if rng is not None:
            ranges.append(rng)
    return ranges


def _overlap(window: _Range, ranges: list[_Range]) -> timedelta:
    """Total time inside `window` covered by `ranges` (overlapping ranges counted once)."""
    w_start, w_end = window
    clipped = sorted((max(s, w_start), min(e, w_end)) for s, e in ranges if s < w_end and e > w_start)
    total = timedelta()
    cur_start: datetime | None = None
    cur_end: datetime | None = None
    for s, e in clipped:
        if cur_end is None or s > cur_end:
            if cur_end is not None and cur_start is not None:
                total += cur_end - cur_start
            cur_start, cur_end = s, e
        else:
            cur_end = max(cur_end, e)
    if cur_end is not None and cur_start is not None:
        total += cur_end - cur_start
    return total


def _inside_any(moment: datetime, ranges: list[_Range]) -> bool:
    return any(s <= moment < e for s, e in ranges)


# --- Rule 1: baseline -------------------------------------------------------

def compute_baseline(task_doc: Mapping[str, Any], actions: list[Mapping[str, Any]]) -> datetime | None:
    """Latest action (system or not) by actionTime; falls back to the task's createdDate."""
    times = [t for a in actions if (t := _as_datetime(a.get("actionTime"))) is not None]
    if times:
        return max(times)
    return _as_datetime(task_doc.get("createdDate"))


# --- Rule 2: due datetime ---------------------------------------------------

def compute_due_datetime(
    task_doc: Mapping[str, Any],
    baseline: datetime | None,
    *,
    now: datetime,
    pauses: list[_Range],
    vacations: list[_Range],
) -> datetime | None:
    """baseline + interval, pushed back by pause/vacation time between baseline and now.

    forcedDueOn overrides when it is later than the baseline.
    TODO: fixedDueWeekDaysStoreVal / fixedDueMonthDaysStoreVal / fixedDueMonthsStoreVal and
    isSeasonal/activeMonths are not handled yet (all empty/unused in the captured data).
    """
    if baseline is None:
        return None
    forced = _as_datetime(task_doc.get("forcedDueOn"))
    if forced is not None and forced > baseline:
        return forced
    minutes = _as_int(task_doc.get("frequencyMinutes"))
    if minutes <= 0:
        return None
    due = baseline + timedelta(minutes=minutes)
    if now > baseline:
        window = (baseline, now)
        due += _overlap(window, pauses) + _overlap(window, vacations)
    return due


# --- Rule 4: flags ----------------------------------------------------------

def is_paused(pauses: list[_Range], now: datetime) -> bool:
    return _inside_any(now, pauses)


def is_archived(task_doc: Mapping[str, Any]) -> bool:
    return task_doc.get("archivedOn") is not None


def is_on_vacation(vacations: list[_Range], now: datetime) -> bool:
    return _inside_any(now, vacations)


# --- Rule 5: last done ------------------------------------------------------

def compute_last_done(actions: list[Mapping[str, Any]]) -> tuple[datetime | None, str | None]:
    """Latest non-system action and who did it ("" -> None)."""
    best: tuple[datetime, str | None] | None = None
    for action in actions:
        if action.get("systemAction"):
            continue
        when = _as_datetime(action.get("actionTime"))
        if when is None:
            continue
        if best is None or when > best[0]:
            by = action.get("doneByUserID")
            best = (when, by if isinstance(by, str) and by else None)
    if best is None:
        return None, None
    return best


# --- Rule 6: whose turn -----------------------------------------------------

def compute_turn(task_doc: Mapping[str, Any], last_done_by: str | None, known_ids: Collection[str]) -> frozenset[str]:
    assignments = [p for p in _as_list(task_doc.get("taskAssignments")) if isinstance(p, str)]
    if not assignments:
        return frozenset()
    if task_doc.get("assignsAll"):
        turn = set(assignments)
    elif task_doc.get("assignmentRotationOff"):
        turn = {assignments[0]}
    elif last_done_by is None or last_done_by not in assignments:
        turn = {assignments[0]}
    else:
        turn = {assignments[(assignments.index(last_done_by) + 1) % len(assignments)]}
    return frozenset(p for p in turn if p in known_ids)


# --- Rule 8: fixed schedules and seasons ------------------------------------

def _ints(value: Any) -> tuple[int, ...]:
    return tuple(sorted({i for v in _as_list(value) if (i := _as_int(v, -1)) >= 0}))


def schedule_weekdays(task_doc: Mapping[str, Any]) -> tuple[int, ...]:
    """ISO weekdays (1 = Monday ... 7 = Sunday); a 0 is read as Sunday."""
    return tuple(sorted({7 if d == 0 else d for d in _ints(task_doc.get("fixedDueWeekDaysStoreVal")) if d <= 7}))


def schedule_month_days(task_doc: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(d for d in _ints(task_doc.get("fixedDueMonthDaysStoreVal")) if 1 <= d <= 31)


def schedule_months(task_doc: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(m for m in _ints(task_doc.get("fixedDueMonthsStoreVal")) if 1 <= m <= 12)


def season_months(task_doc: Mapping[str, Any]) -> tuple[int, ...]:
    """Months a seasonal task is active in; empty for all-year tasks."""
    if not task_doc.get("isSeasonal"):
        return ()
    return tuple(m for m in _ints(task_doc.get("activeMonths")) if 1 <= m <= 12)


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _on_schedule(
    day: date, weekdays: tuple[int, ...], month_days: tuple[int, ...], months: tuple[int, ...], season: tuple[int, ...]
) -> bool:
    if season and day.month not in season:
        return False
    if months and day.month not in months:
        return False
    if weekdays and day.isoweekday() not in weekdays:
        return False
    if month_days:
        # The 31st in a 30-day month falls on its last day.
        last = calendar.monthrange(day.year, day.month)[1]
        return day.day in {min(d, last) for d in month_days}
    if months and not weekdays:
        return day.day == 1  # "every October": due from the 1st
    return True


def compute_fixed_due(task_doc: Mapping[str, Any], last_day: date) -> date | None:
    """First scheduled day after the last completion (local date).

    Tody backdates a new task's first action to the previous scheduled day, so this also
    gives the right first due date. For every N weeks/months/years, the day must also be
    at least N-1 periods after the last completion.
    """
    weekdays, month_days, months = schedule_weekdays(task_doc), schedule_month_days(task_doc), schedule_months(task_doc)
    if not (weekdays or month_days or months):
        return None
    periods = max(_as_int(task_doc.get("frequency"), 1), 1) - 1
    frequency_type = _as_int(task_doc.get("frequencyTypeStoreVal"))
    if frequency_type == FREQUENCY_WEEKS:
        earliest = last_day + timedelta(weeks=periods)
    elif frequency_type == FREQUENCY_MONTHS:
        earliest = _add_months(last_day, periods)
    elif frequency_type == FREQUENCY_YEARS:
        earliest = _add_months(last_day, 12 * periods)
    else:
        earliest = last_day
    season = season_months(task_doc)
    day = earliest + timedelta(days=1)
    for _ in range(_MAX_SCAN_DAYS):
        if _on_schedule(day, weekdays, month_days, months, season):
            return day
        day += timedelta(days=1)
    return None


def apply_season(due: date, season: tuple[int, ...]) -> date:
    """Move a due date outside the season to the first day of the next active month."""
    if not season or due.month in season:
        return due
    day = due.replace(day=1)
    for _ in range(12):
        day = _add_months(day, 1)
        if day.month in season:
            return day
    return due


def is_fixed_schedule(task_doc: Mapping[str, Any]) -> bool:
    return _as_int(task_doc.get("taskTypeStoreVal")) == TASK_TYPE_FIXED or bool(
        schedule_weekdays(task_doc) or schedule_month_days(task_doc) or schedule_months(task_doc)
    )


def compute_due(
    task_doc: Mapping[str, Any],
    baseline: datetime | None,
    *,
    now: datetime,
    tz: tzinfo,
    pauses: list[_Range],
    vacations: list[_Range],
) -> date | None:
    """Local due date: a fixed schedule or the interval rule, then limited to the season."""
    if baseline is None:
        return None
    forced = _as_datetime(task_doc.get("forcedDueOn"))
    due: date | None
    if is_fixed_schedule(task_doc) and not (forced is not None and forced > baseline):
        due = compute_fixed_due(task_doc, baseline.astimezone(tz).date())
    else:
        due_dt = compute_due_datetime(task_doc, baseline, now=now, pauses=pauses, vacations=vacations)
        due = due_dt.astimezone(tz).date() if due_dt is not None else None
    return apply_season(due, season_months(task_doc)) if due is not None else None


# --- Snapshot parsing -------------------------------------------------------

def _docs(snapshot: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    return [d for d in _as_list(snapshot.get(key)) if isinstance(d, Mapping)]


def _doc_id(doc: Mapping[str, Any], *keys: str) -> str | None:
    for key in ("_id", *keys):
        value = doc.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _vacation_ranges(snapshot: Mapping[str, Any], date_ranges: Mapping[str, Mapping[str, Any]]) -> list[_Range]:
    # vacationList format is unverified (empty in captured data): accept dateRange ids
    # (any type) or inline dicts with startDate/endDate.
    ranges: list[_Range] = []
    for plan in _docs(snapshot, "planSpecifications"):
        ranges.extend(_resolve_ranges(plan.get("vacationList"), date_ranges, type_filter=None))
    return ranges


def parse_snapshot(snapshot: Mapping[str, list[dict[str, Any]]], *, now: datetime, tz: tzinfo) -> TodyData:
    """Build TodyData from TodyClient.fetch_snapshot() output.

    `Task.last_done` is converted to `tz` here, so describe() can format it as a local date.
    """
    now = _as_datetime(now) or datetime.now(UTC)

    areas: dict[str, Area] = {}
    for doc in _docs(snapshot, "areas"):
        if (area_id := _doc_id(doc, "areaID")) is not None:
            areas[area_id] = Area(id=area_id, name=str(doc.get("areaName") or ""))

    participants: dict[str, Participant] = {}
    for doc in _docs(snapshot, "users"):
        if (user_id := _doc_id(doc, "userID")) is not None:
            participants[user_id] = Participant(id=user_id, name=str(doc.get("userName") or ""))

    date_ranges: dict[str, Mapping[str, Any]] = {}
    for doc in _docs(snapshot, "dateRanges"):
        if (range_id := _doc_id(doc, "dateRangeID")) is not None:
            date_ranges[range_id] = doc

    actions_by_task: dict[str, list[Mapping[str, Any]]] = {}
    for doc in _docs(snapshot, "actions"):
        task_id = doc.get("belongsToTaskID")
        if isinstance(task_id, str):
            actions_by_task.setdefault(task_id, []).append(doc)

    vacations = _vacation_ranges(snapshot, date_ranges)
    on_vacation = is_on_vacation(vacations, now)

    tasks: dict[str, Task] = {}
    for doc in _docs(snapshot, "tasks"):
        task_id = _doc_id(doc, "taskID")
        if task_id is None:
            continue
        actions = actions_by_task.get(task_id, [])
        pauses = _resolve_ranges(doc.get("taskPauses"), date_ranges, type_filter=DATE_RANGE_TASK_PAUSE)
        baseline = compute_baseline(doc, actions)
        due = compute_due(doc, baseline, now=now, tz=tz, pauses=pauses, vacations=vacations)
        last_done, last_done_by = compute_last_done(actions)
        area_id = doc.get("belongsToAreaID")
        tasks[task_id] = Task(
            id=task_id,
            name=str(doc.get("taskName") or ""),
            area_id=area_id if isinstance(area_id, str) and area_id else None,
            frequency=_as_int(doc.get("frequency")),
            frequency_type=_as_int(doc.get("frequencyTypeStoreVal")),
            frequency_minutes=_as_int(doc.get("frequencyMinutes")),
            due=due,
            last_done=last_done.astimezone(tz) if last_done is not None else None,
            last_done_by=last_done_by,
            turn_participant_ids=compute_turn(doc, last_done_by, participants),
            paused=is_paused(pauses, now),
            archived=is_archived(doc),
            weekdays=schedule_weekdays(doc),
            month_days=schedule_month_days(doc),
            months=schedule_months(doc),
            active_months=season_months(doc),
        )

    sync_name = ""
    for meta in _docs(snapshot, "fbMetadata"):
        if isinstance(name := meta.get("dataSyncName"), str) and name:
            sync_name = name
            break

    return TodyData(
        sync_name=sync_name,
        areas=areas,
        participants=participants,
        tasks=tasks,
        on_vacation=on_vacation,
    )


# --- Rule 7: due list -------------------------------------------------------

def tasks_due_by(data: TodyData, until: date) -> list[Task]:
    """Active tasks (not paused, not archived, not during vacation) due on or before `until`, sorted by due date then name."""
    if data.on_vacation:
        return []
    due = [
        t for t in data.tasks.values()
        if t.due is not None and not t.paused and not t.archived and t.due <= until
    ]
    return sorted(due, key=lambda t: (t.due, t.name.casefold(), t.id))


# --- Rule 8: description ----------------------------------------------------

_UNITS = {
    "en": {FREQUENCY_WEEKS: ("week", "weeks"), FREQUENCY_MONTHS: ("month", "months"), FREQUENCY_YEARS: ("year", "years")},
    "nl": {FREQUENCY_WEEKS: ("week", "weken"), FREQUENCY_MONTHS: ("maand", "maanden"), FREQUENCY_YEARS: ("jaar", "jaren")},
}
_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "nl": ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"),
}


def _lang(language: str) -> str:
    return "nl" if language.lower().startswith("nl") else "en"


def frequency_text(task: Task, language: str) -> str:
    lang = _lang(language)
    unit = _UNITS[lang].get(task.frequency_type)
    if unit is None:
        # Unknown type: fall back to whole days from frequencyMinutes.
        days = max(task.frequency_minutes // 1440, 0)
        unit, count = (("dag", "dagen") if lang == "nl" else ("day", "days")), days
    else:
        count = task.frequency
    if count == 1:
        if lang == "nl":
            # "jaar" is neuter: "Elk jaar"; "week"/"maand"/"dag" take "Elke".
            return f"{'Elk' if unit[0] == 'jaar' else 'Elke'} {unit[0]}"
        return f"Every {unit[0]}"
    return f"{'Elke' if lang == 'nl' else 'Every'} {count} {unit[1]}"


_WEEKDAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "nl": ("ma", "di", "wo", "do", "vr", "za", "zo"),
}


def _join(items: list[str], lang: str) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {'en' if lang == 'nl' else 'and'} {items[-1]}"


def schedule_text(task: Task, language: str) -> str:
    """Fixed schedule and season, e.g. " on Mon, Wed and Fri (Dec only)"; empty when not used."""
    lang = _lang(language)
    nl = lang == "nl"
    parts = []
    if task.weekdays:
        parts.append(f"{'op' if nl else 'on'} {_join([_WEEKDAYS[lang][d - 1] for d in task.weekdays], lang)}")
    if task.month_days:
        days = _join([str(d) for d in task.month_days], lang)
        parts.append(f"op dag {days}" if nl else f"on day {days}")
    if task.months:
        parts.append(f"{'in' if nl else 'in'} {_join([_MONTHS[lang][m - 1] for m in task.months], lang)}")
    text = "".join(f" {p}" for p in parts)
    if task.active_months:
        months = _join([_MONTHS[lang][m - 1] for m in task.active_months], lang)
        text += f" (alleen {months})" if nl else f" ({months} only)"
    return text


def last_done_text(task: Task, data: TodyData, language: str) -> str:
    lang = _lang(language)
    if task.last_done is None:
        return "Nog nooit gedaan" if lang == "nl" else "Never done yet"
    d = task.last_done.date()  # already local (converted in parse_snapshot)
    when = f"{d.day} {_MONTHS[lang][d.month - 1]} {d.year}"
    text = f"Laatst gedaan {when}" if lang == "nl" else f"Last done {when}"
    person = data.participants.get(task.last_done_by) if task.last_done_by else None
    if person is not None and person.name:
        text += f" {'door' if lang == 'nl' else 'by'} {person.name}"
    return text


def describe(task: Task, data: TodyData, language: str) -> str:
    """Todo item description: frequency and last completion, in English or Dutch ("nl")."""
    return f"{frequency_text(task, language)}{schedule_text(task, language)} · {last_done_text(task, data, language)}"
