#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from pathlib import Path

try:
    from mak.mcp.runtime import (
        RuntimeValidationError,
        exact_object,
        integer,
        list_request_params,
        mcp_message,
        number,
        string,
        tool_call_params,
        validate_tool_arguments,
    )
except ModuleNotFoundError:  # Support direct execution by file path.
    from runtime import (
        RuntimeValidationError,
        exact_object,
        integer,
        list_request_params,
        mcp_message,
        number,
        string,
        tool_call_params,
        validate_tool_arguments,
    )

PROTOCOL_VERSION = "2025-11-25"
COMPLETION_MESSAGE = "任务已完成，退出码为 0"
DEFAULT_WATCH_POLL_INTERVAL_SECONDS = 30 * 60
MIN_WATCH_POLL_INTERVAL_SECONDS = 0.1
MAX_WATCH_POLL_INTERVAL_SECONDS = 24 * 60 * 60
MAX_WATCHERS = 64
MAX_COMPLETED_WATCHERS = 64
WATCHER_TTL_SECONDS = 24 * 60 * 60
COMPLETED_WATCHER_TTL_SECONDS = 60 * 60
UNKNOWN_EXIT_CODE = 999
LOG_COMPLETION_MARKERS = (
    "exit code 0", "exit_code=0", "return code 0", "exited with code 0", "退出码为 0",
)
VALID_LOG_LEVELS = {"debug", "info", "notice", "warning", "error", "critical", "alert", "emergency"}


def _safe_error(_exc: BaseException | None = None) -> str:
    return "Internal MCP server error"


def log_root() -> Path:
    return Path(os.environ.get("TASK_WATCHER_LOG_ROOT", os.getcwd())).expanduser().resolve()


def confined_log_path(raw_path: str) -> Path:
    root = log_root()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError("log path is outside the configured root") from None
    return resolved


