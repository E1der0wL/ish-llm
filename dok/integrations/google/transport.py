"""One authenticated HTTP session per operation, with a single 401 refresh."""
from contextlib import asynccontextmanager
import httpx


class GoogleSession:
    def __init__(self, connection, client, token):
        self.connection, self.client, self.token = connection, client, token
        self.refreshed = False

    async def request(self, method, url, *, headers=None, **kwargs):
        while True:
            response = await self.client.request(method, url,
                headers={**(headers or {}), "Authorization": "Bearer " + self.token}, **kwargs)
            if response.status_code != 401 or self.refreshed:
                return response
            self.token = await self.connection.token(refresh=True)
            self.refreshed = True

    async def get(self, url, **kwargs):
        return await self.request("GET", url, **kwargs)


class GoogleTransport:
    def __init__(self, connection, client=httpx.AsyncClient):
        self.connection, self.client = connection, client

    @asynccontextmanager
    async def session(self):
        token = await self.connection.token()
        async with self.client(timeout=self.connection.config.timeout_s, follow_redirects=False) as client:
            yield GoogleSession(self.connection, client, token)
