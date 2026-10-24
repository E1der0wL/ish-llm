"""Canonical prompt_toolkit selectors for the Hub theme."""

from enum import StrEnum
from types import MappingProxyType

from .notifications import NotificationKind


class ThemeSelector(StrEnum):
    ROOT = "hub"
    HEADER = "hub.header"
    HEADER_PROJECT = "hub.header.project"
    PROFILE = "hub.profile"
    HEADER_MODEL = "hub.header.model"
    ACCENT = "hub.accent"
    SYSTEM = "hub.system"
    MUTED = "hub.muted"
    SIDEBAR = "hub.sidebar"
    SELECTED = "hub.selected"
    FOCUSED = "hub.focused"
    SETTINGS_INPUT = "hub.settings-input"
    DIVIDER = "hub.divider"
    TITLE = "hub.title"
    COMPOSER = "hub.composer"
    QUESTION = "hub.question"
    QUESTION_HEADING = "hub.question-heading"
    QUESTION_CHOICE = "hub.question-choice"
    QUESTION_CHOICE_SELECTED = "hub.question-choice-selected"
    PTK_AUTO_SUGGESTION = "hub auto-suggestion"
    FOOTER = "hub.footer"
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
    AUXILIARY = "hub.auxiliary"
    AUXILIARY_BUTTON = "hub.auxiliary button"
    AUXILIARY_BUTTON_FOCUSED = "hub.auxiliary button.focused"
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
    CLASS_REFERENCE = "class:{selector}"

    BEFORE_ALERTS = MappingProxyType(
        {
            ThemeSelector.ROOT.value: "noinherit fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.HEADER.value: "fg:{theme.bar_foreground} bg:{theme.header_background} bold",
            ThemeSelector.HEADER_PROJECT.value: "fg:{theme.accent}",
            ThemeSelector.PROFILE.value: "fg:{theme.bar_foreground} bg:{theme.header_background} bold",
            ThemeSelector.HEADER_MODEL.value: "fg:{theme.muted}",
            ThemeSelector.ACCENT.value: "fg:{theme.accent} bold",
            ThemeSelector.SYSTEM.value: "fg:{theme.accent3} bold",
            ThemeSelector.MUTED.value: "fg:{theme.muted}",
            ThemeSelector.SIDEBAR.value: "",
            ThemeSelector.SELECTED.value: "fg:{theme.accent} bold underline",
            ThemeSelector.FOCUSED.value: "fg:{theme.accent} bold underline",
            ThemeSelector.SETTINGS_INPUT.value: "fg:{surface_text} bg:{theme.user_background}",
            ThemeSelector.DIVIDER.value: "fg:{theme.muted}",
            ThemeSelector.TITLE.value: "bold",
            ThemeSelector.COMPOSER.value: "",
            ThemeSelector.QUESTION.value: "",
            ThemeSelector.QUESTION_HEADING.value: "fg:{theme.accent} bg:{theme.header_background} bold",
            ThemeSelector.QUESTION_CHOICE.value: "fg:{theme.foreground}",
            ThemeSelector.QUESTION_CHOICE_SELECTED.value: "fg:{theme.accent} bold",
            ThemeSelector.PTK_AUTO_SUGGESTION.value: "fg:{theme.muted} nobold",
            ThemeSelector.FOOTER.value: "fg:{theme.bar_foreground} bg:{theme.footer_background}",
            ThemeSelector.PTK_FRAME_BORDER.value: "fg:{theme.muted}",
            ThemeSelector.NOTICE.value: "fg:{theme.notice}",
            ThemeSelector.USER.value: "fg:{theme.user}",
            ThemeSelector.USER_BLOCK.value: "fg:{theme.user} bg:{theme.user_background}",
            ThemeSelector.PTK_SCROLLBAR_BACKGROUND.value: "bg:{theme.background}",
            ThemeSelector.PTK_SCROLLBAR_BUTTON.value: "bg:{theme.scrollbar_button}",
            ThemeSelector.PTK_SCROLLBAR_ARROW.value: "fg:{theme.scrollbar_arrow} bg:{theme.background} bold",
            ThemeSelector.PTK_SCROLLBAR_START.value: "nounderline",
            ThemeSelector.PTK_SCROLLBAR_END.value: "nounderline",
            ThemeSelector.SCROLLBAR_TRACK.value: "bg:{theme.background}",
            ThemeSelector.SCROLLBAR_THUMB.value: "fg:{theme.scrollbar_arrow} bg:{theme.scrollbar_button} bold",
            ThemeSelector.SCROLLBAR_ARROW.value: "fg:{theme.scrollbar_arrow} bg:{theme.background}",
            ThemeSelector.SCROLLBAR_HOVER.value: "bg:{theme.accent2} bold",
            ThemeSelector.MOUSE_HOVER_BUTTON_TEXT.value: "fg:{theme.accent2} bold nounderline",
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
            ThemeSelector.PTK_DIALOG_BODY_TEXT_AREA.value: "fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.PTK_DIALOG_FRAME_LABEL.value: "fg:{theme.accent} bg:{theme.background}",
            ThemeSelector.PTK_DIALOG_SHADOW.value: "fg:{theme.muted} bg:{theme.background}",
            ThemeSelector.PTK_BUTTON.value: "fg:{theme.button_foreground} bg:{theme.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED.value: "fg:{theme.button_foreground} bg:{theme.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED_BUTTON_TEXT.value: "fg:{theme.accent} bold",
            ThemeSelector.PTK_BUTTON_SELECTED_BUTTON_TEXT.value: "fg:{theme.accent} bold",
            ThemeSelector.AUXILIARY.value: "bg:default",
            ThemeSelector.AUXILIARY_BUTTON.value: "fg:{theme.button_foreground} bg:{theme.background}",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED.value: "fg:{theme.button_foreground} bg:{theme.background}",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED_BUTTON_TEXT.value: "fg:{theme.accent} bold",
            ThemeSelector.PTK_CHECKBOX_LIST.value: "fg:{theme.foreground} bg:{theme.background} nounderline noreverse",
            ThemeSelector.CHECKLIST.value: "fg:{theme.foreground} bg:{theme.background}",
            ThemeSelector.PTK_CHECKBOX_LIST_FOCUSED.value: "bold nounderline noreverse",
            ThemeSelector.PTK_CHECKBOX_CHECKED.value: "fg:{theme.accent} bold",
            ThemeSelector.PTK_COMPLETION_MENU.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION.value: "fg:{theme.completion_foreground} bg:{theme.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION_CURRENT.value: "fg:{theme.completion_foreground} bg:{theme.completion_current_background} bold",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION.value: "fg:{theme.muted} bg:{theme.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION_CURRENT.value: "fg:{theme.completion_foreground} bg:{theme.completion_current_background}",
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
    def alert_selectors(cls, kind):
        selector = cls.ALERT_CLASS.format(kind=kind)
        return tuple(
            template.format(selector=selector) for template in cls.ALERT_TARGETS
        )

    @staticmethod
    def _render_rule(selector, template, theme):
        if selector == ThemeSelector.SETTINGS_INPUT.value:
            return template.format(
                theme=theme, surface_text=theme._surface_text(theme.user_background)
            )
        return template.format(theme=theme)

    @classmethod
    def rules(cls, theme):
        result = {
            selector: cls._render_rule(selector, template, theme)
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
                selector: cls._render_rule(selector, template, theme)
                for selector, template in cls.AFTER_ALERTS.items()
            }
        )
        return result
