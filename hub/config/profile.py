"""UI identity, independent of provider and conversation persistence."""

from dataclasses import dataclass, field
from getpass import getuser


@dataclass(frozen=True, slots=True)
class UserProfile:
    display_name: str = field(default_factory=getuser)
    language: str = ""
    email: str = ""
    phone: str = ""

    def __post_init__(self):
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name must be a nonempty string")
        if self.language not in ("", "ko", "en"):
            raise ValueError("language must be empty, ko or en")
        for key in ("email", "phone"):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f"{key} must be a string")
