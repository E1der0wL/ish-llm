"""Completion preferences and generated JSONC explanations."""
from dataclasses import dataclass

from ...core.records import Boolean, Nested, Number, POSITIVE_SECONDS, Record, setting
from ...core.theme import MenuStyle
from ...integrations.ish.keys import KeyRule, KeySequence
from ...popups.config import PopupPanel, PopupDimension, PopupHeight


@dataclass(frozen=True, slots=True)
class CompletionWidth(PopupDimension):
    value: int | float = setting(Number(), default=42)


@dataclass(frozen=True, slots=True)
class CompletionHeight(PopupHeight):
    value: int | float = setting(Number(), default=5)


@dataclass(frozen=True, slots=True)
class CompletionPanel(PopupPanel):
    width: CompletionWidth = setting(Nested(CompletionWidth), factory=CompletionWidth)
    height: CompletionHeight = setting(Nested(CompletionHeight), factory=CompletionHeight)


@dataclass(frozen=True, slots=True)
class CompletionMenu(MenuStyle):
    enabled: bool = setting(Boolean(), default=True,
        description="Apply dok's shared theme to the ish completion list, including existing suggestions. false keeps the host style. Menu layout and keyboard behavior stay with ish.")


@dataclass(frozen=True, slots=True)
class ManagementWidth(PopupDimension):
    value: int | float = setting(Number(), default=104)


@dataclass(frozen=True, slots=True)
class ManagementHeight(PopupHeight):
    value: int | float = setting(Number(), default=28)


@dataclass(frozen=True, slots=True)
class ManagementPanel(PopupPanel):
    width: ManagementWidth = setting(Nested(ManagementWidth), factory=ManagementWidth)
    height: ManagementHeight = setting(Nested(ManagementHeight), factory=ManagementHeight)


@dataclass(frozen=True, slots=True)
class ManagementConfig(Record):
    key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "m"),
        description="Open or close saved completion management. Default Alt+M; null removes this binding. Escape goes back through dropdown, editor and prefix list. Closing a changed draft asks before discarding. No lookup or model call is made.")
    panel: ManagementPanel = setting(Nested(ManagementPanel), factory=ManagementPanel,
        description="Centered completion manager. Changes are staged until Save or F2; closing asks before discarding unsaved changes.")


@dataclass(frozen=True, slots=True)
class CompletionConfig(Record):
    enabled: bool = setting(Boolean(), default=True,
        description="Enable saved option/subcommand suggestions, registration and management popups. Never executes lookup commands automatically.")
    key: KeySequence | None = setting(KeyRule(optional=True), default=("escape", "o"),
        description="Open or close the prefix form beneath the prompt cursor. Tab toggles merge/replace while typing continues to edit the prefix. Enter runs the captured prompt command, analyzes its output and registers it. Escape stays at this root form; the opening key cancels. Default Alt+O; null removes the binding.")
    hub: bool = setting(Boolean(), default=True,
        description="After Enter confirms registration, use the installed Hub command-options Agent to analyze output and translate descriptions into dok's selected language. New Agent model defaults come from hub; existing Agents are edited in Hub. Local fallback preserves source descriptions. No shell tools are assigned by dok.")
    timeout_s: float = setting(POSITIVE_SECONDS, default=60,
        description="Hub analysis deadline in seconds. This does not limit or terminate the shell command; normal ish controls apply to it.")
    menu: CompletionMenu = setting(Nested(CompletionMenu), factory=CompletionMenu,
        description="Completion list appearance, separate from the registration popup. Existing suggestions keep their order and precede generated suggestions; generated names are sorted case-insensitively with punctuation, including leading option dashes. Existing duplicates win.")
    panel: CompletionPanel = setting(Nested(CompletionPanel), factory=CompletionPanel,
        description="Cursor-anchored popup size and style. Fits below or above the prompt cursor inside the existing input area.")
    manage: ManagementConfig = setting(Nested(ManagementConfig), factory=ManagementConfig,
        description="Manage prefixes and entries, including manual edits, deletion and collection locks.")

    def keys(self):
        return tuple(key for key in (self.key, self.manage.key) if key is not None)
