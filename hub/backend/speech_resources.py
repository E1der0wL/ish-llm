"""In-process speech resources; these handles never enter stored configuration."""

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class SpeechResources:
    OPERATION: ClassVar[str] = 'get_speech_resources'
    calls: Any
    diagnostics: Any
