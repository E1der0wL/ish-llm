"""Settings-style navigation for schema editors hosted inside a dialog."""

from llm.core.schema import SchemaKeyword

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window, DynamicContainer
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..sidebar import SidebarList, SidebarItem, SidebarHeading
from ..tab_page import TabPage
from ..content_inset import ContentInset
from ..actions import ActionFrame, ActionBar
from .settings_viewport import SettingsBody, SettingsPane
from ...locales.keys import MessageKey
from ...ui.input.keys import KeyName, ShortcutLabel, ShortcutAction
from ...ui.input.registry import ShortcutRegistry
from ...config.theme_style import ThemeSelector


class SettingsEditorLayout(VSplit):
    GENERAL = "general"
    CONFIG = "config"
    POLICY = "policy"

    def __init__(self, editor):
        self.editor = editor
        self.selected = self.GENERAL
        self.sections = [SidebarItem(self.GENERAL, editor.view.t(MessageKey.SETTINGS_GENERAL))]
        self.body = HSplit([Label(editor.view.t(MessageKey.COMPONENT_LOADING))])
        self.pane = SettingsPane(DynamicContainer(lambda: self.body),
                                 width=lambda: Dimension(min=1, preferred=self.main_width()))
        self.sidebar = SidebarList(lambda: self.sections, lambda: self.selected,
                                   self.select, on_activate=lambda: self.tabs.focus_tabs())
        registry = ShortcutRegistry().attach(self.sidebar)
        registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                     lambda e: self.sidebar.move(-1 if e.key_sequence[-1].key == KeyName.UP else 1))
        registry.add([KeyName.RIGHT, KeyName.ENTER, KeyName.SPACE], ShortcutLabel.ENTER_SPACE,
                     ShortcutAction.MAIN, lambda e: self.tabs.focus_tabs())
        self.sidebar.key_bindings = registry.bindings
        self.tabs = TabPage([
            (self.CONFIG, editor.view.t(MessageKey.SETTINGS_TAB_CONFIG), self.pane),
            (self.POLICY, editor.view.t(MessageKey.SETTINGS_TAB_POLICY), self.pane),
        ], width=self.main_width, on_change=lambda _: self.refresh(),
            on_enter=self.focus_fields, on_leave=self.finish_edit)
        actions = ActionBar(editor.buttons, self.main_width)
        self.actions = ActionFrame(actions, title=self.action_title)
        editor.engine.button.window.width = Dimension(min=1, weight=1)
        engine_keys = ShortcutRegistry.require(editor.engine.control)
        engine_keys.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL,
                        ShortcutAction.SELECT, self.move)
        engine_keys.add([KeyName.LEFT], ShortcutLabel.LEFT, ShortcutAction.SIDEBAR,
                        lambda e: self.focus_sidebar())
        engine_keys.scroll(lambda e: self.pane.scroll(e.key_sequence[-1].key))
        super().__init__([
            HSplit([
                SidebarHeading(lambda: editor.view.icons.agent + " " +
                               editor.view.t(MessageKey.AGENT_CATEGORY)),
                Window(self.sidebar),
            ], width=lambda: Dimension.exact(editor.view.sidebar_width())),
            Window(width=1, char="│", style=ThemeSelector.DIVIDER.style),
            HSplit([ContentInset(self.tabs, heading=self.page_title, bottom_padding=0), self.actions],
                   width=Dimension(min=1, weight=1)),
        ])

    def main_width(self):
        from ..dialog import PopupGeometry, PopupSize
        return max(1, PopupGeometry.dimensions(PopupSize.LARGE)[0].preferred
                   - self.editor.view.sidebar_width() - 3)

    def page_title(self):
        title = next(item.title for item in self.sections if item.key == self.selected)
        return self.editor.view.t(MessageKey.AGENT_PAGE_TITLE, page=title)

    def action_title(self):
        from ...asset.icon import SELECTED
        editor = self.editor
        active = not get_app().layout.has_focus(self.sidebar)
        name = editor.display_name.text.strip() or editor.identifier.text.strip()
        return [(ThemeSelector.FOCUSED.style if active else "",
                 (SELECTED + " " if active else "  ") +
                 editor.view.t(MessageKey.AGENT_CURRENT, name=name))]

    def fields(self):
        editor = self.editor
        if self.selected == self.GENERAL:
            return list(editor.identity.fields.values()) if self.tabs.selected == self.CONFIG else []
        if editor.form is None:
            return []
        return [field for path, field in editor.form.fields.items()
                if path and path[0] == self.selected
                and ((self.POLICY in path) == (self.tabs.selected == self.POLICY))]

    def controls(self):
        result = [field.input for field in self.fields()]
        if self.selected == self.GENERAL and self.tabs.selected == self.CONFIG:
            result.append(self.editor.engine.button)
        return result

    def finish_edit(self):
        for field in self.editor.all_fields():
            field.finish()

    def select(self, key):
        self.finish_edit()
        self.selected = key
        self.refresh()

    def refresh(self):
        editor = self.editor
        names = list(dict.fromkeys(path[0] for path in editor.form.fields if path)) if editor.form else []
        self.sections = [SidebarItem(self.GENERAL, editor.view.t(MessageKey.SETTINGS_GENERAL))]
        properties = editor.schema.get(SchemaKeyword.PROPERTIES, {}) if editor.schema else {}
        self.sections += [SidebarItem(name, properties.get(name, {}).get(SchemaKeyword.TITLE) or name) for name in names]
        if self.selected not in {item.key for item in self.sections}:
            self.selected = self.GENERAL
        fields = self.fields()
        children = [field.container for field in fields]
        if self.selected == self.GENERAL and self.tabs.selected == self.CONFIG:
            children += [Label(editor.view.t(MessageKey.AGENT_ENGINE)),
                         Label(editor.view.t(MessageKey.OPTION_HELP_AGENT_ENGINE),
                               style=ThemeSelector.MUTED.style), editor.engine]
        self.body = SettingsBody(children or [Label(editor.view.t(MessageKey.SETTINGS_NO_FIELDS))], fields)
        self.pane.vertical_scroll = 0
        get_app().layout.update_parents_relations()
        get_app().invalidate()

    def bind_field(self, field):
        registry = ShortcutRegistry.require(field.input.control)
        available = lambda: not field.editing and not self.editor.busy
        registry.add([KeyName.UP, KeyName.DOWN], ShortcutLabel.VERTICAL, ShortcutAction.SELECT,
                     self.move, when=available)
        registry.add([KeyName.LEFT], ShortcutLabel.LEFT, ShortcutAction.SIDEBAR,
                     lambda e: self.focus_sidebar(), when=available)
        registry.scroll(lambda e: self.pane.scroll(e.key_sequence[-1].key))

    def move(self, event):
        controls = self.controls()
        current = event.app.layout.current_control
        index = next((i for i, item in enumerate(controls) if item.control is current), 0)
        index += -1 if event.key_sequence[-1].key == KeyName.UP else 1
        if controls:
            event.app.layout.focus(controls[max(0, min(index, len(controls)-1))])

    def focus_fields(self):
        controls = self.controls()
        get_app().layout.focus(controls[0] if controls else self.editor.buttons[0])

    def focus_sidebar(self):
        self.finish_edit()
        get_app().layout.focus(self.sidebar)

    @property
    def escape_action(self):
        if get_app().layout.has_focus(self.sidebar):
            return ShortcutAction.CLOSE
        return ShortcutAction.TABS if self.tabs.content_focused else ShortcutAction.SIDEBAR

    def escape(self):
        if self.editor.engine.expanded:
            self.editor.engine.close()
        elif get_app().layout.has_focus(self.sidebar):
            self.editor.close()
        elif self.tabs.content_focused:
            self.tabs.focus_tabs()
        else:
            self.focus_sidebar()

    def cycle(self, reverse=False):
        if self.editor.engine.expanded:
            return
        if get_app().layout.has_focus(self.sidebar):
            return
        self.finish_edit()
        if any(get_app().layout.has_focus(button) for button in self.editor.buttons):
            self.focus_fields()
        else:
            get_app().layout.focus(self.editor.buttons[-1 if reverse else 0])
