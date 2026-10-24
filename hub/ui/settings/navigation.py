"""Settings focus, field editing and keyboard navigation state."""

from hub.locales.keys import MessageKey

import subprocess
from prompt_toolkit.application.current import get_app
from .. import host
from ..input.json_document import JsonDocument
from ...widget.focus import focus_control


def control(widget):
    return getattr(widget, "control", widget)


class SettingsNavigation:
    def __init__(self, screen):
        self.screen = screen
        self.main_zone = 0
        self.positions = [0, 0]
        self.left_key = "profile"

    def left_items(self):
        return [*self.screen.global_keys, *[item.key for item in self.screen.project_items()]]

    def left_control(self):
        return self.screen.global_list if self.left_key in self.screen.global_keys else self.screen.project_list

    def focused_key(self):
        current = get_app().layout.current_control
        if current == self.screen.global_list:
            return self.left_key if self.left_key in self.screen.global_keys else "profile"
        if current == self.screen.project_list:
            return self.left_key if self.left_key not in self.screen.global_keys else self.left_items()[len(self.screen.global_keys)]
        return None

    def groups(self):
        return [self.screen.page.body_widgets(), self.screen.page.buttons] if self.screen.page else [[], []]

    def focus_zone(self, zone):
        widgets = self.groups()[zone]
        if not widgets and zone == 0:
            zone, widgets = 1, self.groups()[1]
        if widgets:
            self.main_zone = zone
            focus_control(control(widgets[min(self.positions[zone], len(widgets) - 1)]))
        get_app().invalidate()

    def remember_zone(self):
        current = get_app().layout.current_control
        for zone, widgets in enumerate(self.groups()):
            controls = [control(widget) for widget in widgets]
            if current in controls:
                self.main_zone, self.positions[zone] = zone, controls.index(current)
                break

    @property
    def actions_focused(self):
        current = get_app().layout.current_control
        return bool(self.screen.page and any(control(button) is current for button in self.screen.page.buttons))

    @property
    def editing(self):
        current = get_app().layout.current_control
        return bool(self.screen.page and any(field.editing and field.input.control == current for field in self.screen.page.all_fields()))

    @property
    def project_focused(self):
        key = self.focused_key()
        return key is not None and key not in self.screen.global_keys

    def finish_edit(self):
        editing = self.editing
        if self.screen.page:
            for field in self.screen.page.all_fields():
                field.finish()
        return editing

    def edit_field(self, field):
        if self.screen.busy or field.readonly:
            return
        self.screen.busy = field.editing = True
        get_app().create_background_task(self._edit_field(field))

    async def _edit_field(self, field):
        try:
            kind = field.schema.get("type")
            kinds = kind if isinstance(kind, list) else [kind]
            field.input.text = await host.edit_text(field.input.text, self.screen.view.general,
                                               suffix=host.HostEditor.TEXT_SUFFIX if "string" in kinds else JsonDocument.SUFFIX)
            self.screen.status = ""
        except subprocess.CalledProcessError as error:
            self.screen.error(self.screen.t(MessageKey.SETTINGS_EDITOR_FAILED, code=error.returncode))
        except Exception as error:
            self.screen.error(error)
        finally:
            self.screen.busy = False
            field.finish()

    def switch_panel(self):
        if self.focused_key() is not None:
            return
        self.finish_edit()
        self.remember_zone()
        get_app().layout.focus(self.left_control())

    def focus(self, reverse=False):
        if self.focused_key() is not None:
            self.screen.layout.focus_main()
            return
        self.finish_edit()
        self.remember_zone()
        step = -1 if reverse else 1
        zone = self.main_zone
        for _ in range(2):
            zone = (zone + step) % 2
            if self.groups()[zone]:
                self.focus_zone(zone)
                break

    def move(self, key):
        if key not in ("up", "down") or self.actions_focused or self.focused_key() is not None:
            return
        current = get_app().layout.current_control
        usage = getattr(self.screen.page, "usage_output", None)
        if usage is not None and current == usage.control and key in ("up", "down"):
            if key == "down" or usage.buffer.document.cursor_position_row:
                usage.buffer.cursor_up() if key == "up" else usage.buffer.cursor_down()
                return
        self.remember_zone()
        widgets = self.groups()[0]
        if widgets:
            index = self.positions[0] + (-1 if key == "up" else 1)
            self.positions[0] = max(0, min(len(widgets) - 1, index))
            self.focus_zone(0)
