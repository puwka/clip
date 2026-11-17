import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend import ollama


class OllamaClientTest(unittest.TestCase):
    def test_network_settings_survive_reload(self):
        connection = ollama.Connection(url="http://192.0.2.10:11434/api/", model="qwen3.5")
        with tempfile.TemporaryDirectory() as temp, patch.object(ollama, "SETTINGS_FILE", Path(temp) / "ollama.json"):
            ollama.save(connection)
            saved = ollama.settings()
        self.assertEqual(saved.url, "http://192.0.2.10:11434")
        self.assertEqual(saved.mode, "network")
        self.assertEqual(saved.model, "qwen3.5:latest")

    def test_chat_retries_server_overload_but_releases_slot(self):
        calls = []

        def respond(request):
            calls.append(request.url.path)
            if len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"message": {"content": "OK"}})

        with (httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(respond)) as client,
              patch.object(ollama.time, "sleep")):
            result = ollama.chat(client, {"model": "test"}, lambda: None)
        self.assertEqual(result["message"]["content"], "OK")
        self.assertEqual(calls, ["/api/chat", "/api/chat"])
        self.assertEqual(ollama.queue_state(), {"waiting": 0, "active": 0})
