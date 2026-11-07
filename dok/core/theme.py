"""Shared theme declarations, reference validation and color resolution.

Named palette entries own shared RGB declarations. Roles reference the palette
(or the terminal); widget selectors reference a role or palette entry. The toolbar
background additionally accepts an explicit RGB override through ThemeBackground.
There are no recursive aliases, process-global active themes or UI dependencies.
"""
from dataclasses import dataclass, fields
from enum import StrEnum

from .colors import HexColor
from .records import Choice, Nested, Nullable, Record, Rule, SettingsError, setting


def palette_color(value: str, description: str):
    return setting(HexColor(), default=value, description=description + " RGB in #RRGGBB.")


@dataclass(frozen=True, slots=True)
class ThemeColors(Record):
    black: str = palette_color("#253047", "Dark blue-gray surfaces.")
    red: str = palette_color("#E58C76", "Soft coral for destructive actions and warm accents.")
    green: str = palette_color("#a3e635", "Lime for save actions and CPU.")
    yellow: str = palette_color("#e5c07b", "Warm yellow for warnings.")
    blue: str = palette_color("#60a5fa", "Blue for Saturdays.")
    purple: str = palette_color("#b4a7d6", "Soft purple for network activity.")
    cyan: str = palette_color("#67e8f9", "Cyan for titles and memory.")
    white: str = palette_color("#a8b2bf", "Cool light gray for menus.")
    comment: str = palette_color("#77818d", "Muted text and low temperatures.")
    bright_black: str = palette_color("#475569", "Visible dark gray for borders and separators.")
    bright_red: str = palette_color("#f6a594", "Bright coral.")
    bright_green: str = palette_color("#bef264", "Bright lime.")
    bright_yellow: str = palette_color("#f3d59e", "Bright warm yellow.")
    bright_blue: str = palette_color("#93c5fd", "Bright blue.")
    bright_purple: str = palette_color("#d1c4ef", "Bright lavender.")
    bright_cyan: str = palette_color("#a5f3fc", "Bright cyan.")
    bright_white: str = palette_color("#e5e7eb", "Bright light gray.")
    bright_comment: str = palette_color("#94a3b8", "Secondary labels and hints.")


# Enum members derive from their owning record, not a second field-name list.
ThemeColor = StrEnum("ThemeColor", {item.name.upper(): item.name for item in fields(ThemeColors)})


def role_color(default, description):
    return setting(Nullable(Choice(ThemeColor)), default=default,
                   description=description + " Palette name; null uses the terminal default. Bright colors do not imply bold.")


@dataclass(frozen=True, slots=True)
class ThemeRoles(Record):
    foreground: str | None = role_color(None, "Normal text, dates, time, media controls and played track.")
    background: str | None = role_color(None, "Shared dock, toolbar and popup background; null preserves terminal transparency.")
    title: str | None = role_color(ThemeColor.CYAN, "Popup titles, focused button text, selected calendar events and weather graph.")
    button_bg: str | None = role_color(ThemeColor.BLACK, "Title bars, buttons and menu surfaces.")
    editor_bg: str | None = role_color(ThemeColor.BLACK, "Entire calendar event pane.")
    input_bg: str | None = role_color(ThemeColor.BRIGHT_BLACK, "Editable text fields, including empty cells and memo rows.")
    save: str | None = role_color(ThemeColor.GREEN, "Save and success actions.")
    delete: str | None = role_color(ThemeColor.RED, "Delete actions and high temperatures.")
    muted: str | None = role_color(ThemeColor.BRIGHT_COMMENT, "Labels, help, remaining track and zero precipitation probability.")
    separator: str | None = role_color(ThemeColor.BRIGHT_BLACK, "Borders and solid separators.")
    warning: str | None = role_color(ThemeColor.YELLOW, "Connection failures and warnings.")
    error: str | None = role_color(ThemeColor.RED, "Errors.")
    disabled: str | None = role_color(ThemeColor.COMMENT, "Inactive placeholders. Unavailable music buttons additionally remain dimmed.")
    menu_text: str | None = role_color(ThemeColor.WHITE, "Dropdown item text.")
    selected_fg: str | None = role_color(ThemeColor.BLACK, "Selected menu item text.")
    selected_bg: str | None = role_color(ThemeColor.WHITE, "Selected menu item background.")
    scrollbar: str | None = role_color(ThemeColor.CYAN, "Scrollbar thumb; its track uses button_bg.")
    cpu: str | None = role_color(ThemeColor.GREEN, "Toolbar CPU text and graph.")
    memory: str | None = role_color(ThemeColor.CYAN, "Toolbar memory text and graph.")
    network: str | None = role_color(ThemeColor.PURPLE, "Toolbar network text and graph.")
    saturday: str | None = role_color(ThemeColor.BLUE, "Calendar Saturdays.")
    sunday: str | None = role_color(ThemeColor.RED, "Calendar Sundays.")
    holiday: str | None = role_color(ThemeColor.RED, "User-registered calendar holidays.")
    low_temp: str | None = role_color(ThemeColor.COMMENT, "Low forecast temperature.")


