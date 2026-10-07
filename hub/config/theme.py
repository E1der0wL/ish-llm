"""UI-only colors. 'default' leaves the terminal's own palette in control."""

from dataclasses import dataclass, fields
import re
import math

from prompt_toolkit.styles import Style


@dataclass(frozen=True, slots=True)
class HubTheme:
    sidebar_width: int = 26
    icon_style: str = "nerd"
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
        base = self.background if self.background != "default" else "#111923"
        return "#" + "".join(f"{round(int(base[i:i+2], 16) * (1 - amount) + int(color[i:i+2], 16) * amount):02x}"
                              for i in (1, 3, 5))

    def _surface_text(self, background: str) -> str:
        if self.foreground != "default" or background == "default":
            return self.foreground
        brightness = sum(int(background[i:i+2], 16) * weight for i, weight in ((1, 0.2126), (3, 0.7152), (5, 0.0722)))
        return "#17212b" if brightness > 150 else "#d9e2ef"

    # Semantic rendering roles are derived, never stored or edited separately.
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

    @classmethod
    def from_preferences(cls, values: dict) -> "HubTheme":
        """Read older Hub palettes without retaining individual widget overrides."""
        values = dict(values)
        for old, new in (("accent", "accent1"), ("user", "accent2"), ("notice", "accent3"), ("muted", "comment")):
            if old in values:
                values.setdefault(new, values.pop(old))
        obsolete = {name for name, value in vars(cls).items() if isinstance(value, property)}
        obsolete.update(("button_focused_background", "checklist_focused_background", "scrollbar_background"))
        return cls(**{key: value for key, value in values.items() if key not in obsolete})

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
            if field.name == "sidebar_width":
                if type(value) is not int or not 18 <= value <= 60:
                    raise ValueError("sidebar_width must be an integer between 18 and 60")
                continue
            if not isinstance(value, str) or not re.fullmatch(r"default|#[0-9a-fA-F]{6}", value):
                raise ValueError(f"{field.name} must be 'default' or a #RRGGBB color")

    def style(self) -> Style:
        return Style.from_dict({
            # Reset inherited ish backgrounds as well as our own previous theme.
            "hub": f"noinherit fg:{self.foreground} bg:{self.background}",
            "hub.header": f"fg:{self.bar_foreground} bg:{self.header_background} bold",
            "hub.header.project": f"fg:{self.accent}",
            "hub.header.model": f"fg:{self.muted}",
            "hub.accent": f"fg:{self.accent} bold",
            "hub.muted": f"fg:{self.muted}", "hub.sidebar": "",
            "hub.selected": f"fg:{self.accent} bold underline",
            "hub.focused": f"fg:{self.accent} bold underline",
            "hub.settings-input": f"fg:{self._surface_text(self.user_background)} bg:{self.user_background}",
            "hub.divider": f"fg:{self.muted}", "hub.title": "bold",
            "hub.composer": "",
            "hub.footer": f"fg:{self.bar_foreground} bg:{self.footer_background}",
            "hub frame.border": f"fg:{self.muted}",
            "hub.notice": f"fg:{self.notice}", "hub.detail": "",
            "hub.user": f"fg:{self.user}",
            "hub.user-block": f"fg:{self.user} bg:{self.user_background}",
            "hub scrollbar.background": "bg:default",
            "hub scrollbar.button": f"bg:{self.scrollbar_button}",
            "hub scrollbar.arrow": f"fg:{self.scrollbar_arrow} bg:default bold",
            "hub scrollbar.start": "nounderline",
            "hub scrollbar.end": "nounderline",
            "hub.scrollbar-track": "bg:default",
            "hub.scrollbar-thumb": f"fg:{self.scrollbar_arrow} bg:{self.scrollbar_button} bold",
            "hub.scrollbar-arrow": f"fg:{self.scrollbar_arrow} bg:default",
            **{selector: f"fg:{getattr(self, 'alert_' + level + '_foreground')} bg:{getattr(self, 'alert_' + level + '_background')}"
               for level in ("error", "info", "warning", "success")
               for selector in (f"hub.alert-{level}", f"hub.alert-{level} frame.label", f"hub.alert-{level} frame.border")},
            "hub dialog": f"fg:{self.foreground} bg:{self.background}",
            "hub dialog.body": f"fg:{self.foreground} bg:{self.background}",
            "hub dialog.body text-area": f"fg:{self.foreground} bg:{self.background}",
            "hub dialog frame.label": f"fg:{self.accent} bg:{self.background}",
            "hub dialog shadow": f"fg:{self.muted} bg:{self.background}",
            "hub button": f"fg:{self.button_foreground} bg:{self.button_background} nobold nounderline noreverse",
            "hub button.focused": f"fg:{self.button_foreground} bg:{self.button_background} nobold nounderline noreverse",
            "hub button.focused button.text": f"fg:{self.accent} bold",
            "hub button.selected button.text": f"fg:{self.accent} bold",
            "hub checkbox-list": f"fg:{self.checklist_foreground} bg:{self.checklist_background} nounderline noreverse",
            "hub.checklist": f"fg:{self.checklist_foreground} bg:{self.checklist_background}",
            "hub checkbox-list.focused": "bold nounderline noreverse",
            "hub checkbox-checked": f"fg:{self.accent} bold",
            "hub completion-menu": f"fg:{self.completion_foreground} bg:{self.completion_background}",
            "hub completion-menu.completion": f"fg:{self.completion_foreground} bg:{self.completion_background}",
            "hub completion-menu.completion.current": f"fg:{self.completion_foreground} bg:{self.completion_current_background} bold",
            "hub completion-menu.meta.completion": f"fg:{self.muted} bg:{self.completion_background}",
            "hub completion-menu.meta.completion.current": f"fg:{self.completion_foreground} bg:{self.completion_current_background}",
            "hub completion-menu.scrollbar.background": "bg:default",
            "hub completion-menu.scrollbar.button": f"bg:{self.scrollbar_button}",
            "hub.search.match": f"fg:{self.completion_foreground} bg:{self.completion_current_background}",
            "hub.search.current": f"fg:{self.code_background} bg:{self.accent} bold",
            "hub.toast": f"fg:{self.completion_foreground} bg:{self.completion_background}",
            "hub.toast frame.label": f"fg:{self.accent} bg:{self.completion_background}",
        })

    @classmethod
    def dark(cls) -> "HubTheme":
        return cls(foreground="#d9e2ef", background="#111923", accent1="#7cddc7",
                   comment="#91a4b8", accent2="#8ecbff", accent3="#dfc890")
