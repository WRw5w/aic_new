import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "server_ops" / "mcp_automation" / "server.py"
THREAD_ID = "019f1b78-a971-7933-8083-2110e29c6b89"


class McpClient:
    def __init__(self, automation_root):
        env = os.environ.copy()
        env["CODEX_AUTOMATIONS_ROOT"] = str(automation_root)
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_PATH)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.next_id = 1
        self.messages = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.reader.join(timeout=1)

    def request(self, method, params=None, timeout=5):
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self._read_message(deadline - time.time())
            except queue.Empty:
                continue
            if message.get("id") == request_id:
                return message
        raise AssertionError(f"Timed out waiting for response to {method}")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name, arguments=None, timeout=5):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)
        if "error" in response:
            return response
        text = response["result"]["content"][0]["text"]
        return json.loads(text)

    def _send(self, message):
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _read_stdout(self):
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except Exception as exc:
            self.messages.put(exc)

    def _read_message(self, timeout):
        try:
            message = self.messages.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise AssertionError(f"Server exited before sending a message. stderr={stderr!r}")
            raise
        if isinstance(message, Exception):
            raise AssertionError(f"Reader failed: {message!r}")
        return message


class McpAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.client = McpClient(self.root)
        self.client.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "unit-test", "version": "1.0.0"},
            },
        )
        self.client.notify("notifications/initialized")

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def read_toml(self, automation_id):
        path = self.root / automation_id / "automation.toml"
        with path.open("rb") as handle:
            return tomllib.load(handle)

    def create_alarm(self, automation_id="score-check"):
        return self.client.call_tool(
            "create_heartbeat",
            {
                "id": automation_id,
                "name": "Score check",
                "prompt": "Resume score check. Do not poll.",
                "target_thread_id": THREAD_ID,
                "delay_minutes": 5,
            },
        )

    def test_initialize_and_list_tools(self):
        response = self.client.request("tools/list")
        tool_names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertEqual(
            tool_names,
            {
                "list_automations",
                "get_automation",
                "create_heartbeat",
                "update_heartbeat",
                "pause_automation",
                "resume_automation",
                "delete_automation",
                "validate_automation",
            },
        )

    def test_create_heartbeat_writes_codex_automation_toml(self):
        result = self.create_alarm()

        self.assertTrue(result["ok"])
        self.assertEqual(result["automation"]["id"], "score-check")
        self.assertEqual(result["automation"]["status"], "ACTIVE")
        self.assertEqual(result["automation"]["rrule"], "FREQ=MINUTELY;INTERVAL=5;COUNT=1")
        self.assertTrue((self.root / "score-check" / "automation.toml").exists())

        data = self.read_toml("score-check")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["kind"], "heartbeat")
        self.assertEqual(data["prompt"], "Resume score check. Do not poll.")
        self.assertEqual(data["target_thread_id"], THREAD_ID)
        self.assertIsInstance(data["created_at"], int)
        self.assertEqual(data["created_at"], data["updated_at"])

    def test_list_and_get_automation_return_structured_data(self):
        self.create_alarm()

        listed = self.client.call_tool("list_automations")
        self.assertEqual([item["id"] for item in listed["automations"]], ["score-check"])
        self.assertEqual(listed["automations"][0]["prompt_summary"], "Resume score check. Do not poll.")

        fetched = self.client.call_tool("get_automation", {"id": "score-check"})
        self.assertEqual(fetched["automation"]["name"], "Score check")
        self.assertIn('id = "score-check"', fetched["toml"])

    def test_update_pause_and_resume_preserve_created_at(self):
        self.create_alarm()
        created_at = self.read_toml("score-check")["created_at"]
        time.sleep(0.002)

        updated = self.client.call_tool(
            "update_heartbeat",
            {
                "id": "score-check",
                "name": "Updated score check",
                "prompt": "Check score once, then pause this automation.",
                "rrule": "FREQ=MINUTELY;INTERVAL=1",
            },
        )
        self.assertTrue(updated["ok"])
        data = self.read_toml("score-check")
        self.assertEqual(data["created_at"], created_at)
        self.assertGreaterEqual(data["updated_at"], created_at)
        self.assertEqual(data["name"], "Updated score check")
        self.assertEqual(data["rrule"], "FREQ=MINUTELY;INTERVAL=1")

        paused = self.client.call_tool("pause_automation", {"id": "score-check"})
        self.assertEqual(paused["automation"]["status"], "PAUSED")
        self.assertEqual(self.read_toml("score-check")["status"], "PAUSED")

        resumed = self.client.call_tool("resume_automation", {"id": "score-check"})
        self.assertEqual(resumed["automation"]["status"], "ACTIVE")
        self.assertEqual(self.read_toml("score-check")["status"], "ACTIVE")

    def test_validate_reports_missing_required_fields(self):
        bad_dir = self.root / "bad"
        bad_dir.mkdir()
        (bad_dir / "automation.toml").write_text(
            'version = 1\nid = "bad"\nkind = "heartbeat"\nstatus = "ACTIVE"\n',
            encoding="utf-8",
        )

        result = self.client.call_tool("validate_automation", {"id": "bad"})

        self.assertFalse(result["ok"])
        self.assertIn("name is required", result["errors"])
        self.assertIn("prompt is required", result["errors"])
        self.assertIn("rrule is required", result["errors"])
        self.assertIn("target_thread_id is required", result["errors"])

    def test_delete_requires_reason_and_removes_directory(self):
        self.create_alarm()

        missing_reason = self.client.request(
            "tools/call",
            {"name": "delete_automation", "arguments": {"id": "score-check"}},
        )
        self.assertEqual(missing_reason["error"]["code"], -32602)
        self.assertIn("reason is required", missing_reason["error"]["message"])

        deleted = self.client.call_tool("delete_automation", {"id": "score-check", "reason": "test cleanup"})
        self.assertTrue(deleted["ok"])
        self.assertFalse((self.root / "score-check").exists())

    def test_rejects_unsafe_id_and_invalid_status(self):
        unsafe = self.client.request(
            "tools/call",
            {
                "name": "create_heartbeat",
                "arguments": {
                    "id": "../escape",
                    "name": "Bad",
                    "prompt": "Bad",
                    "target_thread_id": THREAD_ID,
                    "delay_minutes": 1,
                },
            },
        )
        self.assertEqual(unsafe["error"]["code"], -32602)
        self.assertIn("id must match", unsafe["error"]["message"])

        bad_status = self.client.request(
            "tools/call",
            {
                "name": "create_heartbeat",
                "arguments": {
                    "id": "bad-status",
                    "name": "Bad",
                    "prompt": "Bad",
                    "target_thread_id": THREAD_ID,
                    "delay_minutes": 1,
                    "status": "RUNNING",
                },
            },
        )
        self.assertEqual(bad_status["error"]["code"], -32602)
        self.assertIn("status must be ACTIVE or PAUSED", bad_status["error"]["message"])


if __name__ == "__main__":
    unittest.main()
