"""Tests for the Wyze Bulb Cam (HL_BC) light."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.components.light import ColorMode
from homeassistant.exceptions import HomeAssistantError

from custom_components.wyzeapi import bulbcam
from custom_components.wyzeapi.light import WyzeCamerafloodlight


def _camera(model: str = "HL_BC") -> SimpleNamespace:
    return SimpleNamespace(
        mac="AABBCCDDEEFF",
        nickname="Driveway Cam",
        product_model=model,
        floodlight=True,
        available=True,
    )


def _service(response: dict) -> SimpleNamespace:
    auth_lib = SimpleNamespace(
        refresh_if_should=AsyncMock(),
        post=AsyncMock(return_value=response),
        token=SimpleNamespace(access_token="token", expired=False),
    )
    return SimpleNamespace(
        _auth_lib=auth_lib,
        floodlight_on=AsyncMock(),
        floodlight_off=AsyncMock(),
    )


def _run_action_reply(result: int) -> dict:
    return {
        "code": "1",
        "msg": "SUCCESS",
        "data": {"result": result, "instance_id": "AABBCCDDEEFF"},
    }


def _entity(camera, service) -> WyzeCamerafloodlight:
    entity = WyzeCamerafloodlight(camera, service, "bulbcam")
    entity.async_schedule_update_ha_state = Mock()
    return entity


@pytest.mark.asyncio
async def test_rejected_run_action_raises_and_keeps_state() -> None:
    """data.result != 1 is surfaced as an error, with no phantom toggle."""
    camera = _camera()
    service = _service(_run_action_reply(3))
    entity = _entity(camera, service)

    with pytest.raises(HomeAssistantError, match="result=3"):
        await entity.async_turn_off()

    assert camera.floodlight is True
    payload = service._auth_lib.post.await_args.kwargs["json"]
    assert payload["action_key"] == "floodlight_off"
    assert payload["provider_key"] == "HL_BC"
    assert payload["instance_id"] == "AABBCCDDEEFF"


@pytest.mark.asyncio
async def test_accepted_run_action_updates_state() -> None:
    camera = _camera()
    service = _service(_run_action_reply(1))
    entity = _entity(camera, service)

    await entity.async_turn_off()

    assert camera.floodlight is False
    entity.async_schedule_update_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_other_cameras_still_use_wyzeapy() -> None:
    camera = _camera("LD_CFP")
    service = _service({})
    entity = WyzeCamerafloodlight(camera, service, "floodlight")
    entity.async_schedule_update_ha_state = Mock()

    await entity.async_turn_on()

    service.floodlight_on.assert_awaited_once_with(camera)
    service._auth_lib.post.assert_not_awaited()


def test_supported_color_modes_is_a_set() -> None:
    entity = WyzeCamerafloodlight(_camera(), _service({}), "bulbcam")
    assert entity.supported_color_modes == {ColorMode.ONOFF}


@pytest.mark.asyncio
async def test_fetch_raw_properties_keeps_unknown_pids() -> None:
    service = _service(
        {
            "code": "1",
            "data": {
                "property_list": [
                    {"pid": "P1056", "value": "1", "ts": 10},
                    {"pid": "P9999", "value": "x", "ts": 0},
                ]
            },
        }
    )
    props = await bulbcam.fetch_raw_properties(service, _camera())
    assert props == {"P1056": ("1", 10), "P9999": ("x", 0)}


def test_log_property_changes_only_logs_diffs(caplog) -> None:
    camera = _camera()
    caplog.set_level(logging.DEBUG, logger=bulbcam.__name__)
    before = {"P1056": ("1", 10), "P3": ("1", 5)}
    after = {"P1056": ("2", 20), "P3": ("1", 5)}

    bulbcam.log_property_changes(camera, before, after)

    assert "CHANGED P1056" in caplog.text
    assert "P3" not in caplog.text
