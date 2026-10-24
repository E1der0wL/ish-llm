"""Profile preset selection and configured-editor JSON authoring."""

from hub.locales.keys import MessageKey

from ..input.json_document import JsonDocument

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from dataclasses import dataclass
from ...backend.project.settings_contracts import PresetApplication, SettingsOperation
from ...config.preset_records import PresetIndex, PresetSelection
from ...widget.controls import Button
from ...widget.dialog import Dialog
from ...widget.input_field import InputField
from .. import host

EXPORT_CURRENT = object()


@dataclass
class PresetEditorDraft:
    EDITING = True
    IDLE = False
    NO_ERROR = ""

    text: str
    editing: bool = IDLE
    error: str = NO_ERROR

    @classmethod
    def from_export(cls, exported):
        return cls(
            JsonDocument.render(
                exported, allow_non_finite=JsonDocument.ALLOW_NON_FINITE_PREVIEW
            )
        )

    def start(self):
        self.editing = self.EDITING

    def accept_text(self, text):
        self.text = text
        self.error = self.NO_ERROR

    def finish(self):
        self.editing = self.IDLE


class PresetUI:
    NO_SELECTION = PresetIndex.UNSELECTED
    NEW_PRESET = None
    NO_RESULT = None
    LOADED = True
    NOT_LOADED = False
    LOADING = True
    NOT_LOADING = False
    NEW_DEFINITION = False
    EDIT_BUTTON_WIDTH = 12

    def __init__(self, view):
        self.view = view
        self.names = []
        self.selected = self.NO_SELECTION
        self.loaded = self.NOT_LOADED
        self.loading = self.NOT_LOADING

    def items(self):
        return [(name, name) for name in self.names] + [
            (EXPORT_CURRENT, self.view.t(MessageKey.PRESET_EXPORT_CURRENT)),
            (self.NEW_PRESET, self.view.t(MessageKey.PRESET_ADD)),
        ]

    def loaded_names(self, result):
        if result is not self.NO_RESULT:
            selection = PresetSelection.from_record(result)
            self.names, self.selected = selection.names, selection.selected
            self.view.profile_bar.presets.value = self.selected
            self.loaded = self.LOADED

    def call(self, operation, *args, completed):
        def done(result):
            if result is self.NO_RESULT:
                self.view.notice = self.view.settings.status
            completed(result)
            get_app().invalidate()

        self.view.settings.call(operation, *args, completed=done)

    def refresh(self):
        if self.loading:
            return
        self.loading = self.LOADING

        def done(result):
            self.loading = self.NOT_LOADING
            self.loaded_names(result)
            # A bad file should report once, not trigger another disk read on
            # every output snapshot. Explicit operations can still try again.
            self.loaded = self.LOADED

        self.call(SettingsOperation.PRESET_LIST, completed=done)

    def choose(self, name):
        screen = self.view.settings
        if screen.busy:
            return
        if any(page.dirty for page in screen.drafts.active_pages()):
            self.view.notice = self.view.t(MessageKey.PRESET_DRAFTS)
            return
        if name is self.NEW_PRESET or name is EXPORT_CURRENT:
            self.view.profile_bar.presets.value = self.selected
            self.call(
                SettingsOperation.PRESET_EXPORT,
                self.view.project_id,
                completed=lambda value: self.create(
                    value, exporting=name is EXPORT_CURRENT
                ),
            )
            return
        self.call(
            SettingsOperation.PRESET_APPLY,
            name,
            self.view.project_id,
            completed=self.applied,
        )

    def applied(self, result):
        if result is self.NO_RESULT:
            return
        screen = self.view.settings
        applied = PresetApplication.observe(result)
        preferences = applied.catalog.preferences
        screen.drafts.reset_for_preset(preferences)
        screen.loaded_catalog(applied.catalog.record)
        self.view.user_profile = preferences.required_profile()
        self.view.general = preferences.required_general()
        self.view.set_theme(preferences.theme())
        self.loaded_names(applied.presets_record)
        if applied.batch_error:
            screen.error(applied.batch_error)
            self.view.notice = screen.status
        else:
            self.view.notice = self.view.t(MessageKey.PRESET_APPLIED)
            screen.drafts.prompt_restart(screen.global_keys)
        get_app().invalidate()

    def create(self, exported, *, exporting=NEW_DEFINITION):
        if exported is self.NO_RESULT:
            return
        view, screen = self.view, self.view.settings
        draft = PresetEditorDraft.from_export(exported)
        status = Label(lambda: draft.error)

        def open_editor():
            if draft.editing:
                return
            draft.start()

            async def edit():
                try:
                    draft.accept_text(
                        await host.edit_text(
                            draft.text, view.general, suffix=JsonDocument.SUFFIX
                        )
                    )
                except Exception as error:
                    from ..error_presentation import operation_notice

                    view.operation_diagnostics.record_view(view, error)
                    draft.error = operation_notice(error, view.t)
                finally:
                    draft.finish()
                    get_app().invalidate()

            get_app().create_background_task(edit())

        name = InputField(
            button_text=view.t(MessageKey.PRESET_EDIT),
            button_width=self.EDIT_BUTTON_WIDTH,
            on_action=open_editor,
        )

        def save():
            if draft.editing:
                return
            try:
                from ...config.presets import PresetStore

                value = PresetStore.validate(JsonDocument.parse(draft.text))
                if not name.text.strip():
                    raise ValueError(view.t(MessageKey.SETTINGS_TITLE_REQUIRED))
            except (ValueError, TypeError) as error:
                draft.error = str(error)
                return

            def saved(result):
                if result is not self.NO_RESULT:
                    self.loaded_names(result)
                    if view.dialogs.current is popup:
                        view.close_dialog()
                else:
                    draft.error = screen.status

            self.call(SettingsOperation.PRESET_SAVE, name.text, value, completed=saved)

        popup = Dialog(
            title=view.t(
                MessageKey.PRESET_EXPORT_CURRENT if exporting else MessageKey.PRESET_ADD
            ),
            body=HSplit(
                [
                    Label(
                        view.t(
                            MessageKey.PRESET_EXPORT_HINT
                            if exporting
                            else MessageKey.PRESET_HINT
                        )
                    ),
                    Label(view.t(MessageKey.PRESET_NAME)),
                    name,
                    status,
                ]
            ),
            buttons=[
                Button(view.t(MessageKey.SETTINGS_SAVE), handler=save),
                Button(view.t(MessageKey.CANCEL), handler=view.close_dialog),
            ],
        )
        view.dialogs.show(popup, name.input)
