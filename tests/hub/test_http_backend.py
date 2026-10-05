"""Exercise production LiteLLM streaming against a local OpenAI-compatible stub."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime


class HTTPBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_backend_streams_from_configured_endpoint(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for text, finish in (("Hello ", None), ("**HTTP**", None), (None, "stop")):
                    chunk = {"id": "chatcmpl-hub-test", "object": "chat.completion.chunk",
                             "created": 0, "model": "test", "choices": [{"index": 0,
                             "delta": {"content": text} if text else {}, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"OPENAI_API_KEY": "local-test-key"}):
                runtime = HubRuntime(HubConfig(directory, "loop", "openai/test",
                                              f"http://127.0.0.1:{server.server_port}/v1", auto_title=False))
                try:
                    await runtime.start()
                    if not runtime.sessions:
                        await runtime.new_session()
                    await runtime.submit(runtime.selected_id, "local transport test")
                    async with asyncio.timeout(20):
                        await runtime.sessions[runtime.selected_id].run.wait_idle()
                    snapshot = await runtime.snapshot()
                    self.assertEqual(snapshot.messages[-1].text, "Hello **HTTP**", snapshot.status)
                    self.assertEqual(requests[0][0], "/v1/chat/completions")
                    self.assertEqual(requests[0][1]["model"], "test")
                    self.assertTrue(requests[0][1]["stream"])
                    self.assertEqual(requests[0][1]["messages"][-1]["content"], "local transport test")
                finally:
                    await runtime.close()
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
