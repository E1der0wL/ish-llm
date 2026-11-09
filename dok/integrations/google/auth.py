"""Google OAuth Desktop flow and private atomic credentials, using Google's client library."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ...connections.models import ConnectionError, ConnectionState


class GoogleAuth:
    PROVIDER = "google"
    SCOPES = ("https://www.googleapis.com/auth/calendar.events.readonly",)
    WRITE_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
    ROOT = "connections"
    TOKEN_FILE = "credentials.json"
    LOCK_FILE = "credentials.lock"
    DESKTOP = "installed"
    CLIENT_ID = "client_id"

    def __init__(self, directory, config):
        self.config = config
        self.directory = Path(directory)
        digest = hashlib.sha256(config.id.encode("utf-8")).hexdigest()
        self.private = self.directory / self.ROOT / self.PROVIDER / digest
        self.path = self.private / self.TOKEN_FILE

    @contextmanager
    def _locked(self):
        self.private.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.private, 0o700)
        with (self.private / self.LOCK_FILE).open("a") as stream:
            os.chmod(stream.name, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _client_file(self):
        if not self.config.client_file:
            raise ConnectionError(ConnectionState.SETUP, "Set the OAuth Desktop client_file before connecting")
        path = Path(self.config.client_file).expanduser()
        return path if path.is_absolute() else self.directory / path

    def _client_config(self):
        try:
            with self._client_file().open(encoding="utf-8") as stream:
                client = json.load(stream)
            if not isinstance(client, dict) or not isinstance(client.get(self.DESKTOP), dict) or not client[self.DESKTOP].get(self.CLIENT_ID):
                raise ValueError("An OAuth Desktop client is required")
            return {self.DESKTOP: client[self.DESKTOP]}
        except (OSError, ValueError) as exc:
            raise ConnectionError(ConnectionState.SETUP, "Cannot read the OAuth Desktop client file") from exc

    def _flow(self):
        from google_auth_oauthlib.flow import InstalledAppFlow
        try:
            return InstalledAppFlow.from_client_config(self._client_config(), self.scopes, autogenerate_code_verifier=True)
        except ValueError as exc:
            raise ConnectionError(ConnectionState.SETUP, "Invalid OAuth Desktop client file") from exc

    def _save(self, credentials):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.private, delete=False) as stream:
                temporary = stream.name
                os.chmod(temporary, 0o600)
                stream.write(credentials.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            descriptor = os.open(self.private, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    @property
    def scopes(self):
        return self.WRITE_SCOPES if self.config.write else self.SCOPES

    def _check_scopes(self, credentials):
        granted = credentials.granted_scopes
        # A write grant also authorizes reads after write is disabled in settings.
        sufficient = lambda scopes: (credentials.has_scopes(scopes) and
                                     (granted is None or set(scopes).issubset(granted)))
        if not (sufficient(self.scopes) or not self.config.write and sufficient(self.WRITE_SCOPES)):
            raise ConnectionError(ConnectionState.DENIED, "Calendar read permission is missing")

    def token(self, refresh=False):
        """Runs off the UI loop; one lock covers load, refresh and replacement."""
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        client_id = self._client_config()[self.DESKTOP][self.CLIENT_ID]
        with self._locked():
            if not self.path.exists():
                raise ConnectionError(ConnectionState.AUTH_REQUIRED)
            try:
                credentials = Credentials.from_authorized_user_file(str(self.path))
            except (OSError, ValueError) as exc:
                raise ConnectionError(ConnectionState.AUTH_REQUIRED, "Reconnect this account") from exc
            # A changed OAuth client must not silently reuse a different client's token.
            if credentials.client_id != client_id:
                raise ConnectionError(ConnectionState.AUTH_REQUIRED, "OAuth client changed; reconnect this account")
            self._check_scopes(credentials)
            if refresh or not credentials.valid:
                try:
                    request = Request()
                    def timed_request(*args, **kwargs):
                        kwargs["timeout"] = self.config.timeout_s
                        return request(*args, **kwargs)
                    credentials.refresh(timed_request)
                except RefreshError as exc:
                    state = ConnectionState.FAILED if exc.retryable else ConnectionState.AUTH_REQUIRED
                    raise ConnectionError(state, "Google token refresh failed") from exc
                except TransportError as exc:
                    raise ConnectionError(ConnectionState.FAILED, "Google authentication service is unavailable") from exc
                self._check_scopes(credentials)
                self._save(credentials)
            return credentials.token

    def login(self):
        flow = self._flow()
        # OAuthlib owns state verification and PKCE. No deprecated copy/paste OOB flow.
        credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
            timeout_seconds=self.config.login_s, access_type="offline", prompt="consent")
        if not credentials.refresh_token:
            raise ConnectionError(ConnectionState.DENIED, "Offline calendar read permission was not granted")
        self._check_scopes(credentials)
        with self._locked():
            self._save(credentials)

    def disconnect(self):
        """Forget the local credential only; Google account access is not revoked."""
        with self._locked():
            self.path.unlink(missing_ok=True)

    @property
    def connected(self):
        return self.path.is_file()