ThemeRole = StrEnum("ThemeRole", {item.name.upper(): item.name for item in fields(ThemeRoles)})


class ThemeReference(Rule):
    """A UI selector; RGB is edited centrally in ThemeColors."""

    def decode(self, value):
        if not isinstance(value, str) or value not in self.choices():
            raise SettingsError("Expected a theme role or palette name; edit RGB values under theme.colors")
        return str(value)

    @staticmethod
    def choices():
        return (*ThemeRole, *ThemeColor)

    def schema(self):
        return {"type": "string", "enum": list(self.choices())}


class ThemeBackground(ThemeReference):
    """A surface background may explicitly override its shared theme selector."""

    def decode(self, value):
        if isinstance(value, str) and value.startswith("#"):
            return HexColor().decode(value)
        return super().decode(value)

    def schema(self):
        return {"anyOf": [super().schema(), HexColor().schema()]}


def theme_reference(default=ThemeRole.FOREGROUND, description="Foreground"):
    return setting(ThemeReference(), default=default,
        description=description + ". Theme role or palette name, e.g. foreground, muted, cyan, bright_cyan. Edit RGB under theme.colors; terminal defaults are selected with null in theme.roles.")


@dataclass(frozen=True, slots=True)
class Theme(Record):
    colors: ThemeColors = setting(Nested(ThemeColors), factory=ThemeColors,
        description="Shared 18-color palette for dock, toolbar and popups. Shortcut RGB colors and visualizer gradients stay independent.")
    roles: ThemeRoles = setting(Nested(ThemeRoles), factory=ThemeRoles,
        description="Map UI roles to palette names. null means terminal default, not another role. No hex values or role-to-role aliases here.")

    def resolve(self, reference: str) -> str | None:
        reference = ThemeReference().decode(reference)
        name = getattr(self.roles, reference) if reference in ThemeRole else reference
        return getattr(self.colors, name) if name is not None else None

    def resolve_background(self, reference: str) -> str | None:
        reference = ThemeBackground().decode(reference)
        return reference if reference.startswith("#") else self.resolve(reference)

    def paint(self, foreground=ThemeRole.FOREGROUND, *, background=ThemeRole.BACKGROUND,
              bold=False, dim=False) -> str:
        fg, bg = self.resolve(foreground), self.resolve_background(background)
        return (f"noreverse fg:{fg or 'default'} bg:{bg or 'default'}"
                + (" bold" if bold else " nobold") + (" dim" if dim else " nodim"))

    def literal(self, foreground: str | None, *, background=ThemeRole.BACKGROUND, bold=False) -> str:
        """Direct foreground for independent shortcut/visualizer and public spans."""
        fg = HexColor().decode(foreground) if foreground else None
        bg = self.resolve_background(background)
        return (f"noreverse fg:{fg or 'default'} bg:{bg or 'default'}"
                + (" bold" if bold else " nobold") + " nodim")

    def span(self, foreground: str | None, *, background=ThemeRole.BACKGROUND, bold=False) -> str:
        """Render public TextSpan colors as well as configured theme selectors."""
        if foreground and foreground.startswith("#"):
            return self.literal(foreground, background=background, bold=bold)
        return self.paint(foreground or ThemeRole.FOREGROUND, background=background, bold=bold)

    def menu_styles(self) -> dict[str, str]:
        normal = self.paint(ThemeRole.MENU_TEXT, background=ThemeRole.BUTTON_BG)
        selected = self.paint(ThemeRole.SELECTED_FG, background=ThemeRole.SELECTED_BG)
        track = self.paint(ThemeRole.BUTTON_BG, background=ThemeRole.BUTTON_BG)
        thumb = self.paint(ThemeRole.SCROLLBAR, background=ThemeRole.SCROLLBAR)
        return {"completion-menu": normal, "completion-menu.completion": normal,
                "completion-menu.completion.current": selected,
                "scrollbar.background": track, "scrollbar.background scrollbar.start": track + " nounderline",
                "scrollbar.button": thumb, "scrollbar.button scrollbar.end": thumb + " nounderline"}

    def button(self, foreground=ThemeRole.FOREGROUND, *, selected=False, bold=False):
        """Focus changes button text, never the surface background."""
        return self.paint(ThemeRole.TITLE if selected else foreground,
                          background=ThemeRole.BUTTON_BG, bold=bold)
