"""Current language choices preserve explicit values and reject invalid saves."""

from pathlib import Path
import tempfile
import unittest

from hub.backend.runtime import HubConfig
from hub.config.preferences import PreferencesStore, configured_profile
from hub.config.profile import UserProfile


class FalseyLanguage(str):
    def __bool__(self):
        return False


class LanguagePresenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_explicit_profile_language_is_not_discarded_by_truthiness(self):
        store = PreferencesStore(self.root)
        store.save('general', {'language': ''})
        before = store.path.read_bytes()
        config = HubConfig(self.root, language='ko',
                           user_profile=UserProfile(language=FalseyLanguage('en')))
        resolved = configured_profile(config)
        self.assertEqual(resolved.language, 'en')
        self.assertEqual(resolved.user_profile.language, 'en')
        self.assertEqual(store.path.read_bytes(), before)

    def test_invalid_falsey_language_cannot_replace_valid_preference_file(self):
        for index, value in enumerate((FalseyLanguage('unsupported'), None, False, 0, [], {})):
            with self.subTest(value=repr(value)):
                store = PreferencesStore(self.root / f'case-{index}')
                store.save('general', {'language': 'en', 'editor': 'nano'})
                before = store.path.read_bytes()
                error = None
                try:
                    store.save('general', {'language': value})
                except (TypeError, ValueError) as caught:
                    error = caught
                self.assertEqual(store.path.read_bytes(), before, 'Invalid language rewrote the actual preferences')
                self.assertIsInstance(error, (TypeError, ValueError))
                self.assertEqual(store.load()['general']['language'], 'en')

    def test_current_general_profile_and_host_choice_order_survives_roundtrip(self):
        store = PreferencesStore(self.root)
        store.save('profile', {'display_name': 'Reader', 'language': 'en'})
        store.save('general', {'language': FalseyLanguage('ko')})
        self.assertEqual(store.load()['general']['language'], 'ko')
        self.assertEqual(configured_profile(HubConfig(self.root, language='en')).language, 'ko')
        store.save('general', {'language': ''})
        self.assertEqual(configured_profile(HubConfig(self.root, language='ko')).language, 'en')
        store.save('profile', {'display_name': 'Reader', 'language': ''})
        before = store.path.read_bytes()
        self.assertEqual(configured_profile(HubConfig(self.root, language='ko')).language, 'ko')
        self.assertEqual(store.path.read_bytes(), before)
