"""UI identity, independent of provider and conversation persistence."""

from hub.locales.codes import LanguageCode

from dataclasses import dataclass, field
from typing import ClassVar
from .metadata import SettingMetadata
from getpass import getuser


@dataclass(frozen=True, slots=True)
class UserProfile:
    AUTO_LANGUAGE: ClassVar[str] = ""
    INVALID_NAME: ClassVar[str] = "display_name must be a nonempty string"
    INVALID_LANGUAGE: ClassVar[str] = "language must be empty, ko or en"
    INVALID_STRING: ClassVar[str] = "{key} must be a string"

    display_name: str = field(default_factory=getuser)
    language: str = field(
        default=AUTO_LANGUAGE,
        metadata={SettingMetadata.REQUIRES_RESTART: SettingMetadata.RESTART},
    )
    email: str = ""
    phone: str = ""

    def __post_init__(self):
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError(self.INVALID_NAME)
        if self.language not in (self.AUTO_LANGUAGE, *LanguageCode.BUILTIN):
            raise ValueError(self.INVALID_LANGUAGE)
        for key in (UserProfile.email.__name__, UserProfile.phone.__name__):
            if not isinstance(getattr(self, key), str):
                raise ValueError(self.INVALID_STRING.format(key=key))

    @classmethod
    def schema_properties(cls):
        return SettingMetadata.properties(cls)
