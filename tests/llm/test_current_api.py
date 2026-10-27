"""Current-only APIs and stored preferences; creation defaults remain owner-defined."""

from llm.engines.loop import LoopEngine

from dataclasses import asdict
import inspect
import tempfile
import unittest

from hub.config.general import GeneralSettings
from hub.config.preferences import PreferencesStore
from hub.config.profile import UserProfile
from hub.config.theme import HubTheme
from llm.llm import LargeLanguageModel
from llm.engines.loop import LoopEngine
from llm.services.api import Projects
from llm.services.infrastructure.backups import DirectoryBackups
from llm.services.lifecycle.projects import ProjectManager
from llm.services.runtime.runs import RunManager


class CurrentAPITests(unittest.TestCase):
    def test_loop_retry_evidence_is_explicit_and_independent_of_key_spelling(self):
        engine = LoopEngine()
        for key in ("tool:1:call", "arbitrary-key"):
            for container in (False, True):
                valid = {"status": "started", "container": container, "requires_retry": not container}
                checkpoint = {"header": {"format": LoopEngine.CHECKPOINT_FORMAT}, "records": {key: valid}}
                retry = () if container else (key,)
                engine.validate_resume(checkpoint, retry_nodes=retry)
                candidates = [
                    {name: value for name, value in valid.items() if name != missing}
                    for missing in ("container", "requires_retry")
                ]
                candidates.extend({**valid, "requires_retry": value} for value in (None, 0, 1, container))
                for value in candidates:
                    with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, "explicit"):
                        engine.validate_resume({**checkpoint, "records": {key: value}}, retry_nodes=retry)
        for value in (False, 0, [], ""):
            with self.subTest(decisions=value), self.assertRaises(TypeError):
                engine.validate_resume({"header": {"format": LoopEngine.CHECKPOINT_FORMAT}, "records": {}},
                                       decisions=value)

    def test_only_subscription_observation_and_current_backup_api(self):
        for owner in (LargeLanguageModel, RunManager):
            with self.subTest(owner=owner):
                parameters = inspect.signature(owner).parameters
                self.assertNotIn("on_event", parameters)
                self.assertNotIn("on_run_event", parameters)
                self.assertFalse(hasattr(owner, "on_event"))
        for owner, name in ((Projects, "upgrade_backup"), (Projects, "aupgrade_backup"),
                            (ProjectManager, "upgrade_backup"), (DirectoryBackups, "upgrade"),
                            (HubTheme, "from_preferences")):
            with self.subTest(owner=owner, name=name):
                self.assertFalse(hasattr(owner, name))


class CurrentPreferencesTests(unittest.TestCase):
    def test_creation_materializes_defaults_and_reopening_preserves_values(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            self.assertEqual(store.load(), {})
            for section, owner, values in (
                ("profile", UserProfile, {"email": ""}),
                ("appearance", HubTheme, {"foreground": "default"}),
                ("general", GeneralSettings, {"provider_request_summary": False, "language_packs": {}}),
            ):
                with self.subTest(section=section):
                    expected = asdict(owner(**values))
                    self.assertEqual(store.save(section, values), expected)
                    before = store.path.read_bytes()
                    self.assertEqual(PreferencesStore(root).load()[section], expected)
                    self.assertEqual(store.path.read_bytes(), before)

    def test_reader_rejects_missing_and_old_fields_without_rewriting(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            for section, owner in (("profile", UserProfile), ("appearance", HubTheme),
                                   ("general", GeneralSettings)):
                current = asdict(owner())
                candidates = [{}, None, [], {**current, "unknown_option": True}]
                for key in current:
                    if section == "appearance" and key in {"comment2", "accent4", "accent5"}:
                        # New optional color in the current schema; old required
                        # fields and removed aliases must remain strict below.
                        value = {name: item for name, item in current.items() if name != key}
                        store._write_json({"version": store.VERSION, section: value})
                        before = store.path.read_bytes()
                        self.assertEqual(store.load()[section], current)
                        self.assertEqual(getattr(HubTheme(**store.load()[section]), key), current[key])
                        self.assertEqual(store.path.read_bytes(), before)
                        continue
                    candidates.append({name: value for name, value in current.items() if name != key})
                old = ({"accent": "#112233"}, {"user": "#112233"}, {"muted": "#112233"})
                if section == "appearance":
                    candidates.extend({**current, **extra} for extra in old)
                if section == "general":
                    candidates.extend({**current, key: False} for key in
                                      ("mouse_mode", "recovery_max_attempts", "provider_error_details"))
                for values in candidates:
                    with self.subTest(section=section, values=values):
                        store._write_json({"version": store.VERSION, section: values})
                        before = store.path.read_bytes()
                        with self.assertRaises(TypeError):
                            store.load()
                        with self.assertRaises(TypeError):
                            store.save("profile", {})
                        self.assertEqual(store.path.read_bytes(), before)

    def test_reader_rejects_unknown_sections(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            store._write_json({"version": store.VERSION, "old_settings": {}})
            before = store.path.read_bytes()
            with self.assertRaises(ValueError):
                store.load()
            self.assertEqual(store.path.read_bytes(), before)
