"""Canonical prompt_toolkit selectors for the Hub theme."""

from enum import StrEnum
from types import MappingProxyType

from .notifications import NotificationKind


class ThemeSelector(StrEnum):
    DIALOG = "dialog"
    ROOT = "hub"
    HEADER = "hub.header"
    HEADER_BUTTON = "hub.header button"
    HEADER_BUTTON_FOCUSED = "hub.header button.focused"
    HEADER_BUTTON_TEXT = "hub.header button.text"
    HEADER_BUTTON_FOCUSED_TEXT = "hub.header button.focused button.text"
    HEADER_PROJECT = "hub.header.project"
    PROFILE = "hub.profile"
    PROFILE_NAME = "hub.profile.name"
    PROFILE_ACCOUNT = "hub.profile.account"
    HEADER_MODEL = "hub.header.model"
    ACCENT = "hub.accent"
    SYSTEM = "hub.system"
    MUTED = "hub.muted"
    SIDEBAR = "hub.sidebar"
    SIDEBAR_HEADING = "hub.sidebar-heading"
    SELECTED = "hub.selected"
    FOCUSED = "hub.focused"
    SETTINGS_INPUT = "hub.settings-input"
    SETTINGS_INPUT_FOCUSED = "hub.settings-input.focused"
    DIVIDER = "hub.divider"
    TITLE = "hub.title"
    COMPOSER = "hub.composer"
    INPUT_FRAME = "hub.input-frame"
    QUESTION = "hub.question"
    QUESTION_HEADING = "hub.question-heading"
    QUESTION_CHOICE = "hub.question-choice"
    QUESTION_CHOICE_SELECTED = "hub.question-choice-selected"
    PTK_AUTO_SUGGESTION = "hub auto-suggestion"
    FOOTER = "hub.footer"
    SHORTCUT_SEGMENT = "hub.shortcut-segment"
    SHORTCUT_KEY = "hub.shortcut-key"
    SHORTCUT_EDGE = "hub.shortcut-edge"
    SHORTCUT_LEADING = "hub.shortcut-leading"
    PTK_FRAME_BORDER = "hub frame.border"
    NOTICE = "hub.notice"
    USER = "hub.user"
    USER_BLOCK = "hub.user-block"
    PTK_SCROLLBAR_BACKGROUND = "hub scrollbar.background"
    PTK_SCROLLBAR_BUTTON = "hub scrollbar.button"
    PTK_SCROLLBAR_ARROW = "hub scrollbar.arrow"
    PTK_SCROLLBAR_START = "hub scrollbar.start"
    PTK_SCROLLBAR_END = "hub scrollbar.end"
    SCROLLBAR_TRACK = "hub.scrollbar-track"
    SCROLLBAR_THUMB = "hub.scrollbar-thumb"
    SCROLLBAR_ARROW = "hub.scrollbar-arrow"
    SCROLLBAR_HOVER = "hub.scrollbar-hover"
    MOUSE_HOVER_BUTTON_TEXT = "hub.mouse-hover button.text"
    MOUSE_HOVER = "hub.mouse-hover"
    TEXT_SELECTION = "hub.text-selection"
    INACTIVE = "hub.inactive"
    TRANSPARENT = "hub.transparent"
    PTK_DIALOG = "hub dialog"
    PTK_DIALOG_BODY = "hub dialog.body"
    PTK_DIALOG_BODY_TEXT_AREA = "hub dialog.body text-area"
    PTK_DIALOG_FRAME_LABEL = "hub dialog frame.label"
    PTK_DIALOG_SHADOW = "hub dialog shadow"
    PTK_BUTTON = "hub button"
    PTK_BUTTON_FOCUSED = "hub button.focused"
    PTK_BUTTON_FOCUSED_BUTTON_TEXT = "hub button.focused button.text"
    PTK_BUTTON_SELECTED_BUTTON_TEXT = "hub button.selected button.text"
    DROPDOWN = "hub.dropdown"
    DROPDOWN_BUTTON = "hub.dropdown button"
    DROPDOWN_BUTTON_FOCUSED = "hub.dropdown button.focused"
    DROPDOWN_BUTTON_TEXT = "hub.dropdown button.text"
    DROPDOWN_BUTTON_FOCUSED_TEXT = "hub.dropdown button.focused button.text"
    AUXILIARY = "hub.auxiliary"
    AUXILIARY_BUTTON = "hub.auxiliary button"
    AUXILIARY_BUTTON_FOCUSED = "hub.auxiliary button.focused"
    AUXILIARY_BUTTON_TEXT = "hub.auxiliary button.text"
    AUXILIARY_BUTTON_FOCUSED_BUTTON_TEXT = "hub.auxiliary button.focused button.text"
    PTK_CHECKBOX_LIST = "hub checkbox-list"
    CHECKLIST = "hub.checklist"
    PTK_CHECKBOX_LIST_FOCUSED = "hub checkbox-list.focused"
    PTK_CHECKBOX_CHECKED = "hub checkbox-checked"
    PTK_COMPLETION_MENU = "hub completion-menu"
    PTK_COMPLETION_MENU_COMPLETION = "hub completion-menu.completion"
    PTK_COMPLETION_MENU_COMPLETION_CURRENT = "hub completion-menu.completion.current"
    PTK_COMPLETION_MENU_META_COMPLETION = "hub completion-menu.meta.completion"
    PTK_COMPLETION_MENU_META_COMPLETION_CURRENT = (
        "hub completion-menu.meta.completion.current"
    )
    PTK_COMPLETION_MENU_SCROLLBAR_BACKGROUND = (
        "hub completion-menu.scrollbar.background"
    )
    PTK_COMPLETION_MENU_SCROLLBAR_BUTTON = "hub completion-menu.scrollbar.button"
    SEARCH_MATCH = "hub.search.match"
    SEARCH_CURRENT = "hub.search.current"
    TOAST = "hub.toast"
    TOAST_FRAME_LABEL = "hub.toast frame.label"

    @property
    def style(self):
        return ThemeStyle.CLASS_REFERENCE.format(selector=self.value)


