"""External JSON editing of drafts; persistence stays with SettingsDrafts."""

from hub.locales.keys import MessageKey

from llm.services.schema import ProjectConfiguration

from copy import deepcopy
from llm.core.models import ProjectConfig
from ..input.json_document import JsonDocument
import subprocess

from jsonschema import Draft202012Validator, ValidationError
from prompt_toolkit.application.current import get_app

from .pages import GlobalPage, ProjectPage
from .. import host
from ...backend.project.naming import TITLE_ENGINE
from ...widget.forms.settings import MISSING, at_path, put_path


def same_json(left, right):
    return JsonDocument.same(left, right, missing=MISSING)


def page_values(page):
    return page.values() if isinstance(page, ProjectPage) else page.form.values()


class SettingsJsonEditor:
    def __init__(self, screen):
        self.screen = screen
        self.recovery = None

    def open(self):
        screen = self.screen
        if screen.page is None or screen.busy:
            return
        screen.finish_edit()
        try:
            values = page_values(screen.page)
            exported = deepcopy(values)
            if isinstance(screen.page, ProjectPage):
                if "config" in exported:
                    exported["config"] = ProjectConfig.without_engine_parameters(exported["config"], TITLE_ENGINE)
            text = JsonDocument.render(exported, allow_non_finite=JsonDocument.STRICT_NON_FINITE)
        except (ValueError, TypeError) as error:
            screen.error(error)
            return
        if self.recovery and self.recovery[0] is screen.page:
            text = self.recovery[1]
        screen.busy = True
        get_app().create_background_task(self.edit(screen.page, values, text))

    def candidate(self, page, before, text):
        values = JsonDocument.parse(text)
        if not isinstance(values, dict):
            raise ValueError(self.screen.t(MessageKey.SETTINGS_JSON_OBJECT))
        if isinstance(page, ProjectPage):
            path = ProjectConfiguration.engine_form_path(TITLE_ENGINE)
            if at_path(values, path) is not MISSING:
                raise ValueError(self.screen.t(MessageKey.SETTINGS_HOST_LOCKED))
            schema = ProjectConfiguration.view(page.record).schema
        else:
            schema = page.form.schema
        try:
            Draft202012Validator(schema).validate(values)
        except ValidationError as error:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ValueError(f"{path}: {error.message}") from error
        if isinstance(page, ProjectPage):
            # Validate parents before restoring hidden data: put_path must not
            # silently turn an invalid null/string parent into an object.
            path = ProjectConfiguration.engine_form_path(TITLE_ENGINE)
            hidden = at_path(before, path)
            if hidden is not MISSING:
                put_path(values, path, deepcopy(hidden))
        for form in page.forms():
            for path in form.locked:
                if not same_json(at_path(before, path), at_path(values, path)):
                    raise ValueError(f"{'.'.join(path)}: {self.screen.t(MessageKey.SETTINGS_HOST_LOCKED)}")
        if isinstance(page, ProjectPage):
            record = ProjectConfiguration.with_values(page.record, values)
            candidate = ProjectPage(self.screen, record, suggest_defaults=False)
            candidate.record = page.record
            candidate.set_section(page.section if page.section in (
                "project:general", *("engine:" + key for key in candidate.engine_forms),
                *("component:" + key for key in candidate.component_forms)) else "project:general")
        else:
            candidate = GlobalPage(self.screen, page.name, values)
            candidate.save_request()  # Validate global value types without saving.
            candidate.original = page.original
        candidate.json_baseline = deepcopy(getattr(page, "json_baseline", page.form.original))
        candidate.json_changed = not same_json(page_values(candidate), candidate.json_baseline)
        return candidate

    async def edit(self, page, before, text):
        screen = self.screen
        edited = None
        try:
            edited = await host.edit_text(text, screen.view.general, suffix=JsonDocument.SUFFIX)
            if edited != text or self.recovery:
                candidate = self.candidate(page, before, edited)
                key = page.identifier if isinstance(page, ProjectPage) else page.name
                screen.drafts.pages[key] = candidate
                screen.selected = screen.navigation.left_key = (
                    candidate.section if isinstance(candidate, ProjectPage) else candidate.name)
                screen.show_page(candidate)
                screen._sidebar()
                screen.status = screen.t(MessageKey.SETTINGS_JSON_UPDATED)
            self.recovery = None
        except subprocess.CalledProcessError as error:
            screen.error(screen.t(MessageKey.SETTINGS_EDITOR_FAILED, code=error.returncode))
        except Exception as error:
            if edited is not None:
                self.recovery = (page, edited)
            screen.error(error)
        finally:
            screen.busy = False
            if screen.view.visible and screen.view.settings_open:
                # Rebuilt fields/buttons are new controls, so restore a stable target.
                get_app().layout.focus(screen.auxiliary.buttons["json"])
            get_app().invalidate()
