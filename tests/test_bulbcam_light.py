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


class FakeWyze:
    """Fake Wyze cloud for one Bulb Cam: P1056 changes only for working methods."""

    def __init__(self, light: str, working: set[str]) -> None:
        self.light = light
        self.working = working
        self.calls: list[str] = []

    async def post(self, url: str, json: dict) -> dict:
        if url == bulbcam.PROPERTY_LIST_URL:
            return {
                "code": "1",
                "data": {
                    "property_list": [{"pid": "P1056", "value": self.light, "ts": 1}]
                },
            }
        if url == bulbcam.DEVICE_LIST_SET_PROPERTY_LIST_URL:
            name = "device_list_set_property_list"
            value = json["device_list"][0]["property_list"][0]["pvalue"]
            reply = {"code": "1", "msg": "SUCCESS"}
        elif url == bulbcam.SET_PROPERTY_URL:
            name = "set_property"
            value = json["pvalue"]
            reply = {"code": "1", "msg": "SUCCESS"}
        elif url == bulbcam.RUN_ACTION_URL:
            name = "run_action"
            value = "1" if json["action_key"] == "floodlight_on" else "2"
            ok = name in self.working
            reply = {"code": "1", "data": {"result": 1 if ok else 3}}
        else:
            raise AssertionError(url)
        self.calls.append(name)
        if name in self.working:
            self.light = value
        return reply


def _fake_service(fake: FakeWyze) -> SimpleNamespace:
    service = _service({})
    service._auth_lib.post = AsyncMock(side_effect=fake.post)
    return service


@pytest.fixture(autouse=True)
def _no_verify_delay(monkeypatch) -> None:
    monkeypatch.setattr(bulbcam, "VERIFY_INTERVAL", 0)


@pytest.mark.asyncio
async def test_bulk_endpoint_switches_light_first() -> None:
    camera = _camera()
    fake = FakeWyze("1", {"device_list_set_property_list"})
    entity = _entity(camera, _fake_service(fake))

    await entity.async_turn_off()

    assert fake.light == "2"
    assert fake.calls == ["device_list_set_property_list"]
    assert camera.floodlight is False
    assert entity._light_method == "device_list_set_property_list"


@pytest.mark.asyncio
async def test_falls_back_and_remembers_working_method() -> None:
    camera = _camera()
    fake = FakeWyze("1", {"set_property"})
    entity = _entity(camera, _fake_service(fake))

    await entity.async_turn_off()
    assert fake.calls == ["device_list_set_property_list", "set_property"]
    assert entity._light_method == "set_property"

    fake.calls.clear()
    await entity.async_turn_on()
    assert fake.calls == ["set_property"]
    assert fake.light == "1"
    assert camera.floodlight is True


@pytest.mark.asyncio
async def test_all_methods_fail_raises_and_keeps_state() -> None:
    camera = _camera()
    fake = FakeWyze("1", set())
    entity = _entity(camera, _fake_service(fake))

    with pytest.raises(HomeAssistantError, match="run_action data.result=3"):
        await entity.async_turn_off()

    assert fake.calls == [
        "device_list_set_property_list",
        "set_property",
        "run_action",
    ]
    assert camera.floodlight is True
    entity.async_schedule_update_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_already_in_requested_state_sends_nothing() -> None:
    camera = _camera()
    fake = FakeWyze("1", set())
    entity = _entity(camera, _fake_service(fake))

    await entity.async_turn_on()

    assert fake.calls == []
    assert entity._light_method is None


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
