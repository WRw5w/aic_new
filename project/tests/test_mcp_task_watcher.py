import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "server_ops" / "mcp_task_watcher" / "server.py"


class McpClient:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, str(SERVER_PATH)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
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

    def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.time() + 5
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

    def read_until_method(self, method, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                message = self._read_message(deadline - time.time())
            except queue.Empty:
                continue
            if message.get("method") == method:
                return message
        raise AssertionError(f"Timed out waiting for notification {method}")

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


class McpTaskWatcherTests(unittest.TestCase):
    def setUp(self):
        self.client = McpClient()

    def tearDown(self):
        self.client.close()

    def initialize(self):
        response = self.client.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "unit-test", "version": "1.0.0"},
            },
        )
        self.client.notify("notifications/initialized")
        return response

    def test_initialize_and_list_tools(self):
        init_response = self.initialize()
        self.assertEqual(init_response["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("logging", init_response["result"]["capabilities"])

        tools_response = self.client.request("tools/list")
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}

        self.assertEqual(tool_names, {"watch_log", "watch_pid", "status"})

    def test_watch_log_pushes_completion_notification(self):
        self.initialize()
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "build_log.txt"
            log_path.write_text("building\n", encoding="utf-8")

            response = self.client.request(
                "tools/call",
                {
                    "name": "watch_log",
                    "arguments": {"path": str(log_path), "poll_interval": 0.05},
                },
            )
            self.assertIn("Started log watcher", response["result"]["content"][0]["text"])

            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("finished with exit code 0\n")

            notification = self.client.read_until_method("notifications/message", timeout=3)
            params = notification["params"]
            self.assertEqual(params["level"], "notice")
            self.assertEqual(params["data"]["message"], "任务已完成，退出码为 0")
            self.assertEqual(params["data"]["exitCode"], 0)

    def test_watch_pid_pushes_completion_notification(self):
        self.initialize()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.2)"])
        try:
            response = self.client.request(
                "tools/call",
                {
                    "name": "watch_pid",
                    "arguments": {"pid": child.pid, "poll_interval": 0.05},
                },
            )
            self.assertIn("Started PID watcher", response["result"]["content"][0]["text"])

            child.wait(timeout=3)
            notification = self.client.read_until_method("notifications/message", timeout=3)
            params = notification["params"]
            self.assertEqual(params["data"]["message"], "任务已完成，退出码为 0")
            self.assertEqual(params["data"]["exitCode"], 0)
            self.assertEqual(params["data"]["watcher"]["details"]["pid"], child.pid)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
