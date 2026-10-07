# Tody for Home Assistant

A Home Assistant custom integration that shows your [Tody](https://todyapp.com/) cleaning tasks as to-do lists.

## What it does

- One **to-do list per Tody area**, plus a combined **All tasks** list. Lists that mix areas (All tasks and the person lists) add the area to each item, e.g. `Vacuum floor [Kitchen]`.
- One **to-do list per person**, showing the tasks where it is that person's turn. Tasks assigned to "everyone" appear on every person's list. The participant the integration joined as gets no list.
- Sensors **Overdue tasks** and **Due today** (counts).
- Lists show overdue tasks, tasks due today, and tasks due within the next N days (default 1). Tomorrow's tasks are marked through their due date, which Home Assistant shows in its normal relative format.
- Each item's description holds the task frequency and when and by whom it was last done.
- Paused tasks are hidden. English and Dutch translations.

> [!IMPORTANT]
> **Read-only.** Home Assistant never marks a Tody task as done and never changes Tody data. Ticking items in the to-do lists is not possible.
>
> **One exception:** setting up (and re-authenticating) joins your Tody data sync with an invite code. This is exactly what the Tody app does when a new device joins, and it adds the integration's anonymous login to the invited participant. Nothing else is ever written.
>
> Recommended: create a dedicated participant (for example "Home Assistant") in Tody and invite that one. Removing the integration leaves Tody untouched. To revoke access, remove that participant in the Tody app.

## Disclaimer

This project is unofficial and not affiliated with Looploop or Tody. It uses Tody's private sync backend (Firebase), which may change or break without notice. Use at your own risk.

## Requirements

- Home Assistant 2026.9 or newer.
- A Tody data sync that you can invite people to (Tody may require its premium sharing feature for this).
- A Firebase API key, see [API key](#api-key).

## Installation

### HACS (custom repository)

1. In HACS, open the menu, choose **Custom repositories**, and add `https://github.com/K-4U/hass-tody-sync` as type **Integration**.
2. Install **Tody** (pick the latest release) and restart Home Assistant.

HACS installs the `tody.zip` attached to each GitHub release, which includes the API key (see below). The default branch is hidden in HACS because it only has the placeholder key.

### Manual

Download `tody.zip` from the latest release, unpack it into `custom_components/tody` in your Home Assistant configuration, and restart.

## API key

`FIREBASE_API_KEY` in `custom_components/tody/const.py` is a placeholder (`__FIREBASE_API_KEY__`) in git. Release packages get the Firebase API key used by the Tody Android app from the repository secret `FIREBASE_API_KEY`. If you run the integration straight from a git checkout, replace the placeholder yourself; until then, setup stops with a message saying the key is missing.

## Setup

1. In Tody, add a participant (for example "Home Assistant") and invite them. Note the **invite code**.
2. In Home Assistant go to **Settings > Devices & services > Add integration > Tody**.
3. Enter the invite code.

## Options

Configure under the integration's **Configure** button.

| Option | Range | Default |
| --- | --- | --- |
| Look-ahead days (also show tasks due within N days) | 0-7 | 1 |
| Update interval (minutes) | 5-240 | 15 |

Data is polled; there is no live listener.

## Tody areas in Home Assistant areas

Each Tody area becomes its own device, e.g. **Kitchen tasks**, holding that area's to-do list. Home Assistant also puts the HA area in entity IDs, so the Kitchen list is `todo.kitchen_kitchen_tasks`. The Tody area's own name is in the list's `tody_area` attribute.

- When an area device is first created, it is placed in the HA area with the same name. If that area doesn't exist yet, Home Assistant creates it (standard behaviour for suggested areas).
- After that the area is yours: change it on the device page like any other device. The integration never moves it back.
- All tasks, the person lists and the sensors stay on the main **Tody** device.

The **Overdue tasks** and **Due today** sensors list every task under the `items` attribute (`name`, `area` = Tody area, `ha_area` = HA area of that area's device, `due`), plus `ha_areas`, the distinct HA areas with such tasks.

### Example: vacuum rooms that have a due vacuum task

Map your robot vacuum's rooms to HA areas first (vacuum entity settings → **Map vacuum segments to areas**). Then:

```yaml
alias: Vacuum rooms with a due Tody vacuum task
triggers:
  - trigger: time
    at: "10:00:00"
actions:
  - variables:
      vacuum_areas: >
        {{ ((state_attr('sensor.tody_overdue_tasks', 'items') or [])
            + (state_attr('sensor.tody_due_today', 'items') or []))
           | selectattr('ha_area')
           | selectattr('name', 'search', '(?i)vacuum floor')
           | map(attribute='ha_area') | unique | list }}
  - condition: template
    value_template: "{{ vacuum_areas | count > 0 }}"
  - action: vacuum.clean_area
    target:
      entity_id: vacuum.robot
    data:
      cleaning_area_id: "{{ vacuum_areas }}"
```

Adjust `vacuum floor` to match your task names, and `vacuum.robot` to your vacuum. Remember the integration is read-only: Tody still shows the task as due until someone ticks it off in the app.

## How due dates are calculated

- **Interval tasks** ("every 2 weeks") are due on their **last completion + the interval**. Time during which the task is paused, and vacation time, pushes the due date back, similar to the Tody app.
- **Fixed schedules** (weekdays, days of the month, months) are due on the **first scheduled day after the last completion**, e.g. Monday, Wednesday and Friday.
- **Seasonal tasks** only count days in their active months.

Only the date is used, not the time of day. Nothing is shown as due while a vacation is active. If something looks off, the integration's **Download diagnostics** gives the raw Tody data without logins or names, which helps with bug reports.

### Known limitations

- Vacation handling is unverified against the app.
- "Whose turn it is" is an approximation of Tody's rotation (the next person after whoever did it last).

Treat the Tody app as the source of truth if a date differs.

## Re-authentication

If access is revoked (for example the participant was removed), Home Assistant starts a re-authentication flow asking for a new invite code. After re-joining, the same entities are kept.

## Development

Start a development Home Assistant:

```sh
docker compose -f dev/docker-compose.yml up -d
```

It is available at http://localhost:8123 and loads the integration straight from `custom_components/tody`; restart the container after code changes.

Tests need Python 3.14 (the Home Assistant 2026.9 test harness requires it):

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
```
