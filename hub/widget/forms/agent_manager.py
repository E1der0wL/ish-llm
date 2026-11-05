"""The ordinary Agent manager, retaining each entry's explicit owning Project."""

from dataclasses import dataclass, field
from hub.backend.project.management_values import AgentLocation, ManagementEntry
from hub.locales.keys import MessageKey
from .definition_manager import DefinitionManager
from .agent_editor import AgentEditor


@dataclass(slots=True)
class AgentEditBinding:
    view: object
    request: object
    edit_text: object
    entries: list = field(default_factory=list)
    selected: str = ""


class AgentManager(DefinitionManager):
    def entry_title(self, item):
        title = super().entry_title(item)
        return self.view.t(MessageKey.PLUGIN_AGENT_LABEL, plugin=item[AgentLocation.PLUGIN],
                           name=title) if AgentLocation.PLUGIN in item else title

    def agent_editor(self, name="", record=None):
        entry = next((row for row in self.entries if ManagementEntry(row).name == name), {})
        identifier = entry.get(AgentLocation.AGENT, name)
        target = entry.get(AgentLocation.PROJECT)

        def request(operation, argument, version, done, **kwargs):
            self.request(operation, argument, version, done, target_project=target, **kwargs)

        def restore():
            if binding.selected:
                self.entries = binding.entries
                self.selected = next((ManagementEntry(row).name for row in self.entries
                    if row.get(AgentLocation.AGENT) == binding.selected
                    and (target is None or row.get(AgentLocation.PROJECT) == target)), self.selected)
            self.view.dialogs.show(self, self.list)
            self.reload()

        binding = AgentEditBinding(self.view, request, self.edit_text)
        editor = AgentEditor(binding, identifier, record, on_return=restore)
        self.view.dialogs.show(editor, editor.workspace.sidebar)
        editor.load()
