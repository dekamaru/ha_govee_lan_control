"""Config flow for the Govee LAN Control integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_IP_ADDRESS, CONF_NAME

from .const import (
    CONF_DEVICE_ID,
    CONF_SKU,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    PROBE_TIMEOUT,
)
from .controller import GoveeDiscoveredDevice, GoveeLanController

_LOGGER = logging.getLogger(__name__)


class GoveeLanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one Govee lamp per config entry, by discovery or by IP."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, GoveeDiscoveredDevice] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Entry point: choose between discovery and manual IP."""
        return self.async_show_menu(
            step_id="user", menu_options=["discover", "manual"]
        )

    # -------------------------------------------------------------- discovery

    async def async_step_discover(self, user_input: dict[str, Any] | None = None):
        """Scan the network and let the user pick one of the found lamps."""
        if user_input is not None:
            device = self._discovered[user_input["device"]]
            await self.async_set_unique_id(
                device.device_id, raise_on_progress=False
            )
            self._abort_if_unique_id_configured(updates={"ip": device.ip})
            title = f"Govee {device.sku} ({device.ip})"
            return self.async_create_entry(
                title=title,
                data={
                    "ip": device.ip,
                    CONF_DEVICE_ID: device.device_id,
                    CONF_SKU: device.sku,
                    "name": title,
                },
            )

        try:
            found = await self._with_controller(
                lambda c: c.discover(DISCOVERY_TIMEOUT)
            )
        except OSError:
            return self.async_abort(reason="port_in_use")

        configured = {
            entry.unique_id for entry in self._async_current_entries()
        }
        self._discovered = {
            device.device_id: device
            for device in found.values()
            if device.device_id not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        options = {
            device_id: f"{device.sku or 'Govee'} — {device.ip} ({device_id})"
            for device_id, device in self._discovered.items()
        }
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({vol.Required("device"): vol.In(options)}),
        )

    # ----------------------------------------------------------------- manual

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        """Add a lamp by IP address, verifying it answers on the LAN API."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ip = user_input[CONF_IP_ADDRESS].strip()
            try:
                result = await self._with_controller(
                    lambda c: c.probe(ip, PROBE_TIMEOUT)
                )
            except OSError:
                return self.async_abort(reason="port_in_use")

            if result is None:
                errors["base"] = "cannot_connect"
            else:
                if isinstance(result, GoveeDiscoveredDevice):
                    unique_id: str = result.device_id
                    sku: str | None = result.sku
                else:
                    unique_id = ip
                    sku = None
                await self.async_set_unique_id(unique_id, raise_on_progress=False)
                self._abort_if_unique_id_configured(updates={"ip": ip})
                name = user_input.get(CONF_NAME) or (
                    f"Govee {sku} ({ip})" if sku else f"Govee ({ip})"
                )
                return self.async_create_entry(
                    title=name,
                    data={
                        "ip": ip,
                        CONF_DEVICE_ID: unique_id,
                        CONF_SKU: sku,
                        "name": name,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_IP_ADDRESS): str,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    # ---------------------------------------------------------------- helpers

    async def _with_controller(self, func):
        """Run `func` on the running controller, or on a temporary one.

        The listening port 4002 can only be bound once, so if the integration
        is already set up we must reuse its controller.
        """
        data = self.hass.data.get(DOMAIN)
        if data and (controller := data.get("controller")):
            return await func(controller)

        controller = GoveeLanController()
        await controller.start()  # may raise OSError -> handled by caller
        try:
            return await func(controller)
        finally:
            controller.stop()
