"""Settings focus, field editing and keyboard navigation state."""

from hub.locales.keys import MessageKey

import subprocess
from enum import IntEnum
from llm.core.schema import SchemaKeyword, SchemaType
from ...config.sections import PreferenceSection
from ..input.keys import KeyName
from ...widget.input_contract import EditableControl
from .pages import GlobalPage
from .state import SettingsActivity
from prompt_toolkit.application.current import get_app
from .. import host
from ..input.json_document import JsonDocument
from ...widget.focus import focus_control


def control(widget):
    return EditableControl.resolve(widget)


class SettingsZone(IntEnum):
    FIELDS = 0
    ACTIONS = 1


class SettingsNavigation:
    FIRST_POSITION = 0
    REVERSE = False
    NO_KEY = None

    def __init__(self, screen):
        self.screen = screen
        self.main_zone = SettingsZone.FIELDS
        self.reset_positions()
        self.left_key = PreferenceSection.PROFILE.value

    def reset_positions(self):
        self.positions = [self.FIRST_POSITION for _ in SettingsZone]

    def left_items(self):
        return [
            *self.screen.global_keys,
            *[item.key for item in self.screen.project_items()],
        ]

    def left_control(self):
        return (
            self.screen.global_list
            if self.left_key in self.screen.global_keys
            else self.screen.project_list
        )

    def focused_key(self):
        current = get_app().layout.current_control
        if current == self.screen.global_list:
            return (
                self.left_key
                if self.left_key in self.screen.global_keys
                else PreferenceSection.PROFILE.value
            )
        if current == self.screen.project_list:
            return (
                self.left_key
                if self.left_key not in self.screen.global_keys
                else self.left_items()[len(self.screen.global_keys)]
            )
        return self.NO_KEY

    def groups(self):
        return (
            [self.screen.page.body_widgets(), self.screen.page.buttons]
            if self.screen.page
            else [[], []]
        )

    def focus_zone(self, zone):
        widgets = self.groups()[zone]
        if not widgets and zone == SettingsZone.FIELDS:
            zone, widgets = SettingsZone.ACTIONS, self.groups()[SettingsZone.ACTIONS]
        if widgets:
            self.main_zone = zone
            focus_control(
                control(widgets[min(self.positions[zone], len(widgets) - 1)])
            )
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
        return bool(
            self.screen.page
            and any(control(button) is current for button in self.screen.page.buttons)
        )

    @property
    def editing(self):
        current = get_app().layout.current_control
        return bool(
            self.screen.page
            and any(
                field.editing and field.input.control == current
                for field in self.screen.page.all_fields()
            )
        )

    @property
    def project_focused(self):
        key = self.focused_key()
        return key is not self.NO_KEY and key not in self.screen.global_keys

    def finish_edit(self):
        editing = self.editing
        if self.screen.page:
            for field in self.screen.page.all_fields():
                field.finish()
        return editing

    def edit_field(self, field):
        if self.screen.busy or field.readonly:
            return
        self.screen.busy = field.editing = SettingsActivity.BUSY
        get_app().create_background_task(self._edit_field(field))

    async def _edit_field(self, field):
        try:
            kind = field.schema.get(SchemaKeyword.TYPE)
            kinds = kind if isinstance(kind, list) else [kind]
            field.input.text = await host.edit_text(
                field.input.text,
                self.screen.view.general,
                suffix=(
                    host.HostEditor.TEXT_SUFFIX
                    if SchemaType.STRING in kinds
                    else JsonDocument.SUFFIX
                ),
            )
            self.screen.status = SettingsActivity.NO_STATUS
        except subprocess.CalledProcessError as error:
            self.screen.error(
                self.screen.t(MessageKey.SETTINGS_EDITOR_FAILED, code=error.returncode)
            )
        except Exception as error:
            self.screen.error(error)
        finally:
            self.screen.busy = SettingsActivity.IDLE
            field.finish()

    def switch_panel(self):
        if self.focused_key() is not self.NO_KEY:
            return
        self.finish_edit()
        self.remember_zone()
        get_app().layout.focus(self.left_control())

    def focus(self, reverse=REVERSE):
        if self.focused_key() is not self.NO_KEY:
            self.screen.layout.focus_main()
            return
        self.finish_edit()
        self.remember_zone()
        step = -1 if reverse else 1
        zone = self.main_zone
        for _ in range(len(SettingsZone)):
            zone = (zone + step) % len(SettingsZone)
            if self.groups()[zone]:
                self.focus_zone(zone)
                break

    def move(self, key):
        if (
            key not in (KeyName.UP, KeyName.DOWN)
            or self.actions_focused
            or self.focused_key() is not self.NO_KEY
        ):
            return
        current = get_app().layout.current_control
        usage = GlobalPage.usage_output_for(self.screen.page)
        if (
            usage is not GlobalPage.NO_USAGE_OUTPUT
            and current == usage.control
            and key in (KeyName.UP, KeyName.DOWN)
        ):
            if key == KeyName.DOWN or usage.buffer.document.cursor_position_row:
                (
                    usage.buffer.cursor_up()
                    if key == KeyName.UP
                    else usage.buffer.cursor_down()
                )
                return
        self.remember_zone()
        widgets = self.groups()[SettingsZone.FIELDS]
        if widgets:
            index = self.positions[SettingsZone.FIELDS] + (
                -1 if key == KeyName.UP else 1
            )
            self.positions[SettingsZone.FIELDS] = max(
                self.FIRST_POSITION, min(len(widgets) - 1, index)
            )
            self.focus_zone(SettingsZone.FIELDS)
