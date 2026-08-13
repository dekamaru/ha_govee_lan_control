"""Light platform for the Govee LAN Control integration."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import AVAILABILITY_TIMEOUT, CONF_DEVICE_ID, CONF_SKU, DOMAIN
from .controller import GoveeLanController

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=10)
PARALLEL_UPDATES = 0

# Delay between a command burst and the confirming status request.
REFRESH_DELAY = 1.0
# Small gap between consecutive UDP commands so the lamp keeps up.
COMMAND_GAP = 0.05


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light entity for one config entry (one lamp)."""
    controller: GoveeLanController = hass.data[DOMAIN]["controller"]
    async_add_entities([GoveeLanLight(controller, entry)])


class GoveeLanLight(LightEntity):
    """A Govee lamp controlled over the LAN API."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = True
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
    _attr_min_color_temp_kelvin = 2000
    _attr_max_color_temp_kelvin = 9000

    def __init__(self, controller: GoveeLanController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._entry = entry
        self._ip: str = entry.data["ip"]
        self._device_id: str = entry.data.get(CONF_DEVICE_ID) or entry.data["ip"]
        sku = entry.data.get(CONF_SKU)

        self._attr_unique_id = self._device_id
        self._attr_color_mode = ColorMode.RGB
        # Optimistic until proven otherwise; flips to unavailable if the lamp
        # stays silent for AVAILABILITY_TIMEOUT.
        self._last_seen = time.monotonic()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer="Govee",
            model=sku,
            name=entry.data.get("name") or entry.title,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._controller.register(
                self._ip,
                self._device_id,
                self._handle_status,
                self._handle_ip_change,
            )
        )
        self._controller.request_status(self._ip)

    @property
    def available(self) -> bool:
        return (time.monotonic() - self._last_seen) < AVAILABILITY_TIMEOUT

    # ------------------------------------------------------------- push input

    @callback
    def _handle_status(self, status: dict | None) -> None:
        """Handle a devStatus reply (or a heartbeat when status is None)."""
        self._last_seen = time.monotonic()
        if status is not None:
            self._attr_is_on = status["on"]
            brightness = status.get("brightness")
            if brightness is not None:
                self._attr_brightness = round(brightness * 255 / 100)
            color_temp = status.get("color_temp")
            if color_temp:
                self._attr_color_temp_kelvin = color_temp
                self._attr_color_mode = ColorMode.COLOR_TEMP
            else:
                rgb = status.get("rgb")
                if rgb is not None:
                    self._attr_rgb_color = rgb
                self._attr_color_mode = ColorMode.RGB
        self.async_write_ha_state()

    @callback
    def _handle_ip_change(self, new_ip: str) -> None:
        """The lamp answered a scan from a new IP (DHCP renewal)."""
        self._ip = new_ip
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, "ip": new_ip}
        )

    # ---------------------------------------------------------------- polling

    async def async_update(self) -> None:
        """Fire a status request; the reply arrives as a push update."""
        self._controller.request_status(self._ip)

    # --------------------------------------------------------------- commands

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.turn(self._ip, True)
        self._attr_is_on = True

        color_temp = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        brightness = kwargs.get(ATTR_BRIGHTNESS)

        if color_temp is not None:
            color_temp = max(2000, min(9000, int(color_temp)))
            await asyncio.sleep(COMMAND_GAP)
            self._controller.set_color_temp(self._ip, color_temp)
            self._attr_color_temp_kelvin = color_temp
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif rgb is not None:
            await asyncio.sleep(COMMAND_GAP)
            self._controller.set_rgb(self._ip, *rgb)
            self._attr_rgb_color = rgb
            self._attr_color_mode = ColorMode.RGB

        if brightness is not None:
            await asyncio.sleep(COMMAND_GAP)
            self._controller.set_brightness(
                self._ip, max(1, round(brightness * 100 / 255))
            )
            self._attr_brightness = brightness

        self.async_write_ha_state()
        self._schedule_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.turn(self._ip, False)
        self._attr_is_on = False
        self.async_write_ha_state()
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        """Confirm the optimistic state with a real status request."""
        async_call_later(self.hass, REFRESH_DELAY, self._request_status)

    @callback
    def _request_status(self, _now: Any) -> None:
        self._controller.request_status(self._ip)
