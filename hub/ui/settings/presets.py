"""Profile preset selection and configured-editor JSON authoring."""

from hub.locales.keys import MessageKey

from ..input.json_document import JsonDocument

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ...config.general import GeneralSettings
from ...config.profile import UserProfile
from ...config.theme import HubTheme
from ...widget.controls import Button
from ...widget.dialog import Dialog
from ...widget.input_field import InputField
from .. import host

EXPORT_CURRENT = object()

class PresetUI:
    def __init__(self, view):
        self.view = view
        self.names = []
        self.selected = ""
        self.loaded = False
        self.loading = False

    def items(self):
        return [(name, name) for name in self.names] + [
            (EXPORT_CURRENT, self.view.t(MessageKey.PRESET_EXPORT_CURRENT)), (None, self.view.t(MessageKey.PRESET_ADD))]

    def loaded_names(self, result):
        if result is not None:
            self.names, self.selected = result["names"], result["selected"]
            self.view.profile_bar.presets.value = self.selected
            self.loaded = True

    def call(self, operation, *args, completed):
        def done(result):
            if result is None:
                self.view.notice = self.view.settings.status
            completed(result)
            get_app().invalidate()
        self.view.settings.call(operation, *args, completed=done)

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        def done(result):
            self.loading = False
            self.loaded_names(result)
            # A bad file should report once, not trigger another disk read on
            # every output snapshot. Explicit operations can still try again.
            self.loaded = True
        self.call("preset_list", completed=done)

    def choose(self, name):
        screen = self.view.settings
        if screen.busy:
            return
        if any(page.dirty for page in screen.drafts.active_pages()):
            self.view.notice = self.view.t(MessageKey.PRESET_DRAFTS)
            return
        if name is None or name is EXPORT_CURRENT:
            self.view.profile_bar.presets.value = self.selected
            self.call("preset_export", self.view.project_id,
                      completed=lambda value: self.create(value, exporting=name is EXPORT_CURRENT))
            return
        self.call("preset_apply", name, self.view.project_id, completed=self.applied)

    def applied(self, result):
        if result is None:
            return
        screen = self.view.settings
        preferences = result["catalog"]["preferences"]
        screen.drafts.pages.clear()
        screen.page = None
        screen.drafts.saved_sidebar_width = preferences.get("appearance", {}).get("sidebar_width")
        screen.loaded_catalog(result["catalog"])
        self.view.user_profile = UserProfile(**preferences["profile"])
        self.view.general = GeneralSettings(**preferences["general"])
        self.view.set_theme(HubTheme(**preferences.get("appearance", {})))
        self.loaded_names(result["presets"])
        if result["batch"]["error"]:
            screen.error(result["batch"]["error"])
            self.view.notice = screen.status
        else:
            self.view.notice = self.view.t(MessageKey.PRESET_APPLIED)
            screen.drafts.prompt_restart(screen.global_keys)
        get_app().invalidate()

    def create(self, exported, *, exporting=False):
        if exported is None:
            return
        view, screen = self.view, self.view.settings
        draft = {"text": JsonDocument.render(exported, allow_non_finite=JsonDocument.ALLOW_NON_FINITE_PREVIEW), "editing": False}
        status = Label(lambda: draft.get("error", ""))
        def open_editor():
            if draft["editing"]:
                return
            draft["editing"] = True
            async def edit():
                try:
                    draft["text"] = await host.edit_text(draft["text"], view.general, suffix=JsonDocument.SUFFIX)
                    draft["error"] = ""
                except Exception as error:
                    from ..error_presentation import operation_notice
                    view.operation_diagnostics.record(getattr(view, "project_id", ""), error)
                    draft["error"] = operation_notice(error, view.t)
                finally:
                    draft["editing"] = False
                    get_app().invalidate()
            get_app().create_background_task(edit())
        name = InputField(button_text=view.t(MessageKey.PRESET_EDIT), button_width=12, on_action=open_editor)
        def save():
            if draft["editing"]:
                return
            try:
                from ...config.presets import PresetStore
                value = PresetStore.validate(JsonDocument.parse(draft["text"]))
                if not name.text.strip():
                    raise ValueError(view.t(MessageKey.SETTINGS_TITLE_REQUIRED))
            except (ValueError, TypeError) as error:
                draft["error"] = str(error)
                return
            def saved(result):
                if result is not None:
                    self.loaded_names(result)
                    if view._dialog is popup:
                        view.close_dialog()
                else:
                    draft["error"] = screen.status
            self.call("preset_save", name.text, value, completed=saved)
        popup = Dialog(title=view.t(MessageKey.PRESET_EXPORT_CURRENT if exporting else MessageKey.PRESET_ADD), body=HSplit([
            Label(view.t(MessageKey.PRESET_EXPORT_HINT if exporting else MessageKey.PRESET_HINT)), Label(view.t(MessageKey.PRESET_NAME)), name, status]),
            buttons=[Button(view.t(MessageKey.SETTINGS_SAVE), handler=save), Button(view.t(MessageKey.CANCEL), handler=view.close_dialog)])
        view.dialogs.show(popup, name.input)