class ThemeStyle:
    BOLD = "bold"
    ITALIC = "italic"
    UNDERLINE = "underline"
    STRIKE = "strike"
    REVERSE = "reverse"
    BLINK = "blink"
    HIDDEN = "hidden"
    ATTRIBUTES = (BOLD, ITALIC, UNDERLINE, STRIKE, REVERSE, BLINK, HIDDEN)
    NEGATED_ATTRIBUTE = "no"
    FOREGROUND_PREFIX = "fg:"
    BACKGROUND_PREFIX = "bg:"
    MODIFIER_SEPARATOR = " "
    CLASS_REFERENCE = "class:{selector}"
    USER_BLOCK_COLORS = "fg:{theme.accent4} bg:{theme.user_background}"
    SHORTCUT_ARROW_COLORS = "fg:{theme.shortcut_background} bg:{theme.footer_background}"

    BEFORE_ALERTS = MappingProxyType(
        {
            ThemeSelector.ROOT.value: "noinherit fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.HEADER.value: "fg:{theme.bar_foreground} bg:{theme.header_background} bold",
            ThemeSelector.HEADER_PROJECT.value: "fg:{theme.accent}",
            ThemeSelector.PROFILE.value: "fg:{theme.comment} bg:{theme.widget_background} bold",
            ThemeSelector.PROFILE_NAME.value: "fg:{theme.accent2}",
            ThemeSelector.PROFILE_ACCOUNT.value: "fg:{theme.comment}",
            ThemeSelector.HEADER_MODEL.value: "fg:{theme.muted}",
            ThemeSelector.ACCENT.value: "fg:{theme.accent} bold",
            ThemeSelector.SYSTEM.value: "fg:{theme.accent3} bold",
            ThemeSelector.MUTED.value: "fg:{theme.muted}",
            ThemeSelector.SIDEBAR.value: "",
            ThemeSelector.SELECTED.value: "fg:{theme.accent} bold underline",
            ThemeSelector.FOCUSED.value: "fg:{theme.accent} bold underline",
            ThemeSelector.SETTINGS_INPUT.value: "fg:{theme.foreground} bg:{theme.widget_background}",
            ThemeSelector.SETTINGS_INPUT_FOCUSED.value: "fg:{theme.accent} bg:{theme.widget_background} bold underline",
            ThemeSelector.DIVIDER.value: "fg:{theme.muted}",
            ThemeSelector.TITLE.value: "bold",
            ThemeSelector.COMPOSER.value: "fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.QUESTION.value: "",
            ThemeSelector.QUESTION_HEADING.value: "fg:{theme.accent} bg:{theme.header_background} bold",
            ThemeSelector.QUESTION_CHOICE.value: "fg:{theme.foreground}",
            ThemeSelector.QUESTION_CHOICE_SELECTED.value: "fg:{theme.accent} bold",
            ThemeSelector.PTK_AUTO_SUGGESTION.value: "fg:{theme.muted} nobold",
            ThemeSelector.FOOTER.value: "fg:{theme.accent4} bg:{theme.footer_background}",
            ThemeSelector.SHORTCUT_SEGMENT.value: "fg:{theme.accent5} bg:{theme.shortcut_background} bold",
            ThemeSelector.SHORTCUT_KEY.value: "fg:{theme.accent3} bg:{theme.shortcut_background} bold",
            ThemeSelector.SHORTCUT_EDGE.value: SHORTCUT_ARROW_COLORS,
            ThemeSelector.SHORTCUT_LEADING.value: "fg:{theme.footer_background} bg:{theme.shortcut_background}",
            ThemeSelector.PTK_FRAME_BORDER.value: "fg:{theme.muted}",
            ThemeSelector.NOTICE.value: "fg:{theme.notice}",
            ThemeSelector.USER.value: "fg:{theme.user}",
            ThemeSelector.USER_BLOCK.value: USER_BLOCK_COLORS,
            ThemeSelector.PTK_SCROLLBAR_BACKGROUND.value: "bg:{theme.background}",
            ThemeSelector.PTK_SCROLLBAR_BUTTON.value: "bg:{theme.scrollbar_button}",
            ThemeSelector.PTK_SCROLLBAR_ARROW.value: "fg:{theme.scrollbar_arrow} bg:{theme.background} bold",
            ThemeSelector.PTK_SCROLLBAR_START.value: "nounderline",
            ThemeSelector.PTK_SCROLLBAR_END.value: "nounderline",
            ThemeSelector.SCROLLBAR_TRACK.value: "bg:{theme.background}",
            ThemeSelector.SCROLLBAR_THUMB.value: "fg:{theme.scrollbar_arrow} bg:{theme.scrollbar_button} bold",
            ThemeSelector.SCROLLBAR_ARROW.value: "fg:{theme.scrollbar_arrow} bg:{theme.background}",
            ThemeSelector.SCROLLBAR_HOVER.value: "bg:{theme.accent2} bold",
            ThemeSelector.MOUSE_HOVER_BUTTON_TEXT.value: "fg:{theme.button_foreground} bold nounderline",
            ThemeSelector.MOUSE_HOVER.value: "bold",
            ThemeSelector.TEXT_SELECTION.value: "fg:{theme.code_background} bg:{theme.accent2} nobold",
            ThemeSelector.INACTIVE.value: "",
            ThemeSelector.TRANSPARENT.value: "bg:default",
        }
    )
    AFTER_ALERTS = MappingProxyType(
        {
            ThemeSelector.PTK_DIALOG.value: "fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.PTK_DIALOG_BODY.value: "fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.PTK_DIALOG_BODY_TEXT_AREA.value: "fg:{theme.foreground} bg:{theme.widget_background}",
            ThemeSelector.PTK_DIALOG_FRAME_LABEL.value: "fg:{theme.accent} bg:{theme.background}",
            ThemeSelector.PTK_DIALOG_SHADOW.value: "fg:{theme.muted} bg:{theme.background}",
            ThemeSelector.PTK_BUTTON.value: "fg:{theme.button_foreground} bg:{theme.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED.value: "fg:{theme.button_foreground} bg:{theme.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED_BUTTON_TEXT.value: "fg:{theme.button_foreground} bold",
            ThemeSelector.PTK_BUTTON_SELECTED_BUTTON_TEXT.value: "fg:{theme.button_foreground} bold",
            ThemeSelector.HEADER_BUTTON.value: "fg:{theme.icon_button_foreground} bg:{theme.header_background} nobold nounderline noreverse",
            ThemeSelector.HEADER_BUTTON_FOCUSED.value: "fg:{theme.icon_button_foreground} bg:{theme.header_background} bold nounderline noreverse",
            ThemeSelector.HEADER_BUTTON_TEXT.value: "fg:{theme.icon_button_foreground} bg:{theme.header_background}",
            ThemeSelector.HEADER_BUTTON_FOCUSED_TEXT.value: "fg:{theme.icon_button_foreground} bg:{theme.header_background} bold",
            ThemeSelector.DROPDOWN.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.DROPDOWN_BUTTON.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.DROPDOWN_BUTTON_FOCUSED.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.DROPDOWN_BUTTON_TEXT.value: "fg:{theme.completion_foreground}",
            ThemeSelector.DROPDOWN_BUTTON_FOCUSED_TEXT.value: "fg:{theme.completion_foreground} bold",
            ThemeSelector.AUXILIARY.value: "bg:default",
            ThemeSelector.AUXILIARY_BUTTON.value: "fg:{theme.icon_button_foreground} bg:default",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED.value: "fg:{theme.icon_button_foreground} bg:default",
            ThemeSelector.AUXILIARY_BUTTON_TEXT.value: "fg:{theme.icon_button_foreground}",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED_BUTTON_TEXT.value: "fg:{theme.icon_button_foreground} bold",
            ThemeSelector.PTK_CHECKBOX_LIST.value: "fg:{theme.checklist_foreground} bg:{theme.checklist_background} nounderline noreverse",
            ThemeSelector.CHECKLIST.value: "fg:{theme.checklist_foreground} bg:{theme.checklist_background}",
            ThemeSelector.PTK_CHECKBOX_LIST_FOCUSED.value: "bold nounderline noreverse",
            ThemeSelector.PTK_CHECKBOX_CHECKED.value: "fg:{theme.accent2} bold",
            ThemeSelector.PTK_COMPLETION_MENU.value: "fg:{theme.completion_foreground} bg:{theme.completion_background} noreverse",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION_CURRENT.value: "fg:{theme.completion_foreground} bg:{theme.completion_current_background} bold noreverse",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION.value: "fg:{theme.muted} bg:{theme.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION_CURRENT.value: "fg:{theme.muted} bg:{theme.completion_current_background}",
            ThemeSelector.PTK_COMPLETION_MENU_SCROLLBAR_BACKGROUND.value: "bg:{theme.background}",
            ThemeSelector.PTK_COMPLETION_MENU_SCROLLBAR_BUTTON.value: "bg:{theme.scrollbar_button}",
            ThemeSelector.SEARCH_MATCH.value: "fg:{theme.completion_foreground} bg:{theme.completion_current_background}",
            ThemeSelector.SEARCH_CURRENT.value: "fg:{theme.code_background} bg:{theme.accent} bold",
            ThemeSelector.TOAST.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.TOAST_FRAME_LABEL.value: "fg:{theme.accent} bg:{theme.completion_background}",
        }
    )

    ALERT_CLASS = "hub.alert-{kind}"
    ALERT_TARGETS = ("{selector}", "{selector} frame.label", "{selector} frame.border")
    ALERT_FOREGROUND = "alert_{kind}_foreground"
    ALERT_BACKGROUND = "alert_{kind}_background"
    ALERT_COLORS = "fg:{foreground} bg:{background}"

    @classmethod
    def joined(cls, *parts):
        return cls.MODIFIER_SEPARATOR.join(parts)

    @classmethod
    def alert_style(cls, kind):
        return cls.CLASS_REFERENCE.format(selector=cls.ALERT_CLASS.format(kind=kind))

    @classmethod
    def alert_selectors(cls, kind):
        selector = cls.ALERT_CLASS.format(kind=kind)
        return tuple(
            template.format(selector=selector) for template in cls.ALERT_TARGETS
        )

    @classmethod
    def rules(cls, theme):
        result = {
            selector: template.format(theme=theme)
            for selector, template in cls.BEFORE_ALERTS.items()
        }
        for kind in NotificationKind.values():
            for selector in cls.alert_selectors(kind):
                result[selector] = cls.ALERT_COLORS.format(
                    foreground=getattr(theme, cls.ALERT_FOREGROUND.format(kind=kind)),
                    background=getattr(theme, cls.ALERT_BACKGROUND.format(kind=kind)),
                )
        result.update(
            {
                selector: template.format(theme=theme)
                for selector, template in cls.AFTER_ALERTS.items()
            }
        )
        return result
