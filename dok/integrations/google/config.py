"""Google account configuration; schemas and JSONC comments share these declarations."""
from dataclasses import dataclass
from enum import StrEnum

from ...core.records import Boolean, Choice, POSITIVE_SECONDS, Record, Text, setting
from ...features.calendar.contracts import CalendarSource


class GuestUpdates(StrEnum):
    ALL = "all"
    EXTERNAL = "externalOnly"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class GoogleAccount(Record):
    id: str = setting(Text(nonblank=True, single_line=True), default="google",
        description="Unique connection ID used by calendar sources. This is a local alias, not your Google email address.")
    enabled: bool = setting(Boolean(), default=False,
        description="Allow this account to be used by configured sources. Disabled by default; never opens a login browser automatically.")
    client_file: str = setting(Text(single_line=True), default="",
        description="Path to the Google OAuth Desktop app client JSON. Relative paths resolve beside config.jsonc; ~ uses the Linux home. Authenticate with python -m dok.connections login --config PATH --account ID. Tokens are kept separately from settings.")
    timeout_s: float = setting(POSITIVE_SECONDS, default=20,
        description="HTTP request timeout in seconds for this Google connection.")
    login_s: float = setting(POSITIVE_SECONDS, default=180,
        description="Seconds to wait for the explicit browser OAuth login's localhost callback.")
    write: bool = setting(Boolean(), default=False,
        description="Allow creating, editing and deleting Google events. Enable then log in again to grant calendar.events permission. Actual calendar permissions are still enforced; false keeps read-only access.")
    updates: GuestUpdates = setting(Choice(GuestUpdates), default=GuestUpdates.ALL,
        description="Google guest notifications for explicit event saves/deletes: all, externalOnly (non-Google guests), or none. This does not change dok's local reminders.")

    def calendar_source(self) -> CalendarSource:
        """Provider-owned, disabled example for the account's primary calendar."""
        return CalendarSource(connection=self.id, calendar="primary", label="Google")
