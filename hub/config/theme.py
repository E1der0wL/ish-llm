"""UI-only colors. 'default' leaves the terminal's own palette in control."""

from dataclasses import dataclass, fields
import re
import math

from prompt_toolkit.styles import Style
from .theme_style import ThemeSelector


@dataclass(frozen=True, slots=True)
class HubTheme:
    sidebar_width: int = 26
    icon_style: str = "nerd"
    mode: str = "dark"
    output_refresh_interval: float = 0.1
    progress_refresh_interval: float = 0.1
    foreground: str = "default"
    background: str = "default"
    accent1: str = "#46b59e"
    accent2: str = "#67a9dd"
    accent3: str = "#bc9850"
    comment: str = "#8595a5"

    def _surface(self, color: str, amount: float = 0.16) -> str:
        if color == "default":
            return self.background
        base = self.background if self.background != "default" else self.base_background
        if self.background != "default":
            # Keep the chosen page background, but give derived surfaces the
            # selected theme's light/dark shading even with a custom palette.
            base = self._blend(base, self.base_background, 0.12)
        return self._blend(base, color, amount)

    @staticmethod
    def _blend(base: str, color: str, amount: float) -> str:
        return "#" + "".join(f"{round(int(base[i:i+2], 16) * (1 - amount) + int(color[i:i+2], 16) * amount):02x}"
                              for i in (1, 3, 5))

    def _surface_text(self, background: str) -> str:
        if self.foreground != "default" or background == "default":
            return self.foreground
        brightness = sum(int(background[i:i+2], 16) * weight for i, weight in ((1, 0.2126), (3, 0.7152), (5, 0.0722)))
        return "#17212b" if brightness > 150 else "#d9e2ef"

    # Semantic rendering roles are derived, never stored or edited separately.
    base_background = property(lambda self: "#f5f7fa" if self.mode == "light" else "#111923")
    accent = property(lambda self: self.accent1)
    user = property(lambda self: self.accent2)
    notice = property(lambda self: self.accent3)
    muted = property(lambda self: self.comment)
    user_background = property(lambda self: self._surface(self.accent2))
    header_background = property(lambda self: self._surface(self.accent1))
    footer_background = property(lambda self: self._surface(self.accent1, 0.10))
    code_background = property(lambda self: self._surface(self.comment, 0.10))
    completion_background = property(lambda self: self._surface(self.comment))
    completion_current_background = property(lambda self: self._surface(self.accent1, 0.35))
    button_background = property(lambda self: self._surface(self.accent2, 0.22))
    checklist_background = property(lambda self: self._surface(self.comment))
    scrollbar_button = property(lambda self: self.accent1)
    scrollbar_arrow = property(lambda self: self.comment)
    bar_foreground = property(lambda self: self._surface_text(self.header_background))
    code_foreground = property(lambda self: self._surface_text(self.code_background))
    completion_foreground = property(lambda self: self._surface_text(self.completion_background))
    button_foreground = property(lambda self: self._surface_text(self.button_background))
    checklist_foreground = property(lambda self: self._surface_text(self.checklist_background))
    alert_error_background = property(lambda self: self._surface(self.accent3, 0.30))
    alert_warning_background = property(lambda self: self._surface(self.accent3))
    alert_info_background = property(lambda self: self._surface(self.accent2))
    alert_success_background = property(lambda self: self._surface(self.accent1))
    alert_error_foreground = property(lambda self: self.accent3)
    alert_warning_foreground = property(lambda self: self.accent3)
    alert_info_foreground = property(lambda self: self.accent2)
    alert_success_foreground = property(lambda self: self.accent1)

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in ("output_refresh_interval", "progress_refresh_interval"):
                if type(value) not in (int, float) or not math.isfinite(value) or not 0.01 <= value <= 10:
                    raise ValueError(f"{field.name} must be a number between 0.01 and 10 seconds")
                continue
            if field.name == "icon_style":
                from ..asset.icon import for_style
                for_style(value)
                continue
            if field.name == "mode":
                if value not in ("dark", "light"):
                    raise ValueError("mode must be 'dark' or 'light'")
                continue
            if field.name == "sidebar_width":
                if type(value) is not int or not 18 <= value <= 60:
                    raise ValueError("sidebar_width must be an integer between 18 and 60")
                continue
            if not isinstance(value, str) or not re.fullmatch(r"default|#[0-9a-fA-F]{6}", value):
                raise ValueError(f"{field.name} must be 'default' or a #RRGGBB color")

    def style(self) -> Style:
        return Style.from_dict({
            # Reset inherited ish backgrounds as well as our own previous theme.
            ThemeSelector.ROOT.value: f"noinherit fg:{self.foreground} bg:{self.background}",
            ThemeSelector.HEADER.value: f"fg:{self.bar_foreground} bg:{self.header_background} bold",
            ThemeSelector.HEADER_PROJECT.value: f"fg:{self.accent}",
            ThemeSelector.PROFILE.value: f"fg:{self.bar_foreground} bg:{self.header_background} bold",
            ThemeSelector.HEADER_MODEL.value: f"fg:{self.muted}",
            ThemeSelector.ACCENT.value: f"fg:{self.accent} bold",
            ThemeSelector.SYSTEM.value: f"fg:{self.accent3} bold",
            ThemeSelector.MUTED.value: f"fg:{self.muted}", ThemeSelector.SIDEBAR.value: "",
            ThemeSelector.SELECTED.value: f"fg:{self.accent} bold underline",
            ThemeSelector.FOCUSED.value: f"fg:{self.accent} bold underline",
            ThemeSelector.SETTINGS_INPUT.value: f"fg:{self._surface_text(self.user_background)} bg:{self.user_background}",
            ThemeSelector.DIVIDER.value: f"fg:{self.muted}", ThemeSelector.TITLE.value: "bold",
            ThemeSelector.COMPOSER.value: "",
            ThemeSelector.QUESTION.value: "",
            ThemeSelector.QUESTION_HEADING.value: f"fg:{self.accent} bg:{self.header_background} bold",
            ThemeSelector.QUESTION_CHOICE.value: f"fg:{self.foreground}",
            ThemeSelector.QUESTION_CHOICE_SELECTED.value: f"fg:{self.accent} bold",
            ThemeSelector.PTK_AUTO_SUGGESTION.value: f"fg:{self.muted} nobold",
            ThemeSelector.FOOTER.value: f"fg:{self.bar_foreground} bg:{self.footer_background}",
            ThemeSelector.PTK_FRAME_BORDER.value: f"fg:{self.muted}",
            ThemeSelector.NOTICE.value: f"fg:{self.notice}",
            ThemeSelector.USER.value: f"fg:{self.user}",
            ThemeSelector.USER_BLOCK.value: f"fg:{self.user} bg:{self.user_background}",
            ThemeSelector.PTK_SCROLLBAR_BACKGROUND.value: f"bg:{self.background}",
            ThemeSelector.PTK_SCROLLBAR_BUTTON.value: f"bg:{self.scrollbar_button}",
            ThemeSelector.PTK_SCROLLBAR_ARROW.value: f"fg:{self.scrollbar_arrow} bg:{self.background} bold",
            ThemeSelector.PTK_SCROLLBAR_START.value: "nounderline",
            ThemeSelector.PTK_SCROLLBAR_END.value: "nounderline",
            ThemeSelector.SCROLLBAR_TRACK.value: f"bg:{self.background}",
            ThemeSelector.SCROLLBAR_THUMB.value: f"fg:{self.scrollbar_arrow} bg:{self.scrollbar_button} bold",
            ThemeSelector.SCROLLBAR_ARROW.value: f"fg:{self.scrollbar_arrow} bg:{self.background}",
            ThemeSelector.SCROLLBAR_HOVER.value: f"bg:{self.accent2} bold",
            ThemeSelector.MOUSE_HOVER_BUTTON_TEXT.value: f"fg:{self.accent2} bold nounderline",
            ThemeSelector.MOUSE_HOVER.value: "bold",
            ThemeSelector.TEXT_SELECTION.value: f"fg:{self.code_background} bg:{self.accent2} nobold",
            ThemeSelector.INACTIVE.value: "",
            ThemeSelector.TRANSPARENT.value: "bg:default",
            **{selector: f"fg:{getattr(self, 'alert_' + level + '_foreground')} bg:{getattr(self, 'alert_' + level + '_background')}"
               for level in ("error", "info", "warning", "success")
               for selector in (f"hub.alert-{level}", f"hub.alert-{level} frame.label", f"hub.alert-{level} frame.border")},
            ThemeSelector.PTK_DIALOG.value: f"fg:{self.foreground} bg:{self.background}",
            ThemeSelector.PTK_DIALOG_BODY.value: f"fg:{self.foreground} bg:{self.background}",
            ThemeSelector.PTK_DIALOG_BODY_TEXT_AREA.value: f"fg:{self.foreground} bg:{self.background}",
            ThemeSelector.PTK_DIALOG_FRAME_LABEL.value: f"fg:{self.accent} bg:{self.background}",
            ThemeSelector.PTK_DIALOG_SHADOW.value: f"fg:{self.muted} bg:{self.background}",
            ThemeSelector.PTK_BUTTON.value: f"fg:{self.button_foreground} bg:{self.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED.value: f"fg:{self.button_foreground} bg:{self.button_background} nobold nounderline noreverse",
            ThemeSelector.PTK_BUTTON_FOCUSED_BUTTON_TEXT.value: f"fg:{self.accent} bold",
            ThemeSelector.PTK_BUTTON_SELECTED_BUTTON_TEXT.value: f"fg:{self.accent} bold",
            ThemeSelector.AUXILIARY.value: "bg:default",
            ThemeSelector.AUXILIARY_BUTTON.value: f"fg:{self.button_foreground} bg:{self.background}",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED.value: f"fg:{self.button_foreground} bg:{self.background}",
            ThemeSelector.AUXILIARY_BUTTON_FOCUSED_BUTTON_TEXT.value: f"fg:{self.accent} bold",
            ThemeSelector.PTK_CHECKBOX_LIST.value: f"fg:{self.foreground} bg:{self.background} nounderline noreverse",
            ThemeSelector.CHECKLIST.value: f"fg:{self.foreground} bg:{self.background}",
            ThemeSelector.PTK_CHECKBOX_LIST_FOCUSED.value: "bold nounderline noreverse",
            ThemeSelector.PTK_CHECKBOX_CHECKED.value: f"fg:{self.accent} bold",
            ThemeSelector.PTK_COMPLETION_MENU.value: f"fg:{self.completion_foreground} bg:{self.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION.value: f"fg:{self.completion_foreground} bg:{self.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_COMPLETION_CURRENT.value: f"fg:{self.completion_foreground} bg:{self.completion_current_background} bold",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION.value: f"fg:{self.muted} bg:{self.completion_background}",
            ThemeSelector.PTK_COMPLETION_MENU_META_COMPLETION_CURRENT.value: f"fg:{self.completion_foreground} bg:{self.completion_current_background}",
            ThemeSelector.PTK_COMPLETION_MENU_SCROLLBAR_BACKGROUND.value: f"bg:{self.background}",
            ThemeSelector.PTK_COMPLETION_MENU_SCROLLBAR_BUTTON.value: f"bg:{self.scrollbar_button}",
            ThemeSelector.SEARCH_MATCH.value: f"fg:{self.completion_foreground} bg:{self.completion_current_background}",
            ThemeSelector.SEARCH_CURRENT.value: f"fg:{self.code_background} bg:{self.accent} bold",
            ThemeSelector.TOAST.value: f"fg:{self.completion_foreground} bg:{self.completion_background}",
            ThemeSelector.TOAST_FRAME_LABEL.value: f"fg:{self.accent} bg:{self.completion_background}",
        })

    @classmethod
    def dark(cls) -> "HubTheme":
        return cls(foreground="#d9e2ef", background="#111923", accent1="#7cddc7",
                   comment="#91a4b8", accent2="#8ecbff", accent3="#dfc890")
