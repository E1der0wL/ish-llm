"""Public translation identifiers, open lookup patterns and independent text contracts."""

from copy import deepcopy
import unittest

from hub.locales import Language, en, ko, validate_packs
from hub.locales.codes import LanguageCode
from hub.locales.keys import MessageKey, MessagePattern


class LocaleKeyTests(unittest.TestCase):
    def test_catalog_names_are_unique_and_both_packs_cover_the_same_public_keys(self):
        values = [value for name, value in vars(MessageKey).items() if not name.startswith("_")]
        self.assertTrue(all(type(value) is str for value in values))
        self.assertEqual(len(values), len(set(values)))
        self.assertEqual(set(values), set(en.MESSAGES))
        self.assertEqual(set(values), set(ko.MESSAGES))
        self.assertEqual((MessageKey.STOP, MessageKey.ELAPSED, MessageKey.SETTINGS_TITLE),
                         ("stop", "elapsed", "settings_title"))
        self.assertEqual((LanguageCode.BUILTIN, LanguageCode.DEFAULT), (("ko", "en"), "ko"))

    def test_open_names_and_independent_rendered_words_preserve_custom_fallback(self):
        self.assertEqual(MessagePattern.component_title("custom"), "custom_title")
        self.assertEqual(MessagePattern.settings_item("component"), "settings_component_item")
        self.assertEqual(MessagePattern.settings_group("provider"), "settings_group_provider")
        self.assertEqual(MessagePattern.recovery_result("unknown"), "recovery_result_unknown")
        self.assertEqual(Language("en")(MessageKey.STOP), "Stop")
        self.assertEqual(Language("ko")(MessageKey.STOP), "중단")
        self.assertEqual(Language("en").elapsed(3661), "Elapsed 1h 1m 1s")
        self.assertEqual(Language("ko").elapsed(3661), "소요 1시간 1분 1초")
        self.assertEqual(Language("en")(MessagePattern.component_title("custom")), "custom_title")
        source = {"custom": {"elapsed": "Time {duration}", "stop": "Halt"}}
        original = deepcopy(source)
        selected = Language("custom", source)
        self.assertEqual(selected.elapsed(61), "Time 1m 1s")
        self.assertEqual(selected("stop"), "Halt")
        self.assertEqual(source, original)
        source["custom"]["stop"] = "Changed"
        self.assertEqual(selected("stop"), "Halt")
        for template in ("{duration.__class__}", "{duration[secret]}", "{duration:{stop}}"):
            with self.subTest(template=template), self.assertRaises(ValueError):
                validate_packs({"custom": {"elapsed": template}})
