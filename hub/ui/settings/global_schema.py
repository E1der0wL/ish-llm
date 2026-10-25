"""Localized global settings forms, using each setting Owner's declarations."""

from llm.core.schema import SchemaKeyword, SchemaType
from ...asset.icon import IconStyle
from ...config.general import GeneralSettings
from ...config.metadata import SettingMetadata
from ...config.profile import UserProfile
from ...config.restart import setting_metadata
from ...config.sections import PreferenceSection
from ...config.theme import HubTheme, ThemeMode
from ...locales.keys import MessageKey, MessagePattern
from ...widget.forms.settings import ChoiceField


class GlobalSettingsSchema:
    NERD_LABEL = "Nerd Font"
    PROFILE_DESCRIPTIONS = {
        UserProfile.display_name.__name__: MessageKey.SETTINGS_PROFILE_DESCRIPTION,
        UserProfile.email.__name__: MessageKey.SETTINGS_EMAIL_DESCRIPTION,
        UserProfile.phone.__name__: MessageKey.SETTINGS_PHONE_DESCRIPTION,
    }
    GENERAL_DESCRIPTIONS = {
        GeneralSettings.editor.__name__: MessageKey.SETTINGS_EDITOR_DESCRIPTION,
    }
    COLOR_FIELDS = (
        HubTheme.background.__name__,
        HubTheme.foreground.__name__,
        HubTheme.accent1.__name__,
        HubTheme.accent2.__name__,
        HubTheme.accent3.__name__,
        HubTheme.comment.__name__,
        HubTheme.comment2.__name__,
    )
    REFRESH_FIELDS = (
        HubTheme.output_refresh_interval,
        HubTheme.progress_refresh_interval,
    )
    INITIAL_APPEARANCE_FIELDS = (HubTheme.icon_style, HubTheme.mode, *REFRESH_FIELDS)

    @classmethod
    def initial_values(cls, name, values):
        if name == PreferenceSection.APPEARANCE:
            return {
                **{
                    attribute.__name__: SettingMetadata.initial(HubTheme, attribute)
                    for attribute in cls.INITIAL_APPEARANCE_FIELDS
                },
                **values,
            }
        return values

    @classmethod
    def describe(cls, name, t, minimum_width):
        descriptions = (
            {key: t(message) for key, message in cls.PROFILE_DESCRIPTIONS.items()}
            if name == PreferenceSection.PROFILE
            else (
                {key: t(message) for key, message in cls.GENERAL_DESCRIPTIONS.items()}
                if name == PreferenceSection.GENERAL
                else {
                    key: t(MessageKey.SETTINGS_COLOR_DESCRIPTION)
                    for key in cls.COLOR_FIELDS
                }
            )
        )
        properties = {
            key: {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.DESCRIPTION: description,
            }
            for key, description in descriptions.items()
        }
        for key, metadata in setting_metadata(name).items():
            if key in properties:
                properties[key].update(metadata)
        if name == PreferenceSection.APPEARANCE:
            properties[HubTheme.mode.__name__] = {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.ENUM: [value.value for value in ThemeMode],
                SchemaKeyword.TITLE: t(MessageKey.SETTINGS_THEME_MODE),
                SchemaKeyword.DESCRIPTION: t(
                    MessageKey.SETTINGS_THEME_MODE_DESCRIPTION
                ),
            }
            properties[HubTheme.icon_style.__name__] = {
                SchemaKeyword.TYPE: SchemaType.STRING,
                SchemaKeyword.ENUM: [value.value for value in IconStyle],
                SchemaKeyword.TITLE: t(MessageKey.SETTINGS_ICON_STYLE),
                SchemaKeyword.DESCRIPTION: t(MessageKey.SETTINGS_ICON_DESCRIPTION),
                ChoiceField.SCHEMA_KEY: {
                    IconStyle.NERD.value: cls.NERD_LABEL,
                    IconStyle.UNICODE.value: t(MessageKey.SETTINGS_ICON_UNICODE),
                },
            }
            for key in cls.COLOR_FIELDS:
                properties[key][SchemaKeyword.TITLE] = t(
                    MessagePattern.settings_theme(key)
                )
            properties[HubTheme.sidebar_width.__name__] = {
                SchemaKeyword.TYPE: SchemaType.INTEGER,
                SchemaKeyword.MINIMUM: minimum_width(),
                SchemaKeyword.MAXIMUM: HubTheme.MAXIMUM_SIDEBAR_WIDTH,
                SchemaKeyword.TITLE: t(MessageKey.SETTINGS_SIDEBAR_WIDTH_TITLE),
                SchemaKeyword.DESCRIPTION: t(MessageKey.SETTINGS_SIDEBAR_WIDTH),
            }
            for attribute in cls.REFRESH_FIELDS:
                key = attribute.__name__
                properties[key] = {
                    SchemaKeyword.TYPE: SchemaType.NUMBER,
                    SchemaKeyword.MINIMUM: HubTheme.MINIMUM_REFRESH_SECONDS,
                    SchemaKeyword.MAXIMUM: HubTheme.MAXIMUM_REFRESH_SECONDS,
                    SchemaKeyword.TITLE: t(MessagePattern.settings(key)),
                    SchemaKeyword.DESCRIPTION: t(MessagePattern.settings_help(key)),
                }
        return {
            SchemaKeyword.TYPE: SchemaType.OBJECT,
            SchemaKeyword.PROPERTIES: properties,
        }
