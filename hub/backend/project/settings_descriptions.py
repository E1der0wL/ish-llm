"""Complete application-facing schema descriptions without altering validation."""

from hub.locales.keys import MessageKey, MessagePattern


DESCRIPTIONS = {"title", "components", "model", "api_base", "api_key", "temperature", "max_tokens",
                "timeout", "system_prompt", "max_attempts", "max_retries", "delay_seconds", "max_delay_seconds"}


def describe_options(schema, t, path=()):
    if not isinstance(schema, dict):
        return
    if path and not schema.get("description"):
        schema["description"] = (t(MessagePattern.settings_description(path[-1])) if path[-1] in DESCRIPTIONS else
                                 t(MessageKey.SETTINGS_OPTION_DESCRIPTION, name=".".join(path)))
    for key, child in schema.get("properties", {}).items():
        describe_options(child, t, (*path, key))
    for keyword in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(keyword, ()):
            describe_options(child, t, path)
