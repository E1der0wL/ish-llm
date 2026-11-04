"""Explicit MCP discovery and selection using shared component manager chrome."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import to_filter
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.widgets import Label

from hub.backend.project.management_values import ManagementEntry, MCPOperation
from hub.config.theme_style import ThemeSelector
from hub.locales.keys import MessageKey as K
from hub.ui.output.labels import ellipsize
from ..controls import Button, CheckboxList, ScrollbarMargin
from ..dialog import Dialog, PopupSize
from ..dropdown import DropdownBox
from ..section import Heading
from .record_manager import RecordManager


class MCPManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        buttons = (
            Button(view.t(K.MCP_CONNECT), handler=self.connect),
            Button(view.t(K.DEFINITION_EDIT), handler=self.open_item),
            Button(view.t(K.SETTINGS_DELETE), handler=self.delete),
        )
        super().__init__(view, request, name=name, edit_text=edit_text, extra_buttons=buttons)

    def connect(self):
        entry = next((item for item in self.entries if item["name"] == self.selected), None)
        if self.busy or entry is None:
            return

        def ready(record):
            if self.view._dialog is self:
                popup = MCPToolSelection(self, record)
                self.view.dialogs.show(popup, popup.agent.button)

        self.call(MCPOperation.DISCOVER, self.selected, ManagementEntry(entry).version, completed=ready)


class MCPToolSelection(Dialog):
    """Offline choices after discovery; applying revises only one Agent."""

    def __init__(self, manager, record):
        self.manager, self.view, self.record = manager, manager.view, record
        self.busy, self.status, self.drafts = False, "", {}
        self.current_agent = None
        t = self.view.t
        self.agent = DropdownBox(
            lambda: [(item["name"], item["label"]) for item in record["agents"]],
            placeholder=t(K.MCP_NO_AGENT), on_change=self.select_agent,
            width=36, icons=lambda: self.view.icons,
        )
        self.tools = CheckboxList([(None, t(K.MCP_NO_TOOLS))])
        self.tools.window.style = ThemeSelector.POPUP_LIST.style
        self.tools.window.right_margins = [ScrollbarMargin(display_arrows=True)]
        self.tools.window.height = None
        self.tools.window.dont_extend_height = to_filter(False)
        self.tools.window.dont_extend_width = to_filter(False)
        self.on_close = self.close
        self.apply_button = Button(t(K.CONFIRM), handler=self.apply)
        super().__init__(
            title=t(K.MCP_TOOL_SELECTION, server=record["server"]),
            body=HSplit([
                Heading(t(K.MCP_AGENT)), self.agent, Window(height=1),
                Heading(t(K.MCP_TOOLS)), self.tools, Window(height=1),
                Label(t(K.MCP_SELECTION_HINT), style=ThemeSelector.MUTED.style),
            ]),
            buttons=[self.apply_button, Button(t(K.CANCEL), handler=self.close)],
            size=PopupSize.MEDIUM,
            status_bar=self.view.progress.container(lambda: self.status, surface=self),
        )
        if record["agents"]:
            self.agent.value = record["agents"][0]["name"]
            self.select_agent(self.agent.value)

    def select_agent(self, name):
        if self.current_agent is not None:
            self.drafts[self.current_agent] = list(self.tools.current_values)
        self.current_agent = name
        selected = next(item for item in self.record["agents"] if item["name"] == name)
        previous = selected["bindings"]
        checked = self.drafts.get(name, list(previous.values()))
        rows = [(item["name"], [
            ("", item["name"] + "\n   "),
            (ThemeSelector.MUTED.style + " nobold", ellipsize(" ".join(item["description"].split()), 76)),
        ]) for item in self.record["tools"]]
        available = {item[0] for item in rows}
        rows.extend((item, item + " • " + self.view.t(K.MCP_UNAVAILABLE))
                    for item in dict.fromkeys(checked) if item not in available and item is not None)
        self.tools.values = rows or [(None, self.view.t(K.MCP_NO_TOOLS))]
        self.tools.current_values = [item for item in checked if item is not None]
        self.tools._selected_index = 0
        get_app().invalidate()

    def close(self):
        # Leaving a slow operation never makes a late callback reopen this popup.
        self.view.dialogs.show(self.manager, self.manager.list)

    def apply(self):
        if self.busy or self.current_agent is None:
            return
        agent = next(item for item in self.record["agents"] if item["name"] == self.current_agent)
        value = {key: self.record[key] for key in ("server", "definition_version", "tools_version")}
        value.update(agent=agent["name"], agent_revision=agent["revision"],
                     tools=[name for name in self.tools.current_values if name is not None])
        self.busy = True
        token = self.view.progress.start(self.view.t(K.PROGRESS_BACKEND_WRITE))

        def ready(result):
            self.view.progress.finish(token)
            self.busy = False
            if result is None:
                self.status = self.view.t(K.TOOLS_FAILED)
            else:
                self.manager.status = self.view.t(K.MCP_APPLIED, agent=result["agent"], count=result["tools"])
                if self.view._dialog is self:
                    self.close()
            get_app().invalidate()

        try:
            self.manager.request(MCPOperation.SELECT, value, None,
                                 self.view.progress.callback(token, ready), progress=token)
        except Exception:
            self.view.progress.finish(token)
            self.busy = False
            raise