def file_identity(path: Path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino


class TaskWatcherServer:
    def __init__(self, input_stream, output_stream, error_stream):
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.error_stream = error_stream
        self.write_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.watchers: dict[int, dict] = {}
        self.dedup: dict[tuple, int] = {}
        self.next_watcher_id = 1
        self.minimum_log_level = "info"

    def run(self):
        for line in self.input_stream:
            line = line.strip()
            if not line:
                continue
            try:
                self.handle_message(json.loads(line))
            except Exception:
                print(_safe_error(), file=self.error_stream, flush=True)

    def handle_message(self, raw_message):
        request_id = raw_message.get("id") if isinstance(raw_message, dict) else None
        try:
            message = mcp_message(raw_message)
            method = message["method"]
            request_id = message.get("id")
            raw_params = message.get("params")
            params = {} if raw_params is None else raw_params
            if request_id is None:
                if method in {"notifications/initialized", "notifications/cancelled"}:
                    exact_object(params, optional={"_meta", "requestId", "reason"})
                return
            if method == "initialize":
                exact_object(
                    params,
                    optional={"protocolVersion", "capabilities", "clientInfo", "_meta"},
                )
                result = self.initialize(params)
            elif method == "tools/list":
                list_request_params(params)
                result = {"tools": self.tools()}
            elif method == "tools/call":
                result = self.call_tool(tool_call_params(params))
            elif method == "logging/setLevel":
                exact_object(params, required={"level"})
                result = self.set_log_level(params)
            else:
                self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
                return
            self.write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except (RuntimeValidationError, ValueError):
            if request_id is not None and not isinstance(request_id, bool) and isinstance(request_id, (str, int)):
                self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid parameters"}})
        except Exception as exc:
            print(_safe_error(exc), file=self.error_stream, flush=True)
            if request_id is not None:
                self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": _safe_error()}})

    def initialize(self, params):
        requested = params.get("protocolVersion")
        if requested is not None:
            string(requested, nonempty=True)
        return {
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"logging": {}, "tools": {}},
            "serverInfo": {"name": "task-watcher", "version": "0.2.0"},
            "instructions": "Watch logs or process identities without busy polling. Log completion text is advisory only.",
        }

    def tools(self):
        poll = {"type": "number", "description": "Polling interval in seconds.", "default": DEFAULT_WATCH_POLL_INTERVAL_SECONDS, "minimum": MIN_WATCH_POLL_INTERVAL_SECONDS, "maximum": MAX_WATCH_POLL_INTERVAL_SECONDS}
        return [
            {"name": "watch_log", "description": "Watch newly appended content in a confined log file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "poll_interval": poll}, "additionalProperties": False}},
            {"name": "watch_pid", "description": "Watch a positive PID and its creation identity until it exits.", "inputSchema": {"type": "object", "properties": {"pid": {"type": "integer", "minimum": 1}, "poll_interval": poll}, "required": ["pid"], "additionalProperties": False}},
            {"name": "status", "description": "Return registered watchers.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "cancel_watcher", "description": "Cancel and remove a watcher.", "inputSchema": {"type": "object", "properties": {"watcher_id": {"type": "integer", "minimum": 1}}, "required": ["watcher_id"], "additionalProperties": False}},
        ]

    def call_tool(self, params):
        name = params["name"]
        arguments = params.get("arguments", {})
        if name == "watch_log":
            validate_tool_arguments(arguments, allowed={"path", "poll_interval"})
            return self.start_log_watcher(arguments)
        if name == "watch_pid":
            validate_tool_arguments(arguments, allowed={"pid", "poll_interval"}, required={"pid"})
            return self.start_pid_watcher(arguments)
        if name == "status":
            validate_tool_arguments(arguments, allowed=set())
            return self.status()
        if name == "cancel_watcher":
            validate_tool_arguments(arguments, allowed={"watcher_id"}, required={"watcher_id"})
            return self.cancel_watcher(arguments)
        raise ValueError("unknown tool")

    def set_log_level(self, params):
        level = string(params["level"], nonempty=True)
        if level not in VALID_LOG_LEVELS:
            raise ValueError("invalid log level")
        self.minimum_log_level = level
        return {}

    def poll_interval(self, arguments):
        value = number(arguments.get("poll_interval", DEFAULT_WATCH_POLL_INTERVAL_SECONDS), positive=True)
        value = float(value)
        if not math.isfinite(value) or not MIN_WATCH_POLL_INTERVAL_SECONDS <= value <= MAX_WATCH_POLL_INTERVAL_SECONDS:
            raise ValueError("poll_interval out of range")
        return value

    def start_log_watcher(self, arguments):
        raw = arguments.get("path", "build_log.txt")
        string(raw, nonempty=True)
        path = confined_log_path(raw)
        poll_interval = self.poll_interval(arguments)
        identity = None
        offset = 0
        try:
            if path.is_file():
                stat = path.stat()
                identity = (stat.st_dev, stat.st_ino)
                offset = stat.st_size
        except OSError:
            pass
        return self._start(
            "log",
            ("log", str(path)),
            {"path": path.name, "poll_interval": poll_interval},
            self.watch_log_file,
            path,
            identity,
            offset,
            poll_interval,
        )

    def start_pid_watcher(self, arguments):
        pid = integer(arguments["pid"], positive=True)
        poll_interval = self.poll_interval(arguments)
        identity = process_create_identity(pid)
        if identity is None:
            raise ValueError("process is not available")
        return self._start("pid", ("pid", pid, identity), {"pid": pid, "poll_interval": poll_interval}, self.watch_pid, pid, identity, poll_interval)

    def _start(self, watcher_type, key, details, target, *args):
        with self.state_lock:
            self._cleanup_locked()
            existing_id = self.dedup.get(key)
            if existing_id in self.watchers:
                return self.tool_text(f"Watcher {existing_id} already registered.")
            active_count = sum(not watcher["done"] for watcher in self.watchers.values())
            if active_count >= MAX_WATCHERS:
                raise ValueError("watcher quota exceeded")
            watcher_id = self.next_watcher_id
            self.next_watcher_id += 1
            watcher = {"id": watcher_id, "type": watcher_type, "details": details, "done": False, "created_at": time.time(), "completed_at": None, "cancel": threading.Event(), "key": key}
            self.watchers[watcher_id] = watcher
            self.dedup[key] = watcher_id
        threading.Thread(target=target, args=(watcher_id, *args), daemon=True).start()
        return self.tool_text(f"Started {watcher_type} watcher {watcher_id}.")

    def _cleanup_locked(self):
        now = time.time()
        stale = [
            wid
            for wid, watcher in self.watchers.items()
            if (
                watcher["done"]
                and now - (watcher["completed_at"] or now)
                > COMPLETED_WATCHER_TTL_SECONDS
            )
            or (not watcher["done"] and now - watcher["created_at"] > WATCHER_TTL_SECONDS)
        ]
        for wid in stale:
            self._remove_locked(wid)
        completed = sorted(
            (
                watcher
                for watcher in self.watchers.values()
                if watcher["done"]
            ),
            key=lambda watcher: watcher["completed_at"] or 0,
            reverse=True,
        )
        for watcher in completed[MAX_COMPLETED_WATCHERS:]:
            self._remove_locked(watcher["id"])

    def _remove_locked(self, watcher_id):
        watcher = self.watchers.pop(watcher_id, None)
        if watcher:
            watcher["cancel"].set()
            self.dedup.pop(watcher["key"], None)

    def cancel_watcher(self, arguments):
        watcher_id = integer(arguments["watcher_id"], positive=True)
        with self.state_lock:
            if watcher_id not in self.watchers:
                raise ValueError("watcher not found")
            self._remove_locked(watcher_id)
        return self.tool_text(f"Cancelled watcher {watcher_id}.")

    def watch_log_file(self, watcher_id, path, identity, offset, poll_interval):
        initialized = identity is not None
        while True:
            watcher = self._watcher(watcher_id)
            if watcher is None or watcher["cancel"].wait(poll_interval if initialized else 0):
                return
            try:
                if not path.exists():
                    initialized = True
                    continue
                current_identity = file_identity(path)
                size = path.stat().st_size
                if identity is None:
                    identity, offset, initialized = current_identity, size, True
                    continue
                if current_identity != identity:
                    identity, offset = current_identity, 0
                elif size < offset:
                    offset = 0
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                if self.log_chunk_has_completion(chunk):
                    self.notify_completion(watcher, UNKNOWN_EXIT_CODE, reason="log_marker")
                    return
            except OSError:
                initialized = True

    def _watcher(self, watcher_id):
        with self.state_lock:
            self._cleanup_locked()
            return self.watchers.get(watcher_id)

    def log_chunk_has_completion(self, chunk):
        normalized = chunk.lower()
        return any(marker in normalized for marker in LOG_COMPLETION_MARKERS)

    def watch_pid(self, watcher_id, pid, identity, poll_interval):
        while True:
            watcher = self._watcher(watcher_id)
            if watcher is None or watcher["cancel"].wait(poll_interval):
                return
            current_identity = process_create_identity(pid)
            if current_identity != identity:
                self.notify_completion(watcher, UNKNOWN_EXIT_CODE, reason="process_identity_changed")
                return
            exit_code = process_exit_code(pid)
            if exit_code is not None:
                self.notify_completion(watcher, exit_code)
                return

    def status(self):
        with self.state_lock:
            self._cleanup_locked()
            public = [{k: v for k, v in w.items() if k not in {"cancel", "key"}} for w in self.watchers.values()]
        return self.tool_text(json.dumps(public, ensure_ascii=False))

    def tool_text(self, text):
        return {"content": [{"type": "text", "text": text}]}

    def notify_completion(self, watcher, exit_code=0, reason="process_exit"):
        with self.state_lock:
            current = self.watchers.get(watcher["id"])
            if current is not None:
                if current["done"]:
                    return
                current["done"] = True
                current["completed_at"] = time.time()
            else:
                watcher["done"] = True
                watcher["completed_at"] = time.time()
        if exit_code == 0:
            message, level = COMPLETION_MESSAGE, "notice"
        elif exit_code == UNKNOWN_EXIT_CODE:
            message, level = "任务状态未知；未确认退出码为 0", "warning"
        else:
            message, level = f"任务已完成，退出码为 {exit_code}", "warning"
        public = {k: v for k, v in watcher.items() if k not in {"cancel", "key"}}
        self.write({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": level, "logger": "task-watcher", "data": {"message": message, "exitCode": exit_code, "reason": reason, "watcher": public}}})

    def write(self, message):
        with self.write_lock:
            self.output_stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output_stream.flush()


def process_create_identity(pid):
    integer(pid, positive=True)
    if os.name == "nt":
        return windows_process_create_identity(pid)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        return ("linux", fields[19])
    except (OSError, IndexError):
        return None


def process_exit_code(pid):
    if os.name == "nt":
        return windows_process_exit_code(pid)
    try:
        os.kill(pid, 0)
        return None
    except ProcessLookupError:
        return UNKNOWN_EXIT_CODE
    except PermissionError:
        return None


def _windows_process_times(pid):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None, kernel32
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user))
    value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime if ok else None
    return (handle, value), kernel32


def windows_process_create_identity(pid):
    result, kernel32 = _windows_process_times(pid)
    if result is None:
        return None
    handle, value = result
    kernel32.CloseHandle(handle)
    return ("windows", value)


def windows_process_exit_code(pid):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return UNKNOWN_EXIT_CODE
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return UNKNOWN_EXIT_CODE
        return None if exit_code.value == 259 else int(exit_code.value)
    finally:
        kernel32.CloseHandle(handle)


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    TaskWatcherServer(sys.stdin, sys.stdout, sys.stderr).run()


if __name__ == "__main__":
    main()
