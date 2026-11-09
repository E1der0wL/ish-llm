"""Public external connection contracts, independent of UI and model execution."""
from .models import ConnectionError, ConnectionState, ConnectionStatus
from .registry import ConnectionRegistry, ConnectionSpec

__all__ = ["ConnectionError", "ConnectionState", "ConnectionStatus", "ConnectionRegistry", "ConnectionSpec"]
