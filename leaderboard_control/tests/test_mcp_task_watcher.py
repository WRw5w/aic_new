import io
import json
import threading
import time

import pytest

from mak.mcp.task_watcher import (
    DEFAULT_WATCH_POLL_INTERVAL_SECONDS,
    MAX_COMPLETED_WATCHERS,
    MAX_WATCHERS,
    MAX_WATCH_POLL_INTERVAL_SECONDS,
    MIN_WATCH_POLL_INTERVAL_SECONDS,
    UNKNOWN_EXIT_CODE,
    TaskWatcherServer,
    confined_log_path,
)


def test_task_watcher_server_exposes_watch_tools():
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())

    names = {tool["name"] for tool in server.tools()}

    assert {"watch_log", "watch_pid", "status"} <= names


def test_task_watcher_defaults_to_30_minutes():
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())
    tools = {tool["name"]: tool for tool in server.tools()}

    assert DEFAULT_WATCH_POLL_INTERVAL_SECONDS == 30 * 60
    assert tools["watch_log"]["inputSchema"]["properties"]["poll_interval"]["default"] == 30 * 60
    assert tools["watch_pid"]["inputSchema"]["properties"]["poll_interval"]["default"] == 30 * 60
    assert server.poll_interval({}) == 30 * 60


def test_task_watcher_unknown_exit_is_warning_not_success():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    watcher = {"id": 1, "type": "pid", "details": {"pid": 123}, "done": False}

    server.notify_completion(watcher, UNKNOWN_EXIT_CODE)

    message = json.loads(output.getvalue())
    assert message["params"]["level"] == "warning"
    assert message["params"]["data"]["exitCode"] == UNKNOWN_EXIT_CODE
    assert "未知" in message["params"]["data"]["message"]


def test_poll_interval_and_pid_reject_bool_and_out_of_range():
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())
    for value in (True, float("inf"), MIN_WATCH_POLL_INTERVAL_SECONDS / 2, MAX_WATCH_POLL_INTERVAL_SECONDS + 1):
        with pytest.raises(ValueError):
            server.poll_interval({"poll_interval": value})
    with pytest.raises(ValueError):
        server.start_pid_watcher({"pid": True})


def test_log_path_is_confined(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_WATCHER_LOG_ROOT", str(tmp_path))
    assert confined_log_path("build.log").parent == tmp_path.resolve()
    with pytest.raises(ValueError):
        confined_log_path("../escape.log")


def test_log_marker_is_advisory_not_success():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    watcher = {"id": 1, "type": "log", "details": {}, "done": False}
    server.notify_completion(watcher, UNKNOWN_EXIT_CODE, reason="log_marker")
    message = json.loads(output.getvalue())
    assert message["params"]["level"] == "warning"
    assert message["params"]["data"]["exitCode"] == UNKNOWN_EXIT_CODE


def test_tool_arguments_and_messages_fail_closed():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "status", "arguments": {"extra": 1}}})
    response = json.loads(output.getvalue())
    assert response["error"] == {"code": -32602, "message": "Invalid parameters"}


def test_dedup_and_cancel_are_thread_safe(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_WATCHER_LOG_ROOT", str(tmp_path))
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    first = server.start_log_watcher({"path": "x.log"})
    second = server.start_log_watcher({"path": "x.log"})
    assert "Started" in first["content"][0]["text"]
    assert "already registered" in second["content"][0]["text"]
    server.cancel_watcher({"watcher_id": 1})
    assert server.watchers == {}


def test_initialize_meta_and_standard_notifications_are_accepted_without_response():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"_meta": {"progressToken": "x"}},
        }
    )
    server.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 1, "reason": "stop", "_meta": {}},
        }
    )
    assert len(output.getvalue().splitlines()) == 1


def test_tools_list_accepts_standard_null_params():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": None}
    )
    response = json.loads(output.getvalue())
    assert response["id"] == 1
    assert {item["name"] for item in response["result"]["tools"]} >= {
        "status",
        "watch_pid",
    }


def test_tools_list_accepts_standard_meta_and_null_cursor():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"cursor": None, "_meta": {"progressToken": "probe"}},
        }
    )
    response = json.loads(output.getvalue())
    assert response["id"] == 1
    assert response.get("error") is None


def test_tool_call_accepts_standard_meta_and_null_task():
    output = io.StringIO()
    server = TaskWatcherServer(io.StringIO(), output, io.StringIO())
    server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "status",
                "arguments": {},
                "_meta": {"progressToken": "probe"},
                "task": None,
            },
        }
    )
    response = json.loads(output.getvalue())
    assert response["id"] == 1
    assert response.get("error") is None


def test_log_eof_is_captured_before_thread_registration(monkeypatch, tmp_path):
    monkeypatch.setenv("TASK_WATCHER_LOG_ROOT", str(tmp_path))
    path = tmp_path / "build.log"
    path.write_text("old exit code 0\n", encoding="utf-8")
    captured = {}

    def capture_thread(*, target, args, daemon):
        captured["args"] = args
        return type("ThreadStub", (), {"start": lambda self: None})()

    monkeypatch.setattr(threading, "Thread", capture_thread)
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())
    server.start_log_watcher({"path": "build.log"})
    assert captured["args"][3] == path.stat().st_size
    assert captured["args"][2] is not None


def test_quota_counts_only_active_and_completed_history_is_bounded(monkeypatch):
    server = TaskWatcherServer(io.StringIO(), io.StringIO(), io.StringIO())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    now = time.time()
    for index in range(MAX_COMPLETED_WATCHERS + 4):
        watcher_id = index + 1
        key = ("done", watcher_id)
        server.watchers[watcher_id] = {
            "id": watcher_id,
            "type": "log",
            "details": {},
            "done": True,
            "created_at": now,
            "completed_at": now + index,
            "cancel": threading.Event(),
            "key": key,
        }
        server.dedup[key] = watcher_id
    server.next_watcher_id = MAX_COMPLETED_WATCHERS + 5
    server._start("log", ("active", 1), {}, lambda *_: None)
    assert sum(not watcher["done"] for watcher in server.watchers.values()) == 1
    assert sum(watcher["done"] for watcher in server.watchers.values()) <= MAX_COMPLETED_WATCHERS
    assert MAX_WATCHERS > 1
