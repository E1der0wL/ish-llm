"""Provider declarations, settings composition and lazy capability resolution."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import jsonschema

from dok import DockConfig, SettingsError
from dok.app.composition import connections
from dok.connections import ConnectionError, ConnectionRegistry, ConnectionSpec, ConnectionState, ConnectionStatus
from dok.features.calendar.config import CalendarConfig
from dok.features.calendar.contracts import CalendarSource
from dok.integrations.google.config import GoogleAccount
from dok.settings.connections import ConnectionsConfig
from dok.settings.jsonc import Jsonc


class ConnectionConfigurationTests(unittest.TestCase):
    def test_runtime_defaults_are_empty_and_generated_examples_are_disabled(self):
        self.assertEqual(DockConfig().connections.google, ())
        self.assertEqual(DockConfig().popups.calendar.sources, ())
        initial = DockConfig.initial()
        self.assertFalse(initial.connections.google[0].enabled)
        self.assertFalse(initial.connections.google[0].write)
        self.assertFalse(initial.popups.calendar.sources[0].enabled)
        self.assertEqual(initial.connections.google[0].client_file, "")
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(initial))), initial)
        jsonschema.validate(initial.to_dict(), DockConfig.schema())
        self.assertEqual(connections(initial.connections, "/unused")[0].id,
                         initial.popups.calendar.sources[0].connection)

    def test_owner_rejects_ambiguous_sources_and_account_ids(self):
        with self.assertRaises(SettingsError):
            ConnectionsConfig(google=(GoogleAccount(), GoogleAccount()))
        with self.assertRaises(SettingsError):
            CalendarConfig(sources=(GoogleAccount().calendar_source(), GoogleAccount().calendar_source()))
        for owner, data in ((GoogleAccount, {"timeout_s": 0}), (GoogleAccount, {"id": " "}),
                            (CalendarSource, {"refresh_s": 0}), (CalendarSource, {"calendar": ""})):
            with self.subTest(data=data):
                if owner is CalendarSource:
                    data = {**GoogleAccount().calendar_source().to_dict(), **data}
                with self.assertRaises(SettingsError):
                    owner.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, owner.schema())

    def test_lazy_connections_and_generation_change_preserve_only_matching_instances(self):
        instance = SimpleNamespace(resolve=lambda capability: "value" if capability is str else None,
                                   status=ConnectionStatus(ConnectionState.READY))
        factory = Mock(return_value=instance)
        spec = ConnectionSpec("one", "example", False, 1, factory)
        registry = ConnectionRegistry()
        registry.configure((spec,))
        self.assertEqual(registry.status("one").state, ConnectionState.DISABLED)
        factory.assert_not_called()
        registry.configure((replace(spec, enabled=True),))
        self.assertEqual(registry.require("one", str), "value")
        revision = registry.revision
        registry.configure((replace(spec, enabled=True, create=Mock()),))
        self.assertEqual(registry.revision, revision)
        self.assertEqual(registry.require("one", str), "value")
        factory.assert_called_once()
        with self.assertRaises(ConnectionError):
            registry.require("one", int)
        registry.configure((replace(spec, enabled=True, settings=2),))
        registry.require("one", str)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(registry.status("missing").state, ConnectionState.SETUP)
