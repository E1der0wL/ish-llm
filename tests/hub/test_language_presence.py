"""Current language choices preserve explicit values and reject invalid saves."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.preferences import PreferencesStore, configured_profile, prepare_profile
from hub.config.profile import UserProfile
from hub.config.restart import restart_config
from hub.locales import Language
from hub.locales.codes import LanguageCode


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

    def test_os_locale_matches_supported_packs_and_otherwise_uses_english(self):
        cases = (
            ({}, 'en'),
            ({'LANG': 'ko_KR.UTF-8'}, 'ko'),
            ({'LANG': 'KO-kr.utf8'}, 'ko'),
            ({'LANG': 'en_US.UTF-8'}, 'en'),
            ({'LANG': 'fr_FR.UTF-8'}, 'en'),
            ({'LANG': 'unrecognized locale'}, 'en'),
            ({'LANG': 'ko_KR.UTF-8', 'LC_MESSAGES': 'en_GB.UTF-8'}, 'en'),
            ({'LANG': 'en_US.UTF-8', 'LC_MESSAGES': 'ko_KR.UTF-8'}, 'ko'),
            ({'LANG': 'ko_KR.UTF-8', 'LC_ALL': 'en_US.UTF-8'}, 'en'),
            ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'fr:ko:en'}, 'ko'),
            ({'LANG': 'ko_KR.UTF-8', 'LANGUAGE': 'en:ko'}, 'en'),
            ({'LANG': 'C.UTF-8', 'LANGUAGE': 'ko'}, 'en'),
            ({'LANG': 'POSIX', 'LANGUAGE': 'ko'}, 'en'),
            ({'LANG': 'ko_KR.UTF-8', 'LC_ALL': 'C', 'LANGUAGE': 'ko'}, 'en'),
        )
        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                before = dict(os.environ)
                config = HubConfig(self.root)
                self.assertEqual(config.language, LanguageCode.AUTO)
                self.assertEqual(Language().code, expected)
                self.assertEqual(configured_profile(config).language, expected)
                prepared, _, issue = prepare_profile(config)
                self.assertIsNone(issue)
                self.assertEqual(prepared.language, expected)
                self.assertEqual(dict(os.environ), before)
                self.assertFalse(PreferencesStore(self.root).path.exists())

    def test_explicit_english_or_korean_overrides_os_language(self):
        for environment, explicit in (({'LANG': 'ko_KR.UTF-8'}, 'en'),
                                      ({'LANG': 'en_US.UTF-8'}, 'ko')):
            with self.subTest(explicit=explicit), patch.dict(os.environ, environment, clear=True):
                config = HubConfig(self.root, language=FalseyLanguage(explicit))
                self.assertEqual(configured_profile(config).language, explicit)
                self.assertEqual(prepare_profile(config)[0].language, explicit)

    def test_direct_runtime_resolves_automatic_language_for_model_guidance(self):
        with patch.dict(os.environ, {'LANG': 'ko_KR.UTF-8'}, clear=True):
            original = HubConfig(self.root)
            runtime = HubRuntime(original)
            self.assertEqual(runtime.config.language, 'ko')
            self.assertEqual(runtime.t.code, 'ko')
            self.assertEqual(original.language, '')

    def test_os_selection_includes_saved_packs_without_materializing_the_choice(self):
        store = PreferencesStore(self.root)
        packs = {'pt': {'settings_title': 'Definicoes'},
                 'pt_br': {'settings_title': 'Configuracoes'}}
        store.save('general', {'language': '', 'language_packs': packs})
        before = store.path.read_bytes()
        for locale, expected in (('pt_BR.UTF-8', 'pt_br'), ('pt-PT.UTF-8', 'pt')):
            with self.subTest(locale=locale), patch.dict(os.environ, {'LANG': locale}, clear=True):
                for resolve in (configured_profile, lambda value: prepare_profile(value)[0]):
                    effective = resolve(HubConfig(self.root))
                    self.assertEqual(effective.language, expected)
                    self.assertEqual(effective.language_packs, packs)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.load()['general']['language'], '')

    def test_restart_returns_to_os_selection_after_clearing_explicit_setting(self):
        store = PreferencesStore(self.root)
        launch = HubConfig(self.root)
        with patch.dict(os.environ, {'LANG': 'ko_KR.UTF-8'}, clear=True):
            store.save('general', {'language': 'en'})
            current = configured_profile(launch)
            self.assertEqual(current.language, 'en')
            store.save('general', {'language': ''})
            before = store.path.read_bytes()
            self.assertEqual(restart_config(launch, current).language, 'ko')
            self.assertEqual(store.path.read_bytes(), before)

    def test_startup_recovery_uses_os_language_without_repairing_invalid_settings(self):
        store = PreferencesStore(self.root)
        store.path.parent.mkdir()
        store.path.write_bytes(b'{"broken"')
        with patch.dict(os.environ, {'LANG': 'ko_KR.UTF-8'}, clear=True):
            config, _, issue = prepare_profile(HubConfig(self.root))
            self.assertIsNotNone(issue)
            self.assertEqual(config.language, 'ko')
            self.assertEqual(store.path.read_bytes(), b'{"broken"')

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
