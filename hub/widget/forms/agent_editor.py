"""Agent forms use the selected Engine's public schema and optimistic revision."""

import json
from copy import deepcopy
from jsonschema import Draft202012Validator, ValidationError

from prompt_toolkit.application.current import get_app

from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDescription
from llm.core.schema import SchemaKeyword
from ...backend.execution.engine_selection import HubEngineIdentity
from ...backend.project.management_values import ManagementAction, EditorRecord
from ...locales.keys import MessageKey
from ..controls import Button
from ..dialog import Dialog, PopupSize
from ..dropdown import DropdownBox
from .settings import SchemaForm
from .manager_values import DefinitionDraft
from ...backend.project.settings_descriptions import describe_options
from ...backend.project.settings_help import SettingsHelp


class AgentEditor(Dialog):
    IDENTIFIER = "identifier"
    DISPLAY_NAME = "display_name"

    def __init__(self, manager, name="", record=None):
        self.manager, self.view, self.original_name = manager, manager.view, name
        self.version = EditorRecord(record).version if record else None
        definition = json.loads(EditorRecord(record).text) if record else AgentDefinition.selected(HubEngineIdentity.LOOP)
        self.engine_name = AgentDefinition.engine(definition)
        self.drafts = {self.engine_name: definition}
        self.form, self.busy, self.status = None, False, ""
        self.schema = None
        from ...backend.project.participants import AgentProfile
        from llm.core.schema import SchemaType
        properties = {
            self.IDENTIFIER: {SchemaKeyword.TYPE: SchemaType.STRING,
                           SchemaKeyword.TITLE: self.view.t(MessageKey.DEFINITION_NAME),
                           SchemaKeyword.DESCRIPTION: self.view.t(MessageKey.AGENT_IDENTIFIER_HELP)},
            self.DISPLAY_NAME: {SchemaKeyword.TYPE: SchemaType.STRING,
                             SchemaKeyword.TITLE: self.view.t(MessageKey.AGENT_DISPLAY_NAME),
                             SchemaKeyword.DESCRIPTION: self.view.t(MessageKey.AGENT_DISPLAY_NAME_HELP)},
        }
        values = {self.IDENTIFIER: name} if name else {}
        title = AgentProfile.title(name, definition)
        if title:
            values[self.DISPLAY_NAME] = title
        self.identity = SchemaForm({SchemaKeyword.TYPE: SchemaType.OBJECT,
                                    SchemaKeyword.PROPERTIES: properties}, values, self.view.t)
        self.identity.fields[(self.IDENTIFIER,)].readonly = bool(name)
        self.identifier = self.identity.fields[(self.IDENTIFIER,)].input
        self.display_name = self.identity.fields[(self.DISPLAY_NAME,)].input
        self.engines = []
        self.engine = DropdownBox(lambda: [(name, name) for name in self.engines],
                                  value=self.engine_name, on_change=self.change_engine,
                                  icons=lambda: self.view.icons)
        self.buttons = [Button(self.view.t(MessageKey.SETTINGS_SAVE), handler=self.save),
                        Button(self.view.t(MessageKey.CANCEL), handler=self.close)]
        from .editor_layout import SettingsEditorLayout
        self.workspace = SettingsEditorLayout(self)
        self.pane = self.workspace.pane
        from .agent_draft_tools import AgentDraftTools
        self.draft_tools = AgentDraftTools(self)
        super().__init__(title=self.view.t(MessageKey.AGENTS_TITLE),
                         body=self.workspace,
                         content_padding=0, status_bar=self.workspace.layout.status,
                         status_in_body=True,
                         size=PopupSize.LARGE)
        self.navigation = self.workspace
        for field in self.identity.fields.values():
            self.bind_field(field)
        self.on_close = self.close

    def load(self):
        self.busy = True
        token = self.view.progress.start(self.view.t(MessageKey.COMPONENT_LOADING))

        def ready(description):
            self.view.progress.finish(token)
            self.busy = False
            if self.view._dialog is not self:
                return
            if description is None:
                self.status = self.view.t(MessageKey.TOOLS_FAILED)
                return
            self.install_form(AgentDescription.from_dict(description), self.drafts[self.engine_name])

        self.manager.request(ManagementAction.DESCRIBE, self.engine_name, None,
                             self.view.progress.callback(token, ready))

    def install_form(self, description, definition):
        self.engines = [name for name in description.engines if not HubEngineIdentity.internal(name)]
        self.schema = deepcopy(description.schema)
        schema = deepcopy(self.schema)
        describe_options(schema, self.view.t, scope=SettingsHelp.AGENT)
        schema[SchemaKeyword.PROPERTIES].pop(AgentDefinition.Field.ENGINE, None)
        schema.pop(SchemaKeyword.ALL_OF, None)
        self.form = SchemaForm(schema, definition, self.view.t)
        for field in self.form.fields.values():
            self.bind_field(field)
        self.workspace.refresh()
        get_app().layout.update_parents_relations()
        get_app().invalidate()

    def all_fields(self):
        return [*self.identity.fields.values(), *(self.form.fields.values() if self.form else [])]

    def bind_field(self, field):
        field.enabled = lambda: not self.busy
        field.on_edit = lambda selected: get_app().create_background_task(self.edit_field(selected))
        self.workspace.bind_field(field)

    async def edit_field(self, field):
        if not self.manager.edit_text:
            return
        self.busy = True
        try:
            field.input.text = await self.manager.edit_text(field.input.text, self.view.general, suffix='.json')
        except Exception as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
        finally:
            self.busy = False
            field.finish()
            get_app().invalidate()

    def change_engine(self, name):
        if self.busy or name == self.engine_name:
            self.engine.value = self.engine_name
            return
        try:
            if self.form:
                self.drafts[self.engine_name] = self.form.values()
        except (ValueError, TypeError) as error:
            self.engine.value = self.engine_name
            self.status = self.view.t(MessageKey.ERROR, error=error)
            return
        self.engine_name = name
        self.engine.value = name
        self.drafts.setdefault(name, {AgentDefinition.Field.ENGINE: name})
        self.form = None
        self.load()

    def save(self):
        if self.busy or self.form is None:
            return
        name = self.identifier.text.strip()
        try:
            DefinitionDraft.require_name(name, self.view.t)
            values = self.form.values()
            values[AgentDefinition.Field.ENGINE] = self.engine_name
            from ...backend.project.participants import AgentProfile
            AgentProfile.rename(values, self.display_name.text or name)
            Draft202012Validator(self.schema).validate(values)
            text = EditorRecord.render(values)
            DefinitionDraft.require_size(text, self.view.t)
        except (ValueError, TypeError, ValidationError) as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
            return
        self.busy = True
        token = self.view.progress.start(self.view.t(MessageKey.COMPONENT_LOADING))

        def done(entries):
            self.view.progress.finish(token)
            self.busy = False
            if entries is None:
                self.status = self.view.t(MessageKey.DEFINITION_SAVE_FAILED)
            else:
                self.manager.entries, self.manager.selected = entries, name
                if self.view._dialog is self:
                    self.close()
            get_app().invalidate()

        self.manager.request(ManagementAction.SAVE if self.original_name else ManagementAction.CREATE,
                             EditorRecord.change(name, text), self.version,
                             self.view.progress.callback(token, done))

    def close(self):
        if not self.busy:
            self.view.dialogs.show(self.manager, self.manager.list)
