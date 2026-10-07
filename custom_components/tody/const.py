"""Constants for the Tody integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "tody"
# Name of the integration entry and its device (the Tody sync name is often a random phrase).
DEVICE_NAME: Final = "Tody"

# Tody's Firebase backend, as used by the Tody Android app.
FIREBASE_PROJECT_ID: Final = "tody-96d33"
# TODO(setup): Firebase web API key used by the Tody app. Not committed; see README "API key".
FIREBASE_API_KEY: Final = "__FIREBASE_API_KEY__"
FIREBASE_GMPID: Final = "1:845484751418:android:eea097bb074b6088cf1303"
# The API key is restricted to the Android app, so auth calls must identify as it.
ANDROID_PACKAGE: Final = "com.looploop.tody"
ANDROID_CERT_SHA1: Final = "C3837AC2FDF70306FA5E75315FBB14F09E9A3512"

# Config entry data
CONF_UID: Final = "uid"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_MASTERDATA_ID: Final = "masterdata_id"
CONF_PARTICIPANT_ID: Final = "participant_id"
CONF_INVITE_CODE: Final = "invite_code"

# Options
CONF_LOOKAHEAD_DAYS: Final = "lookahead_days"
DEFAULT_LOOKAHEAD_DAYS: Final = 1
MIN_LOOKAHEAD_DAYS: Final = 0
MAX_LOOKAHEAD_DAYS: Final = 7

# Tody area ids whose list has been placed in a name-matched HA area once (never redone).
CONF_AUTO_LINKED_AREAS: Final = "auto_linked_areas"

CONF_SCAN_INTERVAL: Final = "scan_interval"  # minutes
DEFAULT_SCAN_INTERVAL: Final = 15
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 240
