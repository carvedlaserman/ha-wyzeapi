"""Helpers for the Wyze Bulb Cam (HL_BC) integrated light.

wyzeapy sends the Bulb Cam light through ``run_action`` and only checks the
top-level ``code`` of the reply. Wyze can answer ``code: "1"`` (SUCCESS) while
the nested ``data.result`` says the action was not carried out, so the command
fails silently. These helpers make that failure visible and log the camera's
raw properties so the property that tracks the light can be identified.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import time
from typing import Any

from wyzeapy.const import APP_NAME, APP_VER, APP_VERSION, PHONE_ID, PHONE_SYSTEM_TYPE
from wyzeapy.services.camera_service import Camera, CameraService
from wyzeapy.utils import check_for_errors_standard

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

BULB_CAM_MODEL = "HL_BC"
RUN_ACTION_URL = "https://api.wyzecam.com/app/v2/auto/run_action"
PROPERTY_LIST_URL = "https://api.wyzecam.com/app/v2/device/get_property_list"
SET_PROPERTY_URL = "https://api.wyzecam.com/app/v2/device/set_property"
DEVICE_LIST_SET_PROPERTY_LIST_URL = (
    "https://api.wyzecam.com/app/v2/device_list/set_property_list"
)
RUN_ACTION_SUCCESS = 1

# P1056 tracks the Bulb Cam light: "1" = on, "2" = off (verified against the
# Wyze app toggling the bulb).
LIGHT_PID = "P1056"
LIGHT_ON = "1"
LIGHT_OFF = "2"
VERIFY_ATTEMPTS = 5
VERIFY_INTERVAL = 2.0


def _base_payload(service: CameraService) -> dict[str, Any]:
    """Return the fields wyzeapy sends with every app/v2 request."""
    return {
        "phone_system_type": PHONE_SYSTEM_TYPE,
        "app_version": APP_VERSION,
        "app_ver": APP_VER,
        "sc": "9f275790cab94a72bd206c8876429f3c",
        "ts": int(time.time()),
        "sv": "9d74946e652647e9b6c9d59326aef104",
        "access_token": service._auth_lib.token.access_token,  # noqa: SLF001
        "phone_id": PHONE_ID,
        "app_name": APP_NAME,
    }


async def run_action_checked(
    service: CameraService, camera: Camera, action: str
) -> None:
    """Run a camera action and raise if Wyze did not carry it out."""
    auth_lib = service._auth_lib  # noqa: SLF001
    await auth_lib.refresh_if_should()
    payload = _base_payload(service) | {
        "provider_key": camera.product_model,
        "instance_id": camera.mac,
        "action_key": action,
        "action_params": {},
        "custom_string": "",
    }
    response = await auth_lib.post(RUN_ACTION_URL, json=payload)
    check_for_errors_standard(service, response)

    result = (response.get("data") or {}).get("result")
    _LOGGER.debug(
        "Bulb Cam %s: run_action %s returned data.result=%s",
        camera.nickname,
        action,
        result,
    )
    if result is not None and int(result) != RUN_ACTION_SUCCESS:
        raise HomeAssistantError(
            f"Wyze did not carry out '{action}' on {camera.nickname} "
            f"(run_action data.result={result})"
        )


async def fetch_raw_properties(
    service: CameraService, camera: Camera
) -> dict[str, tuple[str, int]]:
    """Return every property Wyze reports for the camera as pid -> (value, ts)."""
    auth_lib = service._auth_lib  # noqa: SLF001
    await auth_lib.refresh_if_should()
    payload = _base_payload(service) | {
        "device_model": camera.product_model,
        "device_mac": camera.mac,
        "target_pid_list": [],
    }
    response = await auth_lib.post(PROPERTY_LIST_URL, json=payload)
    check_for_errors_standard(service, response)
    return {
        prop["pid"]: (str(prop.get("value")), int(prop.get("ts") or 0))
        for prop in response["data"]["property_list"]
    }


def log_property_changes(
    camera: Camera,
    previous: dict[str, tuple[str, int]] | None,
    current: dict[str, tuple[str, int]],
) -> None:
    """Log the full property list once, then only the properties that changed."""
    if previous is None:
        for pid in sorted(current, key=lambda p: (len(p), p)):
            value, ts = current[pid]
            _LOGGER.debug(
                "Bulb Cam %s property %s = %s (ts %s)",
                camera.nickname,
                pid,
                value,
                ts,
            )
        return

    for pid in sorted(set(previous) | set(current), key=lambda p: (len(p), p)):
        old = previous.get(pid)
        new = current.get(pid)
        if old is None or new is None or old[0] != new[0] or old[1] != new[1]:
            _LOGGER.debug(
                "Bulb Cam %s property CHANGED %s: %s -> %s",
                camera.nickname,
                pid,
                old,
                new,
            )


async def _device_list_set_property(
    service: CameraService, camera: Camera, value: str
) -> None:
    """Set P1056 through the bulk device_list endpoint the Wyze app uses."""
    auth_lib = service._auth_lib  # noqa: SLF001
    await auth_lib.refresh_if_should()
    payload = _base_payload(service) | {
        # This endpoint is signed with a different sc/sv pair (as in wyzeapy).
        "sc": "a626948714654991afd3c0dbd7cdb901",
        "sv": "ddb9baef0d7f44379cd6bfaa8698e682",
        "device_list": [
            {
                "device_mac": camera.mac,
                "device_model": camera.product_model,
                "property_list": [{"pid": LIGHT_PID, "pvalue": value}],
            }
        ],
    }
    response = await auth_lib.post(DEVICE_LIST_SET_PROPERTY_LIST_URL, json=payload)
    _LOGGER.debug("Bulb Cam device_list/set_property_list reply: %s", response)
    check_for_errors_standard(service, response)


async def _set_property(service: CameraService, camera: Camera, value: str) -> None:
    """Set P1056 through the single-device set_property endpoint."""
    auth_lib = service._auth_lib  # noqa: SLF001
    await auth_lib.refresh_if_should()
    payload = _base_payload(service) | {
        "pid": LIGHT_PID,
        "pvalue": value,
        "device_model": camera.product_model,
        "device_mac": camera.mac,
    }
    response = await auth_lib.post(SET_PROPERTY_URL, json=payload)
    _LOGGER.debug("Bulb Cam set_property reply: %s", response)
    check_for_errors_standard(service, response)


async def _run_action(service: CameraService, camera: Camera, value: str) -> None:
    """Send floodlight_on/off through run_action (wyzeapy's method)."""
    await run_action_checked(
        service,
        camera,
        "floodlight_on" if value == LIGHT_ON else "floodlight_off",
    )


LightMethod = Callable[[CameraService, Camera, str], Awaitable[None]]
LIGHT_METHODS: dict[str, LightMethod] = {
    "device_list_set_property_list": _device_list_set_property,
    "set_property": _set_property,
    "run_action": _run_action,
}


async def read_light_value(service: CameraService, camera: Camera) -> str | None:
    """Return the current P1056 value, or None if Wyze did not report it."""
    props = await fetch_raw_properties(service, camera)
    return props.get(LIGHT_PID, (None, 0))[0]


async def _wait_for_value(service: CameraService, camera: Camera, value: str) -> bool:
    for _ in range(VERIFY_ATTEMPTS):
        await asyncio.sleep(VERIFY_INTERVAL)
        if await read_light_value(service, camera) == value:
            return True
    return False


async def set_light(
    service: CameraService,
    camera: Camera,
    turn_on: bool,
    preferred: str | None = None,
) -> str:
    """Switch the Bulb Cam light and confirm it via P1056.

    Tries each known control method, starting with ``preferred``, until the
    camera reports the requested state. Returns the name of the method that
    worked. Raises HomeAssistantError if none did.
    """
    target = LIGHT_ON if turn_on else LIGHT_OFF
    if await read_light_value(service, camera) == target:
        _LOGGER.debug("Bulb Cam %s already %s", camera.nickname, target)
        return preferred or "already_set"

    order = list(LIGHT_METHODS)
    if preferred in LIGHT_METHODS:
        order.remove(preferred)
        order.insert(0, preferred)

    failures: list[str] = []
    for name in order:
        try:
            await LIGHT_METHODS[name](service, camera, target)
        except HomeAssistantError as err:
            failures.append(f"{name}: {err}")
            _LOGGER.debug("Bulb Cam %s: %s failed: %s", camera.nickname, name, err)
            continue
        if await _wait_for_value(service, camera, target):
            _LOGGER.debug(
                "Bulb Cam %s: %s switched the light to %s",
                camera.nickname,
                name,
                target,
            )
            return name
        failures.append(f"{name}: accepted but {LIGHT_PID} did not change")
        _LOGGER.debug(
            "Bulb Cam %s: %s accepted but %s did not change",
            camera.nickname,
            name,
            LIGHT_PID,
        )

    raise HomeAssistantError(
        f"Could not turn {camera.nickname} light {'on' if turn_on else 'off'}: "
        + "; ".join(failures)
    )
