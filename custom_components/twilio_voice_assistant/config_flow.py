"""Config flow skeleton for Twilio Voice Assistant."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN


class TwilioVoiceAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Twilio Voice Assistant."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create the initial integration entry."""
        if user_input is not None:
            return self.async_create_entry(
                title="Twilio Voice Assistant",
                data=user_input,
            )

        schema = vol.Schema({
            vol.Optional("bridge_url"): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema)
