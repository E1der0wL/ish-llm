"""Complete application-facing schema descriptions without altering validation."""

from hub.locales.keys import MessageKey, MessagePattern
from llm.core.schema import SchemaKeyword


DESCRIPTIONS = MessagePattern.settings_description_names()
DESCRIPTION_PATH_SEPARATOR = "."
SCHEMA_ALTERNATIVES = (SchemaKeyword.ONE_OF, SchemaKeyword.ANY_OF, SchemaKeyword.ALL_OF)


def describe_options(schema, t, path=()):
    if not isinstance(schema, dict):
        return
    if path and not schema.get(SchemaKeyword.DESCRIPTION):
        schema[SchemaKeyword.DESCRIPTION] = (
            t(MessagePattern.settings_description(path[-1]))
            if path[-1] in DESCRIPTIONS
            else t(
                MessageKey.SETTINGS_OPTION_DESCRIPTION,
                name=DESCRIPTION_PATH_SEPARATOR.join(path),
            )
        )
    for key, child in schema.get(SchemaKeyword.PROPERTIES, {}).items():
        describe_options(child, t, (*path, key))
    for keyword in SCHEMA_ALTERNATIVES:
        for child in schema.get(keyword, ()):
            describe_options(child, t, path)
