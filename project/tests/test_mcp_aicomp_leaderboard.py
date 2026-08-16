import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "server_ops" / "mcp_aicomp_leaderboard" / "server.py"


class McpClient:
    def __init__(self, root):
        fake_leaderboard = Path(root) / "fake_leaderboard.py"
        fake_leaderboard.write_text(
            (
                "import json\n"
                "print(json.dumps({"
                "'text':'面向噪声标签数据的细粒度图像识别鲁棒微调(发布时间：07月01日 17时00分) "
                "排名 参赛编号 团队名称 提交时间 分数 打分时间 "
                "3 AIC-2026-58579595 swpu_1 2026-07-01 16:07:00 78.5477 2026-07-01 16:11:02'"
                "}))\n"
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["AICOMP_MCP_ROOT"] = str(root)
        env["AICOMP_MCP_RUNNER_COMMAND_JSON"] = json.dumps(
            [sys.executable, "-c", "import time; time.sleep(0.2); print('runner done')"]
        )
        env["AICOMP_MCP_LEADERBOARD_COMMAND_JSON"] = json.dumps([sys.executable, str(fake_leaderboard)])
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


class McpAicompLeaderboardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "submissions").mkdir()
        (self.root / "tools").mkdir()
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

    def make_zip(self, name):
        path = self.root / "submissions" / name
        path.write_bytes(b"PK\x03\x04test")
        return str(path)

    def queued_names(self):
        return [item["name"] for item in self.client.call_tool("queue_status")["queued"]]

    def test_initialize_and_list_tools(self):
        response = self.client.request("tools/list")
        tool_names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertEqual(
            tool_names,
            {
                "queue_status",
                "leaderboard_snapshot",
            },
        )

    def test_mutating_tools_fail_closed(self):
        for name in (
            "queue_push_front",
            "queue_push_back",
            "queue_move_front",
            "queue_move_back",
            "queue_remove",
            "queue_runner_start",
            "queue_runner_watch",
            "queue_skip_score_wait",
        ):
            response = self.client.call_tool(name, {})
            self.assertEqual(response["error"]["code"], -32602)
            self.assertIn("LEGACY_MUTATION_DISABLED_USE_JINYINSAI_SUBMIT", response["error"]["message"])

    def test_leaderboard_snapshot_parses_team_result(self):
        result = self.client.call_tool("leaderboard_snapshot")

        self.assertTrue(result["ok"])
        self.assertEqual(result["parsed"]["leaderboardPublishTime"], "07月01日 17时00分")
        self.assertEqual(result["parsed"]["teamId"], "AIC-2026-58579595")
        self.assertEqual(result["parsed"]["teamName"], "swpu_1")
        self.assertEqual(result["parsed"]["teamSubmitTime"], "2026-07-01 16:07:00")
        self.assertEqual(result["parsed"]["score"], "78.5477")

    def test_queue_status_flags_failed_items_without_results_as_attention(self):
        path = self.make_zip("burned-by-cdp.zip")
        (self.root / "submissions" / "aicomp_submit_queue.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "queue": [
                        {
                            "index": 7,
                            "name": "burned-by-cdp.zip",
                            "path": path,
                            "priority": 100,
                            "status": "failed",
                            "score": "",
                            "note": "CDP timeout: Runtime.enable",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = self.client.call_tool("queue_status")

        self.assertEqual(result["state"], "needs_attention")
        self.assertEqual([item["name"] for item in result["failed_unscored"]], ["burned-by-cdp.zip"])

    def test_skip_score_is_disabled_in_legacy_server(self):
        response = self.client.request(
            "tools/call",
            {"name": "queue_skip_score_wait", "arguments": {"selector": "a.zip"}},
        )

        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("LEGACY_MUTATION_DISABLED_USE_JINYINSAI_SUBMIT", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
