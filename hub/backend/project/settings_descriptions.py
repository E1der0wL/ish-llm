"""Complete application-facing schema descriptions without altering validation."""

from hub.locales.keys import MessageKey, MessagePattern
from llm.core.schema import SchemaKeyword
from llm.core.policies import ApprovalPolicy, PolicySection
from llm.core.models import ProjectConfig
from llm.services.configuration_records import ProjectConfigForm
from .settings_help import SettingsHelp


DESCRIPTIONS = MessagePattern.settings_description_names()
DESCRIPTION_PATH_SEPARATOR = "."
SCHEMA_ALTERNATIVES = (SchemaKeyword.ONE_OF, SchemaKeyword.ANY_OF, SchemaKeyword.ALL_OF)


def describe_options(schema, t, path=(), *, scope=SettingsHelp.PROJECT):
    if not isinstance(schema, dict):
        return
    approval = {
        ApprovalPolicy.enabled.__name__: MessageKey.APPROVAL_POLICY_ENABLED,
        ApprovalPolicy.risk_scheme.__name__: MessageKey.APPROVAL_POLICY_SCHEME,
        ApprovalPolicy.rules.__name__: MessageKey.APPROVAL_POLICY_RULES,
    }
    help_key = SettingsHelp.message(scope, path)
    if help_key is not None:
        schema[SchemaKeyword.DESCRIPTION] = t(help_key)
    if (scope == SettingsHelp.PROJECT
            and path[:-1] == (ProjectConfigForm.config.__name__, ProjectConfig.POLICIES, PolicySection.APPROVAL)
            and path[-1] in approval):
        schema[SchemaKeyword.DESCRIPTION] = t(approval[path[-1]])
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
        describe_options(child, t, (*path, key), scope=scope)
    for keyword in SCHEMA_ALTERNATIVES:
        for child in schema.get(keyword, ()):
            describe_options(child, t, path, scope=scope)
