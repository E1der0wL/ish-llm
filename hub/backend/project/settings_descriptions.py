"""Complete application-facing schema descriptions without altering validation."""


DESCRIPTIONS = {"title", "components", "model", "api_base", "api_key", "temperature", "max_tokens",
                "timeout", "system_prompt", "max_attempts", "max_retries", "delay_seconds", "max_delay_seconds"}


def describe_options(schema, t, path=()):
    if not isinstance(schema, dict):
        return
    if path and not schema.get("description"):
        schema["description"] = (t("settings_desc_" + path[-1]) if path[-1] in DESCRIPTIONS else
                                 t("settings_option_description", name=".".join(path)))
    for key, child in schema.get("properties", {}).items():
        describe_options(child, t, (*path, key))
    for keyword in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(keyword, ()):
            describe_options(child, t, path)
