"""Twilio Voice Assistant custom integration skeleton."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Twilio Voice Assistant integration placeholder."""
    hass.data.setdefault(DOMAIN, {})
    return True
