"""Comment syntax, owner documentation and first-run configuration lifecycle."""
from dok import DockConfig
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import PropertyMock, patch
from prompt_toolkit.input import create_pipe_input
from dok import ConfigStore, SettingsError, WeatherLocation, Width, install, uninstall
from dok.settings.files import ConfigFiles
from dok.settings.jsonc import Jsonc
from dok.core.records import Record
from tests.dok.support import HostPrompt

class JsoncTests(unittest.TestCase):

    def test_comments_preserve_commands_urls_escapes_and_line_endings(self):
        values = [
            'https://example.com/a//b',
            "echo '/* keep */'",
            'echo "// keep"',
            'backslash\\',
            'slash\\"/*keep*/',
            '#ff0000',
            '한글 \uf002',
            'line\nnext'
        ]
        expected = {'values': values}
        for newline in ('\n', '\r\n', '\r'):
            source = '// heading' + newline + '{/* block' + newline + '*/' + json.dumps(expected, ensure_ascii=False)[1:-1] + '// tail' + newline + '}'
            self.assertEqual(Jsonc.loads(source), expected)
        self.assertEqual(Jsonc.loads('{"value": 1/* separator */} // EOF'), {'value': 1})

    def test_invalid_syntax_and_error_positions_are_retained(self):
        for text in (
            '{"value": 1,}',
            '{"value": [1,]}',
            '{unquoted: 1}',
            "{'key': 1}",
            '{"value": 1/* split */2}',
            '{"value": /* never closed',
            '{# comment\n}',
            '{"value": "unterminated}',
            '/* outer /* inner */ outer */{}'
        ):
            with self.subTest(text=text), self.assertRaises(json.JSONDecodeError):
                Jsonc.loads(text)
        text = '// heading\n{\n  /* comment\n     continued */\n  "value": nope\n}'
        with self.assertRaises(json.JSONDecodeError) as caught:
            Jsonc.loads(text)
        self.assertEqual(caught.exception.lineno, 5)
        self.assertEqual(caught.exception.colno, 12)

    def test_every_generated_field_has_owner_documentation_and_roundtrips(self):
        config = DockConfig()
        config = replace(
            config,
            widgets=replace(
                config.widgets,
                weather=replace(config.widgets.weather, location=WeatherLocation(0, 0, 'example'))
            )
        )
        text = Jsonc.dumps(config)
        self.assertEqual(Jsonc.loads(text), config.to_dict())
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(text)), config)

        def inspect(owner):
            for item in owner.documented_fields():
                self.assertTrue(item.description, (type(owner), item.name))
                self.assertEqual(owner.schema()['properties'][item.name]['description'], item.description)
                for line in item.description.splitlines():
                    self.assertIn('// ' + line, text)
                children = item.value if isinstance(item.value, (list, tuple)) else (item.value,)
                for value in children:
                    if isinstance(value, Record):
                        inspect(value)
        inspect(config)

    def test_default_path_ignores_old_json_and_preserves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            preferred = ConfigStore.for_home(directory)
            self.assertEqual(preferred.path.name, ConfigFiles.FILENAME)
            legacy = preferred.path.with_name('config.json')
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"width": {"value": 100}}', encoding='utf-8')
            before = legacy.read_bytes()
            selected = ConfigStore.for_home(directory)
            self.assertEqual(selected.path, preferred.path)
            self.assertEqual(selected.load(), DockConfig())
            self.assertFalse(preferred.path.exists())
            self.assertTrue(selected.create(DockConfig(dock={'width': Width(value=90)})))
            self.assertEqual(ConfigStore.for_home(directory).load().dock.width.value, 90)
            self.assertEqual(legacy.read_bytes(), before)
            preferred.path.write_text('{/* broken', encoding='utf-8')
            with self.assertRaises(SettingsError):
                ConfigStore.for_home(directory).load()

    def test_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ConfigFiles.FILENAME
            path.write_text('{"dock": {}, /* repeated */ "dock": {}}', encoding='utf-8')
            with self.assertRaises(SettingsError):
                ConfigStore(path).load()

    def test_explicit_paths_always_use_jsonc(self):
        with tempfile.TemporaryDirectory() as directory:
            config = DockConfig(dock={'width': Width(value=100)})
            for name in (ConfigFiles.FILENAME, 'custom.conf', 'extensionless'):
                with self.subTest(name=name):
                    store = ConfigStore(Path(directory) / name)
                    store.save(config)
                    text = store.path.read_text(encoding='utf-8')
                    self.assertIn('//', text)
                    self.assertEqual(Jsonc.loads(text), config.to_dict())
                    self.assertEqual(store.load(), config)

    def test_create_never_overwrites_an_existing_file_and_cleans_up_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore.for_home(directory)
            config = DockConfig()
            self.assertTrue(store.create(config))
            before = store.path.read_bytes()
            self.assertFalse(store.create(replace(config, dock=replace(config.dock, width=Width(value=100)))))
            self.assertEqual(store.path.read_bytes(), before)
            store.path.unlink()
            with patch('dok.settings.store.os.link', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.create(config)
            self.assertEqual(list(store.path.parent.iterdir()), [])

    def test_install_creates_complete_document_and_reload_preserves_custom_comments(self):
        from ish.config import config as host_config
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            prompt = HostPrompt(pipe)
            with patch.object(
                type(host_config),
                'ISH_HOME',
                new_callable=PropertyMock,
                return_value=Path(directory)
            ):
                dock = install(prompt)
                self.addCleanup(uninstall, prompt)
                self.assertEqual(dock.config_path.name, ConfigFiles.FILENAME)
                text = dock.config_path.read_text(encoding='utf-8')
                self.assertEqual(Jsonc.loads(text), dock.config.to_dict())
                self.assertIn('//', text)
                self.assertIn('"location": null', text)
                self.assertTrue(dock.config.widgets.shortcuts.shortcut(8).command.endswith(ConfigFiles.FILENAME))
                user_text = '// my note\n' + text
                dock.config_path.write_text(user_text, encoding='utf-8')
                self.assertIs(install(prompt), dock)
                self.assertEqual(dock.config_path.read_text(encoding='utf-8'), user_text)
                old = dock.config
                dock.config_path.write_text('{/* broken', encoding='utf-8')
                self.assertFalse(dock.reload())
                self.assertEqual(dock.config, old)
