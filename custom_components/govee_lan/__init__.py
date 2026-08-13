"""Govee LAN Control integration for Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, LISTEN_PORT
from .controller import GoveeLanController

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Govee lamp from a config entry.

    All entries share a single UDP controller bound to port 4002, because
    Govee devices always reply to that fixed port.
    """
    data = hass.data.setdefault(DOMAIN, {})
    if "controller" not in data:
        controller = GoveeLanController()
        try:
            await controller.start()
        except OSError as err:
            raise ConfigEntryNotReady(
                f"Cannot bind UDP port {LISTEN_PORT}. Another integration is "
                f"probably using it (e.g. the built-in Govee lights local "
                f"integration): {err}"
            ) from err
        data["controller"] = controller
        data["entries"] = set()
    data["entries"].add(entry.entry_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry; close the shared socket with the last one."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data.get(DOMAIN)
        if data is not None:
            data["entries"].discard(entry.entry_id)
            if not data["entries"]:
                data["controller"].stop()
                hass.data.pop(DOMAIN)
    return unload_ok
