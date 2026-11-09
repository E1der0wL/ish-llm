"""Connection status and failures are distinct from a feature's cached data."""
from dataclasses import dataclass
from enum import StrEnum

from ..locales import Locale, Message as M


class ConnectionState(StrEnum):
    DISABLED = "disabled"
    SETUP = "setup"
    AUTH_REQUIRED = "auth_required"
    READY = "ready"
    DENIED = "denied"
    FAILED = "failed"

    def label(self, locale=Locale()):
        return locale.text({self.DISABLED: M.CONNECTION_DISABLED, self.SETUP: M.CONNECTION_SETUP,
            self.AUTH_REQUIRED: M.CONNECTION_AUTH, self.READY: M.CONNECTION_READY,
            self.DENIED: M.CONNECTION_DENIED, self.FAILED: M.CONNECTION_FAILED}[self])


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    state: ConnectionState
    diagnostic: str = ""


class ConnectionError(Exception):
    def __init__(self, state: ConnectionState, diagnostic=""):
        self.status = ConnectionStatus(state, diagnostic)
        super().__init__(diagnostic or state.value)
