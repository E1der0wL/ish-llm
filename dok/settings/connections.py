"""Compose external connection settings from provider-owned declarations."""
from dataclasses import dataclass

from ..core.records import Nested, Record, Sequence, SettingsError, setting
from ..integrations.google.config import GoogleAccount


@dataclass(frozen=True, slots=True)
class ConnectionsConfig(Record):
    google: tuple[GoogleAccount, ...] = setting(Sequence(Nested(GoogleAccount)), default=(),
        description="Google account connections. Empty disables all Google access. Each entry declares its own ID, enabled flag and OAuth client file; credentials are never stored in this settings file.")

    def _check(self):
        if len({account.id for account in self.google}) != len(self.google):
            raise SettingsError("Connection IDs must be unique")

    @classmethod
    def initial(cls):
        return cls(google=(GoogleAccount(),))

    def account(self, identifier):
        return next((account for account in self.google if account.id == identifier), None)
