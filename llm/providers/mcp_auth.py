"""Explicit MCP OAuth wiring. Token storage and browser/callback UX are injected."""

from copy import deepcopy
from llm.core.configuration import UNSET


class MCPOAuth:
    """SDK-owned metadata + storage protocol, instantiated only for selected HTTP servers.

    A separate instance/storage must be selected for each credential namespace.
    No browser, callback listener, secret file or login attempt is started here.
    Connecting with this object explicitly enables the SDK's OAuth challenge and
    refresh behavior. Tool transport failures are never replayed by this wrapper.
    """

    STORAGE_METHODS = ("get_tokens", "set_tokens", "get_client_info", "set_client_info")

    def __init__(self, *, server_url, client_metadata, storage, redirect_handler, callback_handler,
                 client_metadata_url=UNSET):
        if not isinstance(server_url, str) or not server_url:
            raise ValueError("MCP OAuth requires its explicit server URL")
        if not isinstance(client_metadata, dict):
            raise TypeError("MCP OAuth client_metadata must be an object")
        if any(not callable(getattr(storage, name, None)) for name in self.STORAGE_METHODS):
            raise TypeError("MCP OAuth requires an injected SDK TokenStorage")
        if not callable(redirect_handler) or not callable(callback_handler):
            raise TypeError("MCP OAuth requires explicit redirect and callback handlers")
        self.client_metadata, self.storage = deepcopy(client_metadata), storage
        self.server_url = server_url
        self.redirect_handler, self.callback_handler = redirect_handler, callback_handler
        self.client_metadata_url = client_metadata_url

    def create_auth(self, server_url):
        # A connector can be selected by several definitions; credentials cannot
        # follow it to a different endpoint merely because both speak OAuth.
        if server_url != self.server_url:
            raise ValueError("MCP OAuth credentials belong to another server URL")
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
        options = {} if self.client_metadata_url is UNSET else {"client_metadata_url": self.client_metadata_url}
        return OAuthClientProvider(server_url=server_url,
            client_metadata=OAuthClientMetadata.model_validate(deepcopy(self.client_metadata)),
            storage=self.storage, redirect_handler=self.redirect_handler,
            callback_handler=self.callback_handler, **options)
