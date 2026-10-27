"""Filter and external JSON editing for Agent drafts; public schemas own validation."""

import asyncio
from copy import deepcopy

from jsonschema import Draft202012Validator
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDescription
from ...backend.project.management_values import ManagementAction
from ...backend.project.participants import AgentProfile
from ...backend.execution.engine_selection import HubEngineIdentity
from ...locales.keys import MessageKey
from ...ui.input.json_document import JsonDocument
from ..controls import Button, TextArea
from ..dialog import Dialog
from .manager_values import DefinitionDraft


class AgentDraftTools:
    def __init__(self, editor):
        self.editor = editor
        self.recovery_text = None

    def search(self):
        editor = self.editor
        if editor.busy or editor.form is None:
            return
        editor.workspace.finish_edit()
        field = TextArea(text=editor.workspace.query, height=1, multiline=False)

        def restore():
            editor.view.dialogs.show(editor, editor.workspace.sidebar)
            editor.workspace.tabs.focus_tabs()

        def apply():
            editor.workspace.query = field.text.strip()
            editor.workspace.refresh()
            restore()

        popup = Dialog(title=editor.view.t(MessageKey.SETTINGS_SEARCH),
                       body=HSplit([Label(editor.view.t(MessageKey.SETTINGS_SEARCH_HINT)), field]),
                       buttons=[Button(editor.view.t(MessageKey.CONFIRM), handler=apply),
                                Button(editor.view.t(MessageKey.CANCEL), handler=restore)])
        popup.on_close = restore
        field.buffer.accept_handler = lambda _: (apply(), True)[1]
        editor.view.dialogs.show(popup, field)

    def open_json(self):
        editor = self.editor
        if editor.busy or editor.form is None or not editor.manager.edit_text:
            return
        editor.workspace.finish_edit()
        try:
            value = editor.form.values()
            value[AgentDefinition.Field.ENGINE] = editor.engine_name
            AgentProfile.rename(value, editor.display_name.text or editor.identifier.text)
            text = self.recovery_text or JsonDocument.render(value, allow_non_finite=False)
        except (ValueError, TypeError) as error:
            editor.status = editor.view.t(MessageKey.ERROR, error=error)
            return
        editor.busy = True
        get_app().create_background_task(self.edit_json(text))

    async def edit_json(self, text):
        editor = self.editor
        token = editor.view.progress.start(editor.view.t(MessageKey.COMPONENT_LOADING))
        try:
            edited = await editor.manager.edit_text(text, editor.view.general, suffix=JsonDocument.SUFFIX)
            self.recovery_text = edited
            DefinitionDraft.require_size(edited, editor.view.t)
            value = JsonDocument.parse(edited)
            name = AgentDefinition.engine(value)
            future = asyncio.get_running_loop().create_future()

            def ready(record):
                if not future.done():
                    future.set_result(record)

            editor.manager.request(ManagementAction.DESCRIBE, name, None, ready)
            record = await future
            if record is None:
                raise ValueError(editor.view.t(MessageKey.TOOLS_FAILED))
            description = AgentDescription.from_dict(record)
            Draft202012Validator(description.schema).validate(value)
            if name not in description.engines or HubEngineIdentity.internal(name):
                raise ValueError(editor.view.t(MessageKey.SETTINGS_ENGINE_REQUIRED))
            editor.drafts[editor.engine_name] = editor.form.values()
            editor.drafts[name] = deepcopy(value)
            editor.engine_name = editor.engine.value = name
            editor.display_name.text = AgentProfile.title(editor.identifier.text, value)
            editor.install_form(description, value)
            editor.status = editor.view.t(MessageKey.SETTINGS_JSON_UPDATED)
            self.recovery_text = None
        except Exception as error:
            editor.status = editor.view.t(MessageKey.ERROR, error=error)
        finally:
            editor.busy = False
            editor.view.progress.finish(token)
            get_app().invalidate()
