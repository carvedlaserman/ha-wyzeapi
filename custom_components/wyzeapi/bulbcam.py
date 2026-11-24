"""Helpers for the Wyze Bulb Cam (HL_BC) integrated light.

wyzeapy sends the Bulb Cam light through ``run_action`` and only checks the
top-level ``code`` of the reply. Wyze can answer ``code: "1"`` (SUCCESS) while
the nested ``data.result`` says the action was not carried out, so the command
fails silently. These helpers make that failure visible and log the camera's
raw properties so the property that tracks the light can be identified.
"""

from __future__ import annotations

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
RUN_ACTION_SUCCESS = 1


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
