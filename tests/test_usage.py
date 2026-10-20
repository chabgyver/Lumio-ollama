"""Tests de la capture des tokens (Ollama et OpenAI-compatible) sans réseau externe."""

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import ollama_fs_access as ox  # noqa: E402

NDJSON = (
    '{"message": {"content": "bonjour"}, "done": false}\n'
    '{"message": {"content": ""}, "done": true, "eval_count": 12, "prompt_eval_count": 30}\n'
)
SSE = (
    'data: {"choices": [{"delta": {"content": "salut"}, "finish_reason": null}]}\n\n'
    'data: {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}\n\n'
    'data: [DONE]\n\n'
)


class _Handler(BaseHTTPRequestHandler):
    body = b""
    content_type = "application/x-ndjson"

    def do_POST(self):  # noqa: N802 - imposé par BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class _Server:
    def __init__(self, body: bytes, content_type: str):
        handler = type("_H", (_Handler,), {"body": body, "content_type": content_type})
        self.httpd = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class UsageOllamaTests(unittest.TestCase):
    def setUp(self):
        self.server = _Server(NDJSON.encode("utf-8"), "application/x-ndjson")
        self.old_url = ox.OLLAMA_URL
        ox.OLLAMA_URL = self.server.url
        ox.LAST_USAGE.clear()

    def tearDown(self):
        ox.OLLAMA_URL = self.old_url
        ox.LAST_USAGE.clear()
        self.server.close()

    def test_usage_capture(self):
        stats = []
        reply = ox.read_chat_stream(
            {"model": "test", "messages": [], "stream": True},
            on_stats=stats.append,
        )
        self.assertEqual(reply, "bonjour")
        self.assertEqual(ox.LAST_USAGE["total_tokens"], 42)
        self.assertEqual(ox.LAST_USAGE["prompt_tokens"], 30)
        self.assertEqual(ox.LAST_USAGE["provider"], "local")
        self.assertTrue(stats and "42" not in stats[0])  # 12 tok / 30 ctx dans le libellé
        self.assertIn("12 tok", stats[0])


class UsageOpenAITests(unittest.TestCase):
    def setUp(self):
        self.server = _Server(SSE.encode("utf-8"), "text/event-stream")
        ox.LAST_USAGE.clear()

    def tearDown(self):
        ox.LAST_USAGE.clear()
        self.server.close()

    def test_usage_capture(self):
        reply = ox.read_openai_stream(
            self.server.url,
            {"model": "test", "messages": [], "stream": True},
        )
        self.assertEqual(reply, "salut")
        self.assertEqual(ox.LAST_USAGE["total_tokens"], 18)
        self.assertEqual(ox.LAST_USAGE["provider"], "online")


class StreamUsageTests(unittest.TestCase):
    def test_local(self):
        usage = ox.stream_usage({"prompt_eval_count": 10, "eval_count": 20})
        self.assertEqual(usage["total_tokens"], 30)
        self.assertEqual(usage["provider"], "local")

    def test_online(self):
        usage = ox.stream_usage({"usage": {"prompt_tokens": 5, "completion_tokens": 7}}, "online")
        self.assertEqual(usage["total_tokens"], 12)
        self.assertEqual(usage["provider"], "online")

    def test_sans_tokens(self):
        self.assertEqual(ox.stream_usage({}), {})
        self.assertEqual(ox.stream_usage(None), {})


class PrixTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(ox._CATALOG_CACHE)
        ox._CATALOG_CACHE["openrouter"] = (
            0,
            [{"id": "qwen/qwen3.8-flash", "prompt": 0.03, "completion": 0.12,
              "context": 1000, "free": False, "vision": False, "tools": True,
              "vendor": "qwen", "name": "x", "description": "", "source": "openrouter"}],
        )

    def tearDown(self):
        ox._CATALOG_CACHE.clear()
        ox._CATALOG_CACHE.update(self.old)

    def test_prix_connu(self):
        price = ox.catalog_price("qwen/qwen3.8-flash")
        self.assertAlmostEqual(price["prompt"], 0.03)
        self.assertAlmostEqual(price["completion"], 0.12)

    def test_prix_inconnu(self):
        self.assertIsNone(ox.catalog_price("inconnu/modele"))
        self.assertIsNone(ox.catalog_price(""))


if __name__ == "__main__":
    unittest.main()
