import json
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "server_ops" / "mcp_remote_train" / "server.py"


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


class RemoteTrainHelperTests(unittest.TestCase):
    def test_validate_label_rejects_shell_metacharacters(self):
        from server_ops.mcp_remote_train import server

        with self.assertRaises(ValueError):
            server.validate_label("train; rm -rf /")

    def test_wrapper_script_writes_exit_marker_and_status(self):
        from server_ops.mcp_remote_train import server

        spec = server.JobSpec(
            label="smoke",
            command="python train.py",
            work_dir="/root/work",
            log_file="/root/smoke.log",
            pid_file="/root/smoke.pid",
            status_file="/root/smoke.status.json",
        )
        script = server.build_wrapper_script(spec)

        self.assertIn("REMOTE_TRAIN_EXIT_CODE:$rc", script)
        self.assertIn("write_status \"$state\" \"$rc\"", script)
        self.assertIn("python train.py", script)

    def test_pid_gone_without_exit_code_is_unknown_lost(self):
        from server_ops.mcp_remote_train import server

        result = server.derive_state({"state": "running", "exit_code": None}, pid=123, pid_alive=False)

        self.assertEqual(result["state"], "unknown_lost")
        self.assertFalse(result["terminal"])
        self.assertIn("without a recorded exit code", result["reason"])

    def test_success_requires_zero_exit_code(self):
        from server_ops.mcp_remote_train import server

        success = server.derive_state({"state": "success", "exit_code": 0}, pid=123, pid_alive=False)
        missing_code = server.derive_state({"state": "success", "exit_code": None}, pid=123, pid_alive=False)

        self.assertEqual(success["state"], "success")
        self.assertTrue(success["terminal"])
        self.assertEqual(missing_code["state"], "unknown_lost")
        self.assertFalse(missing_code["terminal"])


class RemoteTrainMcpTests(unittest.TestCase):
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

    def test_initialize_and_list_remote_train_tools(self):
        init_response = self.initialize()
        self.assertEqual(init_response["result"]["protocolVersion"], "2025-11-25")

        tools_response = self.client.request("tools/list")
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}

        self.assertEqual(
            tool_names,
            {"start_job", "status_job", "watch_job", "watchers_status", "stop_job", "collect_job"},
        )

    def test_watchers_status_starts_empty(self):
        self.initialize()

        response = self.client.request("tools/call", {"name": "watchers_status", "arguments": {}})
        payload = json.loads(response["result"]["content"][0]["text"])

        self.assertEqual(payload, [])


if __name__ == "__main__":
    unittest.main()
