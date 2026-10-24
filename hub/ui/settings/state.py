"""Shared settings work state; callers retain the existing boolean observations."""


from typing import NamedTuple


class SettingsActivity:
    BUSY = True
    IDLE = False
    NO_STATUS = ""


class SettingsTarget(NamedTuple):
    UNSET = None
    project_id: str
    name: str
