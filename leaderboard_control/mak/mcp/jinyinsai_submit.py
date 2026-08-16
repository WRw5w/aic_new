#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import csv
from contextlib import contextmanager
import http.client
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import traceback
from typing import Any
import urllib.parse
import uuid
import zipfile

from mak.aic.evidence_receipt import (
    EvidenceReceiptError,
    read_evidence_receipt,
    sidecar_path_for,
    write_evidence_receipt,
)
from mak.aic.provenance import ProvenanceError, validate_aic_provenance_manifest
from mak.submission import SUBMISSION_ARCNAME


PROTOCOL_VERSION = "2025-11-25"
SERVER_VERSION = "0.3.0"
SUBMISSION_CONTRACT_VERSION = "aicomp-submit-agent-contract/v4"
CALLER_SANDBOX_MODE = "read-only"
TEAM_ID = "AIC-2026-58579595"
TEAM_NAME = "swpu_1"
PUBLIC_LEADERBOARD_PAGE_ID = "4832828643476639839"
PUBLIC_LEADERBOARD_RW_ID = "4829238709759119407"
PUBLIC_LEADERBOARD_STAGE_ID = "4829238709759119431"
PUBLIC_LEADERBOARD_URL = (
    "https://reg.aicomp.cn/special/phb/detail"
    f"?id={PUBLIC_LEADERBOARD_PAGE_ID}"
    f"&rwId={PUBLIC_LEADERBOARD_RW_ID}"
    f"&stbh={PUBLIC_LEADERBOARD_STAGE_ID}"
)
PUBLIC_LEADERBOARD_API_URL = "https://jluat-smart-app-api.yuntu.cn/third/jsphb"
PUBLIC_LEADERBOARD_SOURCE = "aicomp_public_leaderboard_page"
LEGACY_SCORED_HISTORY_COMPATIBILITY_SCHEMA = (
    "aicomp-legacy-scored-history-compatibility/v1"
)
LEGACY_ACCEPTED_TIME_COMPATIBILITY_SCHEMA = (
    "aicomp-legacy-accepted-time-compatibility/submit-log-git-v1"
)
LEGACY_ACCEPTED_TIME_MAX_PLATFORM_DELTA_SECONDS = 60
QUEUE_PRIORITY_BASE = 1_000_000
SUBMISSION_RECORDS_MAX_DETAILS_PER_CALL = 24
SUBMISSION_RECORDS_TIMEOUT_SECONDS = 180
PUBLIC_LEADERBOARD_TIMEOUT_SECONDS = 240
RUNNER_BLOCKING_WATCH_DEFAULT_SECONDS = 180
RUNNER_BLOCKING_WATCH_MAX_SECONDS = 240
CDP_PREFLIGHT_HOST = "127.0.0.1"
CDP_PREFLIGHT_PORT = 9222
CDP_PREFLIGHT_PATH = "/json/version"
CDP_PREFLIGHT_TIMEOUT_SECONDS = 2.0
CDP_PREFLIGHT_MAX_BYTES = 64 * 1024
SUBMISSION_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
SUBMISSION_MAX_ARCHIVE_ENTRIES = 1
SUBMISSION_MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
SUBMISSION_MAX_COMPRESSION_RATIO = 100
SUBMISSION_MAX_ROWS = 100_000
SUBMISSION_MAX_FIELD_CHARS = 4096
SUBMISSION_MAX_LINE_BYTES = 8192
SUBMISSION_STREAM_CHUNK_BYTES = 64 * 1024
AICOMP_NODE_EXECUTABLE = Path(
    r"C:\Program Files\nodejs\node.exe" if os.name == "nt" else "/usr/bin/node"
)
SUBMISSION_RECORDS_NODE_EXECUTABLE = AICOMP_NODE_EXECUTABLE
SUBMISSION_RECORDS_HELPER = Path(
    r"D:\02_Projects\ML\jinyinsai\tools\aicomp_cdp.mjs"
)
AICOMP_RUNNER_HELPER_SHA256 = (
    "0e05971a4c86a7fc24646eb20e8881289afc44d252acfbc1ad1223f35652ca5a"
)
AICOMP_CDP_HELPER_SHA256 = (
    "62f44ccc2f380684d2f2413988f8f54a0652a9aec155f242eb896ac845cf3788"
)
AICOMP_MANIFEST_HELPER_SHA256 = (
    "73c726bc04e092daf479555ef51bf5047ed8dacfe4ac6467c9b6bbc793e95245"
)
SUBMISSION_RECORDS_HELPER_SHA256 = AICOMP_CDP_HELPER_SHA256
SUBMISSION_RECORDS_DEBUG_URL = "http://127.0.0.1:9222"
AICOMP_RUNNER_RUNTIME_ENV = {
    "AICOMP_REFRESH_DELAY_MINUTES": "5",
    "AICOMP_HEARTBEAT_INTERVAL_MS": str(30 * 60 * 1000),
    "AICOMP_SUBMIT_TIMEOUT_MS": str(8 * 60 * 1000),
    "AICOMP_CDP_COMMAND_TIMEOUT_MS": str(7 * 60 * 1000),
    "AICOMP_POST_UPLOAD_DELAY_MS": "45000",
    "AICOMP_POST_SUBMIT_DELAY_MS": "45000",
}
AICOMP_LEADERBOARD_RUNTIME_ENV = {
    "AICOMP_CDP_COMMAND_TIMEOUT_MS": "30000",
    "AICOMP_LEADERBOARD_RETRY_MS": "180000",
}
SUBMISSION_RECORDS_RUNTIME_ENV = {
    "AICOMP_RECORDS_MAX_PAGES": "2",
    "AICOMP_RECORDS_PAGE_WAIT_MS": "1500",
    "AICOMP_RECORDS_DETAIL_WAIT_MS": "500",
    "AICOMP_RECORDS_NAVIGATION_TIMEOUT_MS": "20000",
    "AICOMP_RECORDS_READINESS_POLL_MS": "500",
    "AICOMP_CDP_COMMAND_TIMEOUT_MS": "30000",
    "AICOMP_RECORDS_RESET_AFTER_EMPTY_SEARCH": "0",
}
RUNNER_WATCH_POLL_INTERVAL_SECONDS = 30 * 60
BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))
PER_SUBMISSION_SOURCE_UNAVAILABLE = "PER_SUBMISSION_SOURCE_UNAVAILABLE"
PER_SUBMISSION_FIELDS_INSUFFICIENT = "PER_SUBMISSION_FIELDS_INSUFFICIENT"
SUBMISSION_RECORDS_COLLECTION_TRUNCATED = (
    "SUBMISSION_RECORDS_COLLECTION_TRUNCATED"
)
SUBMISSION_RECORDS_COLLECTION_COMPLETENESS_UNKNOWN = (
    "SUBMISSION_RECORDS_COLLECTION_COMPLETENESS_UNKNOWN"
)
COMPETITION_SCOPE = "jinyinsai:aicomp-2026"
QUEUE_SCHEMA_VERSION = 3
BLOCKING_QUEUE_STATUSES = {
    "uploading",
    "submit_not_dispatched",
    "outcome_unknown",
    "accepted",
    "awaiting_score",
    "score_missed",
}


def probe_cdp_endpoint() -> dict:
    """Check the fixed local Chrome debug endpoint without following redirects."""

    connection = http.client.HTTPConnection(
        CDP_PREFLIGHT_HOST,
        CDP_PREFLIGHT_PORT,
        timeout=CDP_PREFLIGHT_TIMEOUT_SECONDS,
    )
    try:
        connection.request(
            "GET",
            CDP_PREFLIGHT_PATH,
            headers={"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        if response.status != 200:
            response.read(CDP_PREFLIGHT_MAX_BYTES + 1)
            return {
                "ready": False,
                "reason": "CDP_HTTP_STATUS_UNEXPECTED",
                "httpStatus": int(response.status),
            }
        raw = response.read(CDP_PREFLIGHT_MAX_BYTES + 1)
        if len(raw) > CDP_PREFLIGHT_MAX_BYTES:
            return {"ready": False, "reason": "CDP_RESPONSE_TOO_LARGE"}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"ready": False, "reason": "CDP_RESPONSE_INVALID"}
        websocket = payload.get("webSocketDebuggerUrl") if isinstance(payload, dict) else None
        parsed = urllib.parse.urlsplit(websocket) if isinstance(websocket, str) else None
        if (
            parsed is None
            or parsed.scheme not in {"ws", "wss"}
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != CDP_PREFLIGHT_PORT
            or not parsed.path.startswith("/devtools/browser/")
        ):
            return {"ready": False, "reason": "CDP_BROWSER_TARGET_INVALID"}
        return {
            "ready": True,
            "reason": "CDP_BROWSER_TARGET_READY",
            "endpoint": f"http://{CDP_PREFLIGHT_HOST}:{CDP_PREFLIGHT_PORT}",
        }
    except (OSError, TimeoutError, http.client.HTTPException):
        return {"ready": False, "reason": "CDP_ENDPOINT_UNAVAILABLE"}
    finally:
        connection.close()
RESULT_FIELDS = [
    "query_time",
    "file_index",
    "file_name",
    "file_path",
    "submitted_at",
    "accepted_at",
    "leaderboard_publish_time",
    "team_rank",
    "team_id",
    "team_name",
    "team_submit_time",
    "score",
    "score_time",
    "snapshot_path",
]
SCORE_FINALIZE_ACTION = "finalize_submission_record_score"
PUBLIC_LEADERBOARD_RECOVERY_ACTION = "fetch_public_leaderboard_late_publication"
PUBLIC_LEADERBOARD_FINALIZE_ACTION = "finalize_public_leaderboard_late_publication_score"
SCORE_FINALIZATION_RECEIPT_SCHEMA_VERSION = 3
SCORE_FINALIZATION_PHASES = (
    "prepared",
    "result_applied",
    "queue_applied",
    "active_clear_prepared",
    "active_cleared",
    "event_applied",
    "committed",
)
SCORE_FINALIZATION_RECEIPT_KEYS = {
    "schemaVersion",
    "state",
    "phase",
    "preparedAt",
    "updatedAt",
    "committedAt",
    "finalizationKey",
    "intent",
    "expectedRevisions",
    "previousReceiptDigest",
}


def default_root() -> Path:
    for value in (
        os.environ.get("JINYINSAI_ROOT"),
        os.environ.get("AICOMP_MCP_ROOT"),
    ):
        if value:
            return Path(value).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "tools" / "aicomp_submit_queue.mjs").exists():
        return cwd
    project_root = Path(__file__).resolve().parents[2]
    sibling = project_root.parent.parent / "jinyinsai"
    if (sibling / "tools" / "aicomp_submit_queue.mjs").exists():
        return sibling.resolve()
    return project_root


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def tool_text(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def tool_json(payload: Any) -> dict:
    return tool_text(json.dumps(payload, ensure_ascii=False, indent=2))


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def canonical_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return str(Path(text).expanduser().resolve()).casefold()


def content_id(candidate_sha256: str) -> str:
    return f"sha256:{candidate_sha256.strip().lower()}"


def logical_submission_id(candidate_sha256: str) -> str:
    return f"{COMPETITION_SCOPE}:{TEAM_ID}:{candidate_sha256.strip().lower()}:r0"


def infer_family(name: str) -> str:
    stem = Path(name).stem.lower()
    if stem.startswith("pred_results_"):
        stem = stem[len("pred_results_") :]
    for suffix in ("_balanced", "_tta"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem.endswith("_balanced"):
        stem = stem[: -len("_balanced")]
    return stem


def infer_variant(name: str) -> str:
    return "balanced" if "balanced" in name.lower() else "raw"


def infer_tier(name: str) -> str:
    lower = name.lower()
    if any(token in lower for token in ("clmixsoup", "cleanlabsoup", "cb05")):
        return "critical"
    if "balanced" in lower:
        return "normal"
    return "waste"


def status_counts(queue: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in queue:
        status = item.get("status", "queued")
        counts[status] = counts.get(status, 0) + 1
    return counts


def queued_sort_key(item: dict) -> tuple[int, str, int]:
    return (-int(item.get("priority") or 0), str(item.get("createdAt") or ""), int(item.get("index") or 0))


def find_item(queue: list[dict], selector: str) -> dict | None:
    wanted = str(selector)
    for item in queue:
        path = Path(str(item.get("path") or ""))
        if (
            str(item.get("index")) == wanted
            or item.get("name") == wanted
            or str(path) == wanted
            or path.name == wanted
        ):
            return item
    return None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pid_is_running(pid: Any) -> bool:
    pid_value = safe_int(pid)
    if not pid_value or pid_value <= 0:
        return False
    if os.name == "nt":
        return windows_pid_is_running(pid_value)
    try:
        os.kill(pid_value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def windows_pid_is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def windows_process_creation_filetime(pid: int) -> int | None:
    """Return the creation FILETIME so a reused PID cannot impersonate a runner."""

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def linux_process_start_ticks(pid: int) -> int | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    close_paren = stat_text.rfind(")")
    if close_paren < 0:
        return None
    fields_after_comm = stat_text[close_paren + 2 :].split()
    try:
        # /proc/<pid>/stat field 22; fields_after_comm starts at field 3.
        return int(fields_after_comm[19])
    except (IndexError, TypeError, ValueError):
        return None


def process_start_identity(pid: Any) -> str | None:
    pid_value = safe_int(pid)
    if not pid_value or pid_value <= 0 or not pid_is_running(pid_value):
        return None
    if os.name == "nt":
        creation = windows_process_creation_filetime(pid_value)
        return f"windows-filetime:{creation}" if creation is not None else None
    start_ticks = linux_process_start_ticks(pid_value)
    if start_ticks is None:
        return None
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        boot_id = "unknown-boot"
    return f"linux-proc-start:{boot_id}:{start_ticks}"


def process_start_epoch(pid: Any) -> float | None:
    pid_value = safe_int(pid)
    if not pid_value or pid_value <= 0 or not pid_is_running(pid_value):
        return None
    if os.name == "nt":
        creation = windows_process_creation_filetime(pid_value)
        if creation is None:
            return None
        # Windows FILETIME is 100 ns ticks since 1601-01-01 UTC.
        return creation / 10_000_000 - 11_644_473_600
    start_ticks = linux_process_start_ticks(pid_value)
    if start_ticks is None:
        return None
    try:
        clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        boot_line = next(
            line
            for line in Path("/proc/stat").read_text(encoding="ascii").splitlines()
            if line.startswith("btime ")
        )
        boot_epoch = int(boot_line.split()[1])
    except (OSError, StopIteration, TypeError, ValueError):
        return None
    return boot_epoch + start_ticks / clock_ticks


def runtime_timestamp_epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC).timestamp()


def runner_process_liveness(runner: dict) -> tuple[bool, str]:
    """Bind persisted liveness to a process instance, not merely its reusable PID."""

    pid = runner.get("pid")
    if not pid_is_running(pid):
        return False, "not_running"
    expected_identity = str(runner.get("processIdentity") or "").strip()
    if expected_identity:
        actual_identity = process_start_identity(pid)
        if actual_identity is None:
            # Identity lookup failure is not permission to launch a duplicate.
            return True, "identity_unavailable"
        if actual_identity == expected_identity:
            return True, "identity_matched"
        return False, "pid_reused"

    # Legacy records predate processIdentity. Their startedAt is the child launch
    # time, so a large delta proves that the PID now belongs to another process.
    actual_start = process_start_epoch(pid)
    recorded_start = runtime_timestamp_epoch(runner.get("startedAt"))
    if actual_start is None or recorded_start is None:
        return True, "legacy_identity_unavailable"
    if abs(actual_start - recorded_start) <= 300:
        return True, "legacy_start_time_matched"
    return False, "legacy_pid_reused"


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def parse_team_submit_datetime(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=BEIJING_TZ).astimezone(dt.UTC)


def parse_leaderboard_publish_datetime(value: Any, team_submit_time: Any) -> dt.datetime | None:
    text = compact_text(value)
    team_submit_at = parse_team_submit_datetime(team_submit_time)
    if not text or team_submit_at is None:
        return None
    match = re.fullmatch(r"(\d{2})月(\d{2})日 (\d{2})时(\d{2})分", text)
    if not match:
        return None
    month, day, hour, minute = (int(part) for part in match.groups())
    team_local = team_submit_at.astimezone(BEIJING_TZ)
    try:
        published_local = dt.datetime(
            team_local.year,
            month,
            day,
            hour,
            minute,
            tzinfo=BEIJING_TZ,
        )
    except ValueError:
        return None
    if published_local < team_local - dt.timedelta(days=180):
        try:
            published_local = published_local.replace(year=published_local.year + 1)
        except ValueError:
            return None
    elif published_local > team_local + dt.timedelta(days=180):
        try:
            published_local = published_local.replace(year=published_local.year - 1)
        except ValueError:
            return None
    return published_local.astimezone(dt.UTC)


def is_exact_public_leaderboard_url(value: Any) -> bool:
    text = compact_text(value)
    if not text:
        return False
    parsed = urllib.parse.urlparse(text)
    expected = urllib.parse.urlparse(PUBLIC_LEADERBOARD_URL)
    if (
        parsed.scheme.lower() != expected.scheme
        or parsed.netloc.lower() != expected.netloc
        or parsed.path.rstrip("/") != expected.path.rstrip("/")
        or parsed.fragment
    ):
        return False
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    return query == {
        "id": [PUBLIC_LEADERBOARD_PAGE_ID],
        "rwId": [PUBLIC_LEADERBOARD_RW_ID],
        "stbh": [PUBLIC_LEADERBOARD_STAGE_ID],
    }


def safe_snapshot_name(value: Any) -> str:
    name = str(value or "no_active")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(name).name)[:120] or "snapshot"


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = compact_text(value)
        if text:
            return text
    return ""


def score_as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = compact_text(value)
    if not text or text in {"-", "--", "未打分", "暂无", "无"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def filename_from_url(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text)
    candidate = urllib.parse.unquote(Path(parsed.path or text).name)
    return candidate if candidate and "." in candidate else ""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_submission_datetime(value: Any) -> dt.datetime | None:
    text = compact_text(value)
    has_explicit_timezone = bool(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text, flags=re.IGNORECASE))
    if has_explicit_timezone:
        return parse_iso_datetime(text)
    try:
        parsed_naive = dt.datetime.fromisoformat(text)
    except ValueError:
        parsed_naive = None
    if parsed_naive is not None:
        if parsed_naive.tzinfo is not None:
            return parsed_naive.astimezone(dt.UTC)
        return parsed_naive.replace(tzinfo=BEIJING_TZ).astimezone(dt.UTC)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=BEIJING_TZ).astimezone(dt.UTC)
        except ValueError:
            continue
    return None


def public_leaderboard_source(value: Any) -> bool:
    text = compact_text(value).lower()
    return "leaderboard" in text or "public leaderboard" in text


def submission_records_collection_state(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return "unknown"
    collection = diagnostics.get("collection")
    if not isinstance(collection, dict):
        return "unknown"
    truncated = collection.get("truncated")
    if truncated is False:
        return "complete"
    if truncated is True:
        return "truncated"
    return "unknown"


def dict_first(record: dict, *keys: str) -> Any:
    for key in keys:
        if key in record and record.get(key) not in (None, ""):
            return record.get(key)
    return ""


def normalized_key(key: Any) -> str:
    text = re.sub(r"[\s_\-/:：()（）]+", "", str(key or "").lower())
    if text in {"id", "记录id", "作品id", "workid", "recordid", "businessid", "rowid"}:
        return "record_id"
    if "队列" in text and "index" in text or text == "queueindex":
        return "queue_index"
    if any(token in text for token in ("提交时间", "创建时间", "createdat", "createtime", "submittime", "submittedat", "acceptedat")):
        return "submit_time"
    if any(token in text for token in ("打分时间", "评分时间", "scoretime", "scoredat")):
        return "score_time"
    if any(token in text for token in ("附件", "文件名", "作品文件", "提交文件", "filename", "attachmentfilename")):
        return "attachment_filename"
    if any(token in text for token in ("url", "链接", "地址", "attachmenturl")):
        return "attachment_url"
    if any(token in text for token in ("hash", "sha256", "md5")):
        return "attachment_hash"
    if any(token in text for token in ("状态", "status", "打分结果", "评分结果", "作品提交")) and "分数" not in text:
        return "status"
    if any(token in text for token in ("失败原因", "驳回原因", "错误原因", "failurereason", "reason")):
        return "failure_reason"
    if any(token in text for token in ("分数", "成绩", "score")):
        return "score"
    if any(token in text for token in ("参赛编号", "teamid", "团队编号")):
        return "team_id"
    if any(token in text for token in ("团队名称", "teamname")):
        return "team_name"
    if any(token in text for token in ("竞赛名称", "competitionname")):
        return "competition_name"
    if any(token in text for token in ("竞赛id", "competitionid")):
        return "competition_id"
    return str(key or "")


def flatten_record_fields(record: dict) -> dict:
    flattened: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, dict) and key in {"attachment", "file", "work", "submission", "raw"}:
            for child_key, child_value in value.items():
                flattened.setdefault(normalized_key(child_key), child_value)
        flattened.setdefault(normalized_key(key), value)
    return flattened


def parse_snapshot_output(output: str) -> dict | None:
    start = output.find("{")
    end = output.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_team_result(snapshot: dict | None) -> dict:
    text = re.sub(r"\s+", " ", str((snapshot or {}).get("text") or "")).strip()
    publish = ""
    match = re.search(r"发布时间：(\d{2}月\d{2}日 \d{2}时\d{2}分)", text)
    if match:
        publish = match.group(1)
    row_re = re.compile(
        r"(?:(\d+)\s+)?(AIC-2026-\d+)\s+(.+?)\s+"
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
        r"([0-9.]+)\s+"
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    )
    for i, row in enumerate(row_re.finditer(text), start=1):
        rank, team_id, team_name, submit_time, score, score_time = row.groups()
        if team_id == TEAM_ID or team_name.strip() == TEAM_NAME:
            return {
                "leaderboardPublishTime": publish,
                "rank": rank or str(i),
                "teamId": team_id,
                "teamName": team_name.strip(),
                "teamSubmitTime": submit_time,
                "score": score,
                "scoreTime": score_time,
            }
    return {"leaderboardPublishTime": publish}


class JinyinsaiSubmitServer:
    def __init__(
        self,
        input_stream,
        output_stream,
        error_stream,
        root: Path | str | None = None,
        cdp_probe=None,
        authorized_artifact_roots: tuple[Path | str, ...] | list[Path | str] | None = None,
    ):
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.error_stream = error_stream
        self.write_lock = threading.Lock()
        self.runner_lock = threading.Lock()
        self.runner_state_lock = threading.RLock()
        self.runners: dict[str, dict] = {}
        self.next_runner_id = 1
        raw_server_root = (
            Path(root).expanduser().absolute() if root is not None else default_root()
        )
        configured_roots = (
            (raw_server_root,)
            if authorized_artifact_roots is None
            else authorized_artifact_roots
        )
        if not configured_roots:
            raise ValueError("AUTHORIZED_ARTIFACT_ROOTS_REQUIRED")
        resolved_roots: list[Path] = []
        for value in configured_roots:
            raw_root = Path(value).expanduser().absolute()
            try:
                root_stat = os.lstat(raw_root)
            except OSError as exc:
                raise ValueError(
                    f"AUTHORIZED_ARTIFACT_ROOT_UNAVAILABLE: {raw_root}"
                ) from exc
            if self._is_link_or_reparse(root_stat):
                raise ValueError(
                    f"AUTHORIZED_ARTIFACT_ROOT_REPARSE_POINT_FORBIDDEN: {raw_root}"
                )
            if not stat.S_ISDIR(root_stat.st_mode):
                raise ValueError(
                    f"AUTHORIZED_ARTIFACT_ROOT_NOT_DIRECTORY: {raw_root}"
                )
            resolved_roots.append(raw_root.resolve(strict=True))
        self.authorized_artifact_roots = tuple(resolved_roots)
        self.root = raw_server_root.resolve(strict=True)
        self.cdp_probe = cdp_probe or probe_cdp_endpoint
        self.submissions = self.root / "submissions"
        self.tools_dir = self.root / "tools"
        self.queue_path = self.submissions / "aicomp_submit_queue.json"
        self.active_path = self.submissions / "aicomp_active_submission.json"
        self.state_path = self.submissions / "aicomp_state.md"
        self.results_path = self.submissions / "aicomp_results.csv"
        self.events_path = self.submissions / "aicomp_events.jsonl"
        self.snapshot_log_path = self.submissions / "aicomp_leaderboard_snapshots.log"
        self.snapshot_dir = self.submissions / "leaderboard_snapshots"
        self.legacy_scored_history_compatibility_dir = (
            self.submissions / "legacy_scored_history_compatibility"
        )
        self.legacy_scored_history_compatibility_manifest_path = (
            self.legacy_scored_history_compatibility_dir / "manifest.json"
        )
        self.legacy_accepted_time_compatibility_dir = (
            self.submissions / "legacy_accepted_time_compatibility"
        )
        self.legacy_accepted_time_compatibility_manifest_path = (
            self.legacy_accepted_time_compatibility_dir / "manifest.json"
        )
        self.submission_records_dir = self.submissions / "submission_record_evidence"
        self.score_capture_claims_dir = self.submissions / "score_capture_claims"
        self.public_leaderboard_recovery_claims_dir = (
            self.submissions / "public_leaderboard_recovery_claims"
        )
        self.public_leaderboard_recovery_consumptions_dir = (
            self.submissions / "public_leaderboard_recovery_consumptions"
        )
        self.score_finalizations_dir = self.submissions / "score_finalizations"
        self.runner_state_path = self.submissions / "aicomp_mcp_runners.json"
        self.control_lock_path = self.submissions / "aicomp_control.lock"
        self.runner_lease_path = self.submissions / "aicomp_runner.lock"
        self.runner_registry_lock_path = (
            self.submissions / "aicomp_runner_registry.lock"
        )
        self.reconciliation_backup_dir = self.submissions / "reconciliation_backups"

    def lock_owner_path(self, lock_path: Path) -> Path:
        return lock_path / "owner.json"

    def read_lock_owner(self, lock_path: Path) -> dict | None:
        owner_path = self.lock_owner_path(lock_path)
        for attempt in range(5):
            try:
                owner = read_json(owner_path, None)
                return owner if isinstance(owner, dict) else None
            except (OSError, json.JSONDecodeError):
                # Windows may briefly deny a reader while another process
                # atomically replaces owner.json.  Treat a persistent failure
                # as unknown (and therefore busy), never as permission to
                # steal or remove the lock.
                if attempt == 4:
                    return None
                time.sleep(0.01)
        return None

    def remove_lock_directory(self, lock_path: Path, *, token: str | None = None) -> bool:
        if not lock_path.exists():
            return True
        owner = self.read_lock_owner(lock_path)
        if token is not None and (not owner or owner.get("token") != token):
            return False
        allowed = {"owner.json"}
        for child in lock_path.iterdir():
            if child.name in allowed or child.name.startswith("owner.json.") and child.name.endswith(".tmp"):
                child.unlink(missing_ok=True)
                continue
            raise ValueError(f"LOCK_DIRECTORY_CONTAINS_UNEXPECTED_FILE: {child}")
        lock_path.rmdir()
        return True

    @contextmanager
    def control_lock(self):
        self.submissions.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        for _attempt in range(2):
            try:
                self.control_lock_path.mkdir()
                try:
                    write_json(
                        self.lock_owner_path(self.control_lock_path),
                        {"token": token, "pid": os.getpid(), "acquiredAt": now_iso()},
                    )
                except Exception:
                    persisted = self.read_lock_owner(self.control_lock_path)
                    if persisted is None:
                        self.remove_lock_directory(self.control_lock_path)
                    elif persisted.get("token") == token:
                        self.remove_lock_directory(
                            self.control_lock_path,
                            token=token,
                        )
                    raise
                break
            except FileExistsError:
                owner = self.read_lock_owner(self.control_lock_path)
                if owner and not pid_is_running(owner.get("pid")):
                    self.remove_lock_directory(self.control_lock_path, token=str(owner.get("token") or ""))
                    continue
                raise ValueError("SUBMISSION_CONTROL_BUSY")
        else:
            raise ValueError("SUBMISSION_CONTROL_BUSY")
        try:
            yield
        finally:
            self.remove_lock_directory(self.control_lock_path, token=token)

    @contextmanager
    def runner_registry_lock(self, timeout_seconds: float = 10.0):
        """Serialize runner-registry read/modify/write across MCP processes."""

        self.submissions.mkdir(parents=True, exist_ok=True)
        token = str(uuid.uuid4())
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                self.runner_registry_lock_path.mkdir()
                try:
                    write_json(
                        self.lock_owner_path(self.runner_registry_lock_path),
                        {"token": token, "pid": os.getpid(), "acquiredAt": now_iso()},
                    )
                except Exception:
                    persisted = self.read_lock_owner(self.runner_registry_lock_path)
                    if persisted is None:
                        self.remove_lock_directory(self.runner_registry_lock_path)
                    elif persisted.get("token") == token:
                        self.remove_lock_directory(
                            self.runner_registry_lock_path,
                            token=token,
                        )
                    raise
                break
            except FileExistsError:
                owner = self.read_lock_owner(self.runner_registry_lock_path)
                if owner and not pid_is_running(owner.get("pid")):
                    self.remove_lock_directory(
                        self.runner_registry_lock_path,
                        token=str(owner.get("token") or ""),
                    )
                    continue
                if time.monotonic() >= deadline:
                    raise ValueError("RUNNER_REGISTRY_BUSY")
                time.sleep(0.01)
        try:
            yield
        finally:
            self.remove_lock_directory(
                self.runner_registry_lock_path,
                token=token,
            )

    def runner_lease_owner(self) -> dict | None:
        if not self.runner_lease_path.exists():
            return None
        return self.read_lock_owner(self.runner_lease_path)

    def assert_queue_mutation_allowed(self) -> None:
        if not self.runner_lease_path.exists():
            return
        owner = self.runner_lease_owner()
        if owner and pid_is_running(owner.get("pid")):
            raise ValueError("RUNNER_LEASE_ACTIVE: queue mutation is blocked while a runner owns the ledger")
        raise ValueError("RUNNER_LEASE_STALE_RECONCILIATION_REQUIRED")

    def reserve_runner_lease(self, intent: dict) -> tuple[str | None, dict | None]:
        if self.runner_lease_path.exists():
            owner = self.runner_lease_owner()
            if owner and pid_is_running(owner.get("pid")):
                return None, owner
            if not owner:
                raise ValueError("RUNNER_START_RECONCILIATION_REQUIRED: lease owner is missing")
            if self.stale_submit_lease_releasable_for_capture(owner, intent):
                self.release_runner_lease(str(owner.get("token") or ""))
            else:
                raise ValueError(
                    "RUNNER_START_RECONCILIATION_REQUIRED: stale launching/running lease must be reconciled"
                )
        token = str(uuid.uuid4())
        self.runner_lease_path.mkdir()
        owner = {
            "schemaVersion": 1,
            "token": token,
            "pid": os.getpid(),
            "phase": "launching",
            "intent": intent,
            "acquiredAt": now_iso(),
        }
        try:
            write_json(self.lock_owner_path(self.runner_lease_path), owner)
        except Exception:
            persisted = self.runner_lease_owner()
            if persisted is None:
                self.remove_lock_directory(self.runner_lease_path)
            elif persisted.get("token") == token:
                self.remove_lock_directory(self.runner_lease_path, token=token)
            raise
        return token, owner

    def stale_submit_lease_releasable_for_capture(self, owner: dict, intent: dict) -> bool:
        if intent.get("action") != "capture_score":
            return False
        if not owner or pid_is_running(owner.get("pid")):
            return False
        owner_intent = owner.get("intent")
        if not isinstance(owner_intent, dict) or owner_intent.get("action") != "submit_candidate":
            return False
        if str(owner_intent.get("queueIndex") or "") != str(intent.get("queueIndex") or ""):
            return False
        if str(owner_intent.get("candidateSha256") or "").lower() != str(intent.get("candidateSha256") or "").lower():
            return False
        if str(owner_intent.get("logicalSubmissionId") or "") != str(intent.get("logicalSubmissionId") or ""):
            return False

        active = read_json(self.active_path, None)
        if not isinstance(active, dict) or active.get("status") != "awaiting_score":
            return False
        if str(active.get("queue_index") or "") != str(intent.get("queueIndex") or ""):
            return False
        if str(active.get("accepted_at") or "") != str(intent.get("acceptedAt") or ""):
            return False
        if str(active.get("candidate_sha256") or "").lower() != str(intent.get("candidateSha256") or "").lower():
            return False
        if str(active.get("logical_submission_id") or "") != str(intent.get("logicalSubmissionId") or ""):
            return False

        runner_id = str(owner.get("runner_id") or owner_intent.get("runnerId") or "")
        attempt_id = str(owner_intent.get("attemptId") or "")
        if not runner_id:
            return False
        for runner in self.runner_statuses():
            if str(runner.get("runner_id") or "") != runner_id:
                continue
            if runner.get("running"):
                return False
            if attempt_id:
                persisted = self.read_runner_doc().get("runners", [])
                matching = [
                    row
                    for row in persisted
                    if str(row.get("runner_id") or "") == runner_id
                    and str(row.get("attemptId") or "") == attempt_id
                ]
                if not matching:
                    return False
            return True
        return False

    def update_runner_lease(self, token: str, **updates: Any) -> None:
        owner = self.runner_lease_owner()
        if not owner or owner.get("token") != token:
            return
        owner.update(updates)
        write_json(self.lock_owner_path(self.runner_lease_path), owner)

    def release_runner_lease(self, token: str) -> None:
        self.remove_lock_directory(self.runner_lease_path, token=token)

    def run(self) -> None:
        for line in self.input_stream:
            line = line.strip()
            if not line:
                continue
            try:
                self.handle_message(json.loads(line))
            except Exception:
                print(traceback.format_exc(), file=self.error_stream, flush=True)

    def handle_message(self, message: dict) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            return
        try:
            if method == "initialize":
                result = self.initialize(params)
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                result = self.call_tool(params)
            else:
                self.write(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
                return
            self.write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except ValueError as exc:
            self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})
        except Exception as exc:
            print(traceback.format_exc(), file=self.error_stream, flush=True)
            self.write({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}})

    def initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"logging": {}, "tools": {}},
            "serverInfo": {"name": "jinyinsai-submit", "version": SERVER_VERSION},
            "instructions": (
                f"Caller contract: {SUBMISSION_CONTRACT_VERSION}. "
                "Use this server for jinyinsai AICOMP submission queue operations. "
                "Validate and queue files first; echo queue_status.runnerStartIntent exactly into queue_runner_start. "
                "Only submit_candidate with confirm_real_submit=true may submit externally. "
                f"Public score capture is bound to {PUBLIC_LEADERBOARD_URL}; never use a team/work page as the public leaderboard. "
                "Use background watchers or heartbeat automation for leaderboard monitoring instead of repeated polling. "
                f"Calling agents must run with a {CALLER_SANDBOX_MODE} sandbox; they are operators, not maintainers. "
                "On a capability gap, stop and return "
                "MCP_CHANGE_REQUEST instead of modifying control-plane code, queues, locks, claims, or ledgers."
            ),
        }

    def tools(self) -> list[dict]:
        files_schema = {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"}},
                "provenance_manifests": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["files", "provenance_manifests"],
            "additionalProperties": False,
        }
        items_schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"type": "string"}}},
            "required": ["items"],
            "additionalProperties": False,
        }
        return [
            {
                "name": "validate_submission_file",
                "description": "Validate that a jinyinsai ZIP contains exactly pred_results.csv before queueing.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"file": {"type": "string"}},
                    "required": ["file"],
                    "additionalProperties": False,
                },
            },
            {"name": "queue_status", "description": "Return caller contract/schema versions plus AICOMP queue, active lock, runner, and score state.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "queue_push_front", "description": "Push one or more submission files to the front of the deque. Batch order is preserved.", "inputSchema": files_schema},
            {"name": "queue_push_back", "description": "Push one or more submission files to the back of the deque.", "inputSchema": files_schema},
            {"name": "queue_move_front", "description": "Move existing queued items to the front of the deque.", "inputSchema": items_schema},
            {"name": "queue_move_back", "description": "Move existing queued items to the back of the deque.", "inputSchema": items_schema},
            {
                "name": "queue_remove",
                "description": "Remove queued items from the deque by retaining a terminal dropped identity tombstone. Active or scored items cannot be removed, and removed artifacts cannot be replayed.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"items": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
                    "required": ["items", "reason"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_runner_start",
                "description": "Start exactly one intent-bound submit or capture runner. Echo the latest runnerStartIntent; only submit_candidate may submit externally.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "confirm_real_submit": {"type": "boolean"},
                        "intent": {
                            "type": "object",
                            "description": "Exact runnerStartIntent returned by the latest queue_status call.",
                        },
                    },
                    "required": ["confirm_real_submit", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_reconcile_not_dispatched",
                "description": (
                    "Reconcile one exact upload attempt whose bounded runner evidence proves that no submit "
                    "button click was dispatched. This preserves the same queue item and identity, and only "
                    "returns it to queued; a fresh queue_status and submit intent are still required."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "confirm_retry_same_identity": {"type": "boolean"},
                        "intent": {
                            "type": "object",
                            "description": "Exact reconciliationIntent returned by the latest queue_status call.",
                        },
                    },
                    "required": ["confirm_retry_same_identity", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_runner_watch",
                "description": (
                    "Watch a started queue runner. A process-level one-shot operator must use "
                    "wait_for_completion=true so the MCP host keeps ownership of its child runner "
                    "until a terminal exit; the default notification mode remains non-blocking."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "runner_id": {"type": "string"},
                        "wait_for_completion": {"type": "boolean"},
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": RUNNER_BLOCKING_WATCH_MAX_SECONDS,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "aicomp_submission_records_fetch",
                "description": "Read AICOMP per-submission work scoring records from the logged-in Chrome/CDP session. Does not substitute the public leaderboard for per-submission records.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "queue_index": {"type": "integer"},
                        "selector": {"type": "string"},
                        "file": {"type": "string"},
                        "submitted_at": {"type": "string"},
                        "accepted_at": {"type": "string"},
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": SUBMISSION_RECORDS_TIMEOUT_SECONDS,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "aicomp_public_leaderboard_fetch",
                "description": (
                    "Consume or reconcile the one durable late-publication recovery claim for the exact "
                    "score_missed item, and read only the canonical AICOMP public leaderboard page. Echo "
                    "queue_status.publicLeaderboardRecoveryIntent exactly; this never submits a candidate."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "confirm_public_leaderboard_read": {"type": "boolean"},
                        "intent": {
                            "type": "object",
                            "description": "Exact publicLeaderboardRecoveryIntent returned by queue_status.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": PUBLIC_LEADERBOARD_TIMEOUT_SECONDS,
                        },
                    },
                    "required": ["confirm_public_leaderboard_read", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_finalize_submission_record_score",
                "description": (
                    "Finalize one score_missed item from an exact, evidence-hash-bound scoreFinalizeIntent "
                    "returned by aicomp_submission_records_fetch. This never captures the public leaderboard "
                    "and never reuses or deletes the consumed score capture claim."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "confirm_score_attribution": {"type": "boolean"},
                        "intent": {
                            "type": "object",
                            "description": "Exact scoreFinalizeIntent returned by aicomp_submission_records_fetch.",
                        },
                    },
                    "required": ["confirm_score_attribution", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_finalize_public_leaderboard_score",
                "description": (
                    "Finalize one score_missed item from the exact evidence-hash-bound public "
                    "leaderboard finalize intent returned by aicomp_public_leaderboard_fetch. The "
                    "original failed capture claim and the separate late-recovery claim are preserved."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "confirm_score_attribution": {"type": "boolean"},
                        "intent": {
                            "type": "object",
                            "description": "Exact scoreFinalizeIntent returned by aicomp_public_leaderboard_fetch.",
                        },
                    },
                    "required": ["confirm_score_attribution", "intent"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "queue_skip_score_wait",
                "description": "Manually skip the exact active score wait under the control lock. Selector, when supplied, must be its queue index or logical submission ID; an explicit reason is required.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"selector": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        ]

    def call_tool(self, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "validate_submission_file":
            return tool_json(self.validate_submission_file(arguments))
        if name == "queue_status":
            return tool_json(self.queue_status())
        if name == "queue_push_front":
            return tool_json(self.queue_push(arguments, front=True))
        if name == "queue_push_back":
            return tool_json(self.queue_push(arguments, front=False))
        if name == "queue_move_front":
            return tool_json(self.queue_move(arguments, front=True))
        if name == "queue_move_back":
            return tool_json(self.queue_move(arguments, front=False))
        if name == "queue_remove":
            return tool_json(self.queue_remove(arguments))
        if name == "queue_runner_start":
            return tool_json(self.queue_runner_start(arguments))
        if name == "queue_reconcile_not_dispatched":
            return tool_json(self.queue_reconcile_not_dispatched(arguments))
        if name == "queue_runner_watch":
            return tool_json(self.queue_runner_watch(arguments))
        if name == "aicomp_submission_records_fetch":
            return tool_json(self.aicomp_submission_records_fetch(arguments))
        if name == "aicomp_public_leaderboard_fetch":
            return tool_json(self.aicomp_public_leaderboard_fetch(arguments))
        if name == "queue_finalize_submission_record_score":
            return tool_json(self.queue_finalize_submission_record_score(arguments))
        if name == "queue_finalize_public_leaderboard_score":
            return tool_json(self.queue_finalize_public_leaderboard_score(arguments))
        if name == "queue_skip_score_wait":
            return tool_json(self.queue_skip_score_wait(arguments))
        raise ValueError(f"Unknown tool: {name}")

    def read_queue_doc(self) -> dict:
        doc = read_json(
            self.queue_path,
            {"updatedAt": now_iso(), "schemaVersion": QUEUE_SCHEMA_VERSION, "refreshDelayMinutes": 5, "queue": []},
        )
        if not isinstance(doc, dict):
            raise ValueError("queue document must be an object")
        queue = doc.get("queue")
        if not isinstance(queue, list):
            doc["queue"] = []
        return doc

    def save_queue_doc(self, doc: dict, *, updated_at: str | None = None) -> None:
        doc["updatedAt"] = updated_at or now_iso()
        doc["schemaVersion"] = QUEUE_SCHEMA_VERSION
        write_json(self.queue_path, doc)

    def queued_items(self, queue: list[dict]) -> list[dict]:
        return sorted([item for item in queue if item.get("status", "queued") == "queued"], key=queued_sort_key)

    def item_has_result(self, item: dict, result_rows: list[dict[str, str]]) -> bool:
        if str(item.get("score") or "").strip():
            return True
        item_index = str(item.get("index") or "").strip()
        if not item_index:
            return False
        item_name = str(item.get("name") or "").strip()
        item_path = canonical_path(item.get("path"))
        item_submitted_at = str(item.get("submittedAt") or "").strip()
        for row in result_rows:
            if str(row.get("file_index") or "").strip() != item_index:
                continue
            if item_name and str(row.get("file_name") or "").strip() != item_name:
                continue
            row_path = canonical_path(row.get("file_path"))
            if item_path and row_path and item_path != row_path:
                continue
            row_submitted_at = str(row.get("submitted_at") or "").strip()
            if item_submitted_at and row_submitted_at and item_submitted_at != row_submitted_at:
                continue
            if str(row.get("score") or "").strip():
                return True
        return False

    def failed_unscored_items(self, queue: list[dict]) -> list[dict]:
        result_rows: list[dict[str, str]] = []
        if self.results_path.exists():
            with self.results_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
                result_rows = list(csv.DictReader(stream))
        failed = []
        for item in queue:
            if item.get("status") != "failed" or self.item_has_result(item, result_rows):
                continue
            compact = self.compact_item(item)
            compact["exitCode"] = item.get("exitCode", "")
            compact["note"] = str(item.get("note") or "")[:500]
            failed.append(compact)
        return sorted(failed, key=queued_sort_key)

    def apply_deque_order(self, queue: list[dict], ordered: list[dict]) -> None:
        for position, item in enumerate(ordered):
            item["priority"] = QUEUE_PRIORITY_BASE - position
            item["status"] = "queued"
            if not item.get("createdAt"):
                item["createdAt"] = now_iso()

    def compact_item(self, item: dict) -> dict:
        return {
            "index": item.get("index"),
            "name": item.get("name"),
            "path": item.get("path"),
            "status": item.get("status", "queued"),
            "priority": item.get("priority"),
            "createdAt": item.get("createdAt", ""),
            "submittedAt": item.get("submittedAt", ""),
            "acceptedAt": item.get("acceptedAt", ""),
            "score": item.get("score", ""),
            "scoreTime": item.get("scoreTime", ""),
            "leaderboardPublishTime": item.get("leaderboardPublishTime", ""),
            "teamSubmitTime": item.get("teamSubmitTime", ""),
            "teamRank": item.get("teamRank", ""),
            "teamId": item.get("teamId", ""),
            "teamName": item.get("teamName", ""),
            "snapshotPath": item.get("snapshotPath", ""),
            "provenanceManifest": item.get("provenanceManifest", ""),
            "provenanceModelId": item.get("provenanceModelId", ""),
            "provenanceCandidateSha256": item.get("provenanceCandidateSha256", ""),
            "contentId": item.get("contentId", ""),
            "logicalSubmissionId": item.get("logicalSubmissionId", ""),
            "submissionId": item.get("submissionId", ""),
        }

    def next_index(self, queue: list[dict]) -> int:
        indexes = [int(item.get("index") or 0) for item in queue]
        return max(indexes or [0]) + 1

    def _is_link_or_reparse(self, result: os.stat_result) -> bool:
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        attributes = getattr(result, "st_file_attributes", 0)
        return stat.S_ISLNK(result.st_mode) or bool(attributes & reparse_flag)

    def authorized_artifact_file(self, raw_path: str | Path, label: str) -> Path:
        path = Path(raw_path).expanduser().absolute()
        if not any(path == root or root in path.parents for root in self.authorized_artifact_roots):
            raise ValueError(f"{label}_PATH_OUTSIDE_AUTHORIZED_ROOTS: {path}")
        for component in (path, *path.parents):
            if component in self.authorized_artifact_roots:
                break
            try:
                component_stat = os.lstat(component)
            except OSError as exc:
                raise ValueError(f"{label}_NOT_FOUND: {path}") from exc
            if self._is_link_or_reparse(component_stat):
                raise ValueError(f"{label}_REPARSE_POINT_FORBIDDEN: {component}")
        try:
            file_stat = os.lstat(path)
        except OSError as exc:
            raise ValueError(f"{label}_NOT_FOUND: {path}") from exc
        if self._is_link_or_reparse(file_stat):
            raise ValueError(f"{label}_REPARSE_POINT_FORBIDDEN: {path}")
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"{label}_NOT_FILE: {path}")
        resolved = path.resolve(strict=True)
        if not any(
            resolved == root or root in resolved.parents
            for root in self.authorized_artifact_roots
        ):
            raise ValueError(f"{label}_PATH_OUTSIDE_AUTHORIZED_ROOTS: {path}")
        return resolved

    def validate_provenance_for_file(self, file_path: str, manifest_path: str) -> dict:
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            raise ValueError("PROVENANCE_MANIFEST_REQUIRED")
        path = self.authorized_artifact_file(file_path, "SUBMISSION")
        manifest = self.authorized_artifact_file(manifest_path, "PROVENANCE_MANIFEST")
        try:
            result = validate_aic_provenance_manifest(
                manifest,
                authorized_roots=self.authorized_artifact_roots,
            )
        except ProvenanceError as exc:
            raise ValueError(str(exc)) from exc
        manifest_candidate = Path(result["candidate_zip"]).resolve()
        if manifest_candidate != path:
            raise ValueError(f"PROVENANCE_CANDIDATE_PATH_MISMATCH: manifest={manifest_candidate} file={path}")
        return result

    def item_for_file(self, file_path: str, index: int, provenance_manifest: str) -> dict:
        path = self.authorized_artifact_file(file_path, "SUBMISSION")
        if not path.exists():
            raise ValueError(f"ZIP_NOT_FOUND: {path}")
        if not path.is_file():
            raise ValueError(f"SUBMISSION_NOT_FILE: {path}")
        if path.suffix.lower() not in {".zip", ".rar"}:
            raise ValueError(f"UNSUPPORTED_FILE_TYPE: {path}")
        self.validate_submission_file({"file": str(path)})
        provenance = self.validate_provenance_for_file(str(path), provenance_manifest)
        stat = path.stat()
        name = path.name
        candidate_sha256 = provenance["candidate_sha256"]
        return {
            "index": index,
            "name": name,
            "path": str(path),
            "size": stat.st_size,
            "priority": QUEUE_PRIORITY_BASE,
            "status": "queued",
            "family": infer_family(name),
            "variant": infer_variant(name),
            "tier": infer_tier(name),
            "createdAt": now_iso(),
            "submittedAt": "",
            "acceptedAt": "",
            "score": "",
            "scoreTime": "",
            "leaderboardPublishTime": "",
            "teamSubmitTime": "",
            "exitCode": "",
            "note": "queued by aicomp_leaderboard MCP",
            "provenanceManifest": provenance["manifest"],
            "provenanceExperimentId": provenance["experiment_id"],
            "provenanceBackbone": provenance["backbone"],
            "provenanceModelId": provenance["model_id"],
            "provenanceCandidateSha256": candidate_sha256,
            "provenanceEvidenceFiles": provenance["evidence_files"],
            "provenanceValidatedAt": now_iso(),
            "competitionScope": COMPETITION_SCOPE,
            "teamId": TEAM_ID,
            "contentId": content_id(candidate_sha256),
            "logicalSubmissionId": logical_submission_id(candidate_sha256),
            "submissionId": logical_submission_id(candidate_sha256),
        }

    def validate_submission_file(self, arguments: dict) -> dict:
        raw = arguments.get("file")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("file is required")
        path = self.authorized_artifact_file(raw, "SUBMISSION")
        if path.suffix.lower() != ".zip":
            raise ValueError(f"UNSUPPORTED_FILE_TYPE: {path}")
        archive_size = path.stat().st_size
        if archive_size > SUBMISSION_MAX_ARCHIVE_BYTES:
            raise ValueError(
                f"ZIP_ARCHIVE_TOO_LARGE: max={SUBMISSION_MAX_ARCHIVE_BYTES} got={archive_size}"
            )
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ValueError(f"ZIP_INVALID: {path}") from exc
        with archive as zf:
            entries = zf.infolist()
            if len(entries) > SUBMISSION_MAX_ARCHIVE_ENTRIES:
                raise ValueError(
                    f"ZIP_TOO_MANY_ENTRIES: max={SUBMISSION_MAX_ARCHIVE_ENTRIES} got={len(entries)}"
                )
            names = [entry.filename for entry in entries]
            if names != [SUBMISSION_ARCNAME]:
                raise ValueError(f"ZIP_MUST_CONTAIN_ONLY_{SUBMISSION_ARCNAME}: {names}")
            entry = entries[0]
            if entry.file_size > SUBMISSION_MAX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    "ZIP_ENTRY_TOO_LARGE: "
                    f"max={SUBMISSION_MAX_UNCOMPRESSED_BYTES} got={entry.file_size}"
                )
            ratio = entry.file_size / max(entry.compress_size, 1)
            if ratio > SUBMISSION_MAX_COMPRESSION_RATIO:
                raise ValueError(
                    "ZIP_COMPRESSION_RATIO_TOO_HIGH: "
                    f"max={SUBMISSION_MAX_COMPRESSION_RATIO} got={ratio:.2f}"
                )
            rows: list[list[str]] = []
            with zf.open(entry) as raw_stream:
                text_stream = io.TextIOWrapper(
                    raw_stream,
                    encoding="utf-8-sig",
                    errors="strict",
                    newline="",
                )
                try:
                    for line_number, line in enumerate(text_stream, start=1):
                        if len(line.encode("utf-8")) > SUBMISSION_MAX_LINE_BYTES:
                            raise ValueError(
                                f"CSV_LINE_TOO_LONG: line={line_number} max={SUBMISSION_MAX_LINE_BYTES}"
                            )
                        parsed = next(csv.reader([line]))
                        if len(parsed) != 2:
                            raise ValueError(
                                "BAD_ROW_WIDTH: every row must have exactly two columns"
                            )
                        if any(len(field) > SUBMISSION_MAX_FIELD_CHARS for field in parsed):
                            raise ValueError(
                                f"CSV_FIELD_TOO_LONG: line={line_number} max={SUBMISSION_MAX_FIELD_CHARS}"
                            )
                        rows.append(parsed)
                        if len(rows) > SUBMISSION_MAX_ROWS:
                            raise ValueError(
                                f"CSV_TOO_MANY_ROWS: max={SUBMISSION_MAX_ROWS}"
                            )
                except UnicodeDecodeError as exc:
                    raise ValueError("CSV_ENCODING_INVALID_UTF8") from exc

        filenames = [row[0] for row in rows]
        if len(filenames) != len(set(filenames)):
            raise ValueError("DUPLICATE_FILENAME")

        expected_files = self.expected_test_files()
        if set(filenames) != expected_files:
            raise ValueError(
                f"TEST_FILE_SET_MISMATCH: expected={len(expected_files)} got={len(set(filenames))}"
            )

        valid_classes = self.valid_class_ids()
        for _filename, class_id in rows:
            if class_id.strip() not in valid_classes:
                raise ValueError(f"INVALID_CLASS_ID: {class_id}")

        return {
            "ok": True,
            "file": str(path),
            "archiveNames": names,
            "rows": len(rows),
            "bytes": path.stat().st_size,
        }

    def expected_test_files(self) -> set[str]:
        test_dir = self.root / "data" / "test"
        if not test_dir.is_dir():
            raise ValueError(f"TRUSTED_TEST_DATA_MISSING: {test_dir}")
        files = {p.name for p in test_dir.iterdir() if p.is_file()}
        if not files:
            raise ValueError(f"TRUSTED_TEST_DATA_EMPTY: {test_dir}")
        return files

    def valid_class_ids(self) -> set[str]:
        train_dir = self.root / "data" / "train"
        if not train_dir.is_dir():
            raise ValueError(f"TRUSTED_TRAIN_DATA_MISSING: {train_dir}")
        class_ids = {p.name for p in train_dir.iterdir() if p.is_dir()}
        if not class_ids:
            raise ValueError(f"TRUSTED_TRAIN_DATA_EMPTY: {train_dir}")
        return class_ids

    def queue_item_sha256(self, item: dict) -> str:
        stored = str(
            item.get("provenanceCandidateSha256")
            or item.get("artifactSha256")
            or item.get("candidateSha256")
            or ""
        ).strip().lower()
        if stored:
            return stored
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            return ""
        path = Path(raw_path).expanduser()
        if not path.is_file():
            return ""
        return sha256_file(path)

    def semantic_queue_revision(self, queue: list[dict], active: dict | None) -> str:
        queue_state = [
            {
                "index": item.get("index"),
                "status": item.get("status", "queued"),
                "priority": item.get("priority"),
                "createdAt": item.get("createdAt", ""),
                "path": canonical_path(item.get("path")),
                "candidateSha256": self.queue_item_sha256(item),
                "logicalSubmissionId": item.get("logicalSubmissionId") or "",
                "submittedAt": item.get("submittedAt") or "",
                "acceptedAt": item.get("acceptedAt") or "",
                "attemptId": item.get("attemptId") or "",
                "runnerId": item.get("runnerId") or "",
                "exitCode": item.get("exitCode"),
                "submitDispatchState": item.get("submitDispatchState") or "",
                "scoreCaptureWindowKey": item.get("scoreCaptureWindowKey") or "",
            }
            for item in queue
        ]
        active_state = None
        if isinstance(active, dict):
            active_state = {
                "queueIndex": active.get("queue_index"),
                "status": active.get("status"),
                "submittedAt": active.get("submitted_at") or "",
                "acceptedAt": active.get("accepted_at") or "",
                "logicalSubmissionId": active.get("logical_submission_id") or "",
                "candidateSha256": active.get("candidate_sha256") or "",
                "scoreCaptureWindowKey": active.get("score_capture_window_key") or "",
            }
        encoded = json.dumps(
            {"queue": queue_state, "active": active_state},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def runner_start_intent(self, queue: list[dict], active: dict | None) -> tuple[dict | None, list[str]]:
        issues: list[str] = []
        blocking = [item for item in queue if item.get("status") in BLOCKING_QUEUE_STATUSES]
        if len(blocking) > 1:
            issues.append("MULTIPLE_BLOCKING_QUEUE_ITEMS")

        revision = self.semantic_queue_revision(queue, active)
        if active:
            queue_index = active.get("queue_index")
            if queue_index in (None, ""):
                issues.append("ACTIVE_QUEUE_INDEX_REQUIRED")
                return None, issues
            matches = [item for item in queue if str(item.get("index")) == str(queue_index)]
            if len(matches) != 1:
                issues.append("ACTIVE_QUEUE_INDEX_NOT_UNIQUE")
                return None, issues
            item = matches[0]
            if item.get("status") != "awaiting_score" or active.get("status") != "awaiting_score":
                issues.append("ACTIVE_NOT_CAPTURE_READY")
            item_accepted = str(item.get("acceptedAt") or "").strip()
            active_accepted = str(active.get("accepted_at") or "").strip()
            if not item_accepted:
                issues.append("ACTIVE_ACCEPTANCE_NOT_IN_QUEUE")
            if not active_accepted:
                issues.append("ACTIVE_ACCEPTED_AT_REQUIRED")
            if item_accepted and active_accepted and item_accepted != active_accepted:
                issues.append("ACTIVE_ACCEPTED_AT_MISMATCH")
            item_logical_id = str(item.get("logicalSubmissionId") or "").strip()
            active_logical_id = str(active.get("logical_submission_id") or "").strip()
            if not item_logical_id or not active_logical_id:
                issues.append("ACTIVE_LOGICAL_ID_REQUIRED")
            elif item_logical_id != active_logical_id:
                issues.append("ACTIVE_LOGICAL_ID_MISMATCH")
            candidate_sha256 = self.queue_item_sha256(item)
            active_candidate_sha256 = str(active.get("candidate_sha256") or "").strip().lower()
            if not candidate_sha256 or not active_candidate_sha256:
                issues.append("ACTIVE_CANDIDATE_SHA256_REQUIRED")
            elif candidate_sha256 != active_candidate_sha256:
                issues.append("ACTIVE_CANDIDATE_SHA256_MISMATCH")
            if active.get("score_capture_window_key") or item.get("scoreCaptureWindowKey"):
                issues.append("SCORE_CAPTURE_WINDOW_ALREADY_CLAIMED")
            if blocking and (len(blocking) != 1 or blocking[0] is not item):
                issues.append("ACTIVE_BLOCKING_ITEM_MISMATCH")
            if issues:
                return None, sorted(set(issues))
            return (
                {
                    "action": "capture_score",
                    "queueIndex": int(item["index"]),
                    "candidateSha256": candidate_sha256,
                    "logicalSubmissionId": str(item.get("logicalSubmissionId") or ""),
                    "acceptedAt": item_accepted,
                    "queueRevision": revision,
                },
                [],
            )

        if blocking:
            issues.append("BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK")
            return None, sorted(set(issues))

        queued = self.queued_items(queue)
        if not queued:
            return None, []
        item = queued[0]
        candidate_sha256 = self.queue_item_sha256(item)
        logical_id = str(item.get("logicalSubmissionId") or "")
        if not candidate_sha256 or not logical_id:
            issues.append("QUEUED_SUBMISSION_IDENTITY_INCOMPLETE")
            return None, issues
        return (
            {
                "action": "submit_candidate",
                "queueIndex": int(item["index"]),
                "candidateSha256": candidate_sha256,
                "logicalSubmissionId": logical_id,
                "queueRevision": revision,
            },
            [],
        )

    def no_dispatch_log_proof(self, runner: dict) -> dict | None:
        stdout_value = str(runner.get("stdout") or "").strip()
        if not stdout_value:
            return None
        stdout_path = Path(stdout_value).expanduser().resolve()
        try:
            stdout_path.relative_to(self.submissions.resolve())
        except ValueError:
            return None
        if not stdout_path.name.startswith("aicomp_mcp_runner_stdout_") or stdout_path.suffix.lower() != ".log":
            return None
        if not stdout_path.is_file():
            return None

        output_bytes = stdout_path.read_bytes()
        output = output_bytes.decode("utf-8", errors="replace")
        if re.search(r"(?m)^SUBMIT_(?:CLICKED|ACCEPTED)_AT=", output):
            return None
        click_payloads = re.findall(r"(?m)^click:\s*(\{[^\r\n]*\})\s*$", output)
        if len(click_payloads) != 3:
            return None
        try:
            click_rows = [json.loads(payload) for payload in click_payloads]
        except json.JSONDecodeError:
            return None
        if any(not isinstance(row, dict) or row.get("clicked") is not False for row in click_rows):
            return None
        upload_ready = any(
            line.startswith("upload ready:") and '"ready":true' in line.replace(" ", "")
            for line in output.splitlines()
        )
        if not upload_ready or "upload state after settle:" not in output:
            return None
        explicit_marker = bool(
            re.search(r"(?m)^SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED\s*$", output)
        )
        return {
            "evidencePath": str(stdout_path),
            "evidenceSha256": hashlib.sha256(output_bytes).hexdigest(),
            "proofVersion": "explicit-marker-v1" if explicit_marker else "legacy-three-false-clicks-v1",
            "outputTail": output[-1000:],
            "outputTailSha256": hashlib.sha256(output[-1000:].encode("utf-8")).hexdigest(),
        }

    def cdp_connection_refused_no_dispatch_proof(self, runner: dict) -> dict | None:
        stdout_value = str(runner.get("stdout") or "").strip()
        stderr_value = str(runner.get("stderr") or "").strip()
        if not stdout_value or not stderr_value:
            return None
        stdout_path = Path(stdout_value).expanduser().resolve()
        stderr_path = Path(stderr_value).expanduser().resolve()
        submissions_root = self.submissions.resolve()
        try:
            stdout_path.relative_to(submissions_root)
            stderr_path.relative_to(submissions_root)
        except ValueError:
            return None
        if not stdout_path.name.startswith("aicomp_mcp_runner_stdout_") or stdout_path.suffix.lower() != ".log":
            return None
        if not stderr_path.name.startswith("aicomp_mcp_runner_stderr_") or stderr_path.suffix.lower() != ".log":
            return None
        if not stdout_path.is_file() or not stderr_path.is_file():
            return None

        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        if re.search(r"(?m)^SUBMIT_(?:CLICKED|ACCEPTED)_AT=", combined):
            return None
        if re.search(r"(?m)^click:\s*\{", stdout):
            return None
        if "Submitting exactly one bound candidate:" not in stdout:
            return None
        if "connect ECONNREFUSED 127.0.0.1:9222" not in stderr:
            return None
        if "SUBMIT_OUTCOME_UNKNOWN_BLOCKING" not in stderr:
            return None
        combined_bytes = (
            b"stdout_sha256="
            + hashlib.sha256(stdout_bytes).hexdigest().encode("ascii")
            + b"\nstderr_sha256="
            + hashlib.sha256(stderr_bytes).hexdigest().encode("ascii")
            + b"\n"
            + stdout_bytes
            + b"\n--- STDERR ---\n"
            + stderr_bytes
        )
        output_tail = combined[-1000:]
        return {
            "evidencePath": str(stderr_path),
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "stdoutSha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderrSha256": hashlib.sha256(stderr_bytes).hexdigest(),
            "evidenceSha256": hashlib.sha256(combined_bytes).hexdigest(),
            "proofVersion": "cdp-connection-refused-before-submit-v1",
            "outputTail": output_tail,
            "outputTailSha256": hashlib.sha256(output_tail.encode("utf-8")).hexdigest(),
            "expectedExitCode": 1,
        }

    def upload_ready_process_lost_no_dispatch_proof(self, runner: dict) -> dict | None:
        if runner_process_liveness(runner)[0]:
            return None
        stdout_value = str(runner.get("stdout") or "").strip()
        stderr_value = str(runner.get("stderr") or "").strip()
        if not stdout_value or not stderr_value:
            return None
        stdout_path = Path(stdout_value).expanduser().resolve()
        stderr_path = Path(stderr_value).expanduser().resolve()
        submissions_root = self.submissions.resolve()
        try:
            stdout_path.relative_to(submissions_root)
            stderr_path.relative_to(submissions_root)
        except ValueError:
            return None
        if not stdout_path.name.startswith("aicomp_mcp_runner_stdout_") or stdout_path.suffix.lower() != ".log":
            return None
        if not stderr_path.name.startswith("aicomp_mcp_runner_stderr_") or stderr_path.suffix.lower() != ".log":
            return None
        if not stdout_path.is_file() or not stderr_path.is_file():
            return None
        stdout_bytes = stdout_path.read_bytes()
        stderr_bytes = stderr_path.read_bytes()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        combined = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        if re.search(r"(?m)^SUBMIT_(?:CLICKED|ACCEPTED)_AT=", combined):
            return None
        if re.search(r"(?m)^click:\s*\{", stdout):
            return None
        required_markers = [
            "Submitting exactly one bound candidate:",
            "open submit form:",
            "set file input: true",
            "upload ready:",
            "waiting 45000ms for platform form state to settle...",
        ]
        if any(marker not in stdout for marker in required_markers):
            return None
        if "submit feedback:" in stdout:
            return None
        combined_bytes = (
            b"stdout_sha256="
            + hashlib.sha256(stdout_bytes).hexdigest().encode("ascii")
            + b"\nstderr_sha256="
            + hashlib.sha256(stderr_bytes).hexdigest().encode("ascii")
            + b"\n"
            + stdout_bytes
            + b"\n--- STDERR ---\n"
            + stderr_bytes
        )
        output_tail = combined[-1000:]
        return {
            "evidencePath": str(stdout_path),
            "stdoutPath": str(stdout_path),
            "stderrPath": str(stderr_path),
            "stdoutSha256": hashlib.sha256(stdout_bytes).hexdigest(),
            "stderrSha256": hashlib.sha256(stderr_bytes).hexdigest(),
            "evidenceSha256": hashlib.sha256(combined_bytes).hexdigest(),
            "proofVersion": "upload-ready-process-lost-before-submit-v1",
            "outputTail": output_tail,
            "outputTailSha256": hashlib.sha256(output_tail.encode("utf-8")).hexdigest(),
            "expectedExitCode": None,
        }

    def no_dispatch_evidence(self, item: dict) -> dict | None:
        if item.get("status") not in {"outcome_unknown", "submit_not_dispatched", "uploading"}:
            return None
        if str(item.get("submittedAt") or "").strip() or str(item.get("acceptedAt") or "").strip():
            return None
        item_exit_code = safe_int(item.get("exitCode"))
        if item_exit_code not in {None, 1, 5}:
            return None

        queue_index = str(item.get("index") or "")
        candidate_sha256 = self.queue_item_sha256(item)
        logical_id = str(item.get("logicalSubmissionId") or "").strip()
        attempt_id = str(item.get("attemptId") or "").strip()
        submit_started_at = parse_iso_datetime(item.get("submitStartedAt"))
        expected_runner_id = str(item.get("runnerId") or "").strip()
        if (
            not queue_index
            or not candidate_sha256
            or not logical_id
            or not attempt_id
            or not expected_runner_id
            or submit_started_at is None
        ):
            return None

        matches: list[dict] = []
        for runner in self.read_runner_doc().get("runners", []):
            if not isinstance(runner, dict):
                continue
            if runner.get("mode") != "submit-once":
                continue
            intent = runner.get("intent")
            if not isinstance(intent, dict) or intent.get("action") != "submit_candidate":
                continue
            if str(intent.get("queueIndex") or "") != queue_index:
                continue
            if str(intent.get("candidateSha256") or "").strip().lower() != candidate_sha256:
                continue
            if str(intent.get("logicalSubmissionId") or "").strip() != logical_id:
                continue
            if str(runner.get("runner_id") or "") != expected_runner_id:
                continue
            if str(runner.get("attemptId") or "") != attempt_id:
                continue
            if str(intent.get("attemptId") or "") != attempt_id:
                continue
            if str(intent.get("runnerId") or "") != expected_runner_id:
                continue
            runner_started_at = parse_iso_datetime(runner.get("startedAt"))
            if runner_started_at is None or abs((submit_started_at - runner_started_at).total_seconds()) > 300:
                continue
            matches.append(runner)
        if len(matches) != 1:
            return None

        runner = matches[0]
        if item_exit_code == 5:
            proof = self.no_dispatch_log_proof(runner)
        elif item_exit_code == 1:
            proof = self.cdp_connection_refused_no_dispatch_proof(runner)
        else:
            proof = self.upload_ready_process_lost_no_dispatch_proof(runner)
        if proof is None:
            return None
        if proof.get("proofVersion") == "cdp-connection-refused-before-submit-v1":
            events_bytes = self.events_path.read_bytes() if self.events_path.is_file() else b""
            event_chain = self.legacy_event_chain(
                events_bytes,
                item,
                runner,
                expected_exit_code=int(proof.get("expectedExitCode") or item_exit_code or 1),
            )
            if event_chain is None:
                return None
            proof = {**proof, **event_chain}
        item_binding = item.get("legacyNoDispatchBinding")
        runner_binding = runner.get("legacyNoDispatchBinding")
        if item_binding or runner_binding:
            if not isinstance(item_binding, dict) or not isinstance(runner_binding, dict):
                return None
            for binding in (item_binding, runner_binding):
                if binding.get("bindingVersion") != "legacy-attempt-binding-v2":
                    return None
                if str(binding.get("runnerId") or "") != expected_runner_id:
                    return None
                if str(binding.get("attemptId") or "") != attempt_id:
                    return None
                if str(binding.get("evidenceSha256") or "") != proof["evidenceSha256"]:
                    return None
                if str(binding.get("queueNoteTailSha256") or "") != proof["outputTailSha256"]:
                    return None
            if str(item.get("note") or "") != proof["outputTail"]:
                return None
            events_bytes = self.events_path.read_bytes() if self.events_path.is_file() else b""
            event_chain = self.legacy_event_chain(
                events_bytes,
                item,
                runner,
                expected_exit_code=int(proof.get("expectedExitCode") or item_exit_code or 5),
            )
            if event_chain is None:
                return None
            if any(
                str(binding.get("eventChainSha256") or "") != event_chain["eventChainSha256"]
                for binding in (item_binding, runner_binding)
            ):
                return None
        return {
            "runnerId": str(runner.get("runner_id") or ""),
            "attemptId": attempt_id,
            **proof,
        }

    def legacy_event_chain(
        self,
        events_bytes: bytes,
        item: dict,
        runner: dict,
        *,
        expected_exit_code: int = 5,
    ) -> dict | None:
        rows = []
        for raw_line in events_bytes.decode("utf-8", errors="replace").splitlines():
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError:
                return None
            if isinstance(row, dict):
                rows.append(row)

        queue_index = str(item.get("index") or "")
        attempt_id = str(item.get("attemptId") or "")
        logical_id = str(item.get("logicalSubmissionId") or "")
        candidate_sha256 = self.queue_item_sha256(item)
        submit_events = [
            row
            for row in rows
            if row.get("event") == "submit.start"
            and str(row.get("queue_index") or "") == queue_index
            and str(row.get("attempt_id") or "") == attempt_id
            and str(row.get("logical_submission_id") or "") == logical_id
            and str(row.get("candidate_sha256") or "").lower() == candidate_sha256
        ]
        outcome_events = [
            row
            for row in rows
            if row.get("event") == "submit.outcome_unknown_blocking"
            and str(row.get("queue_index") or "") == queue_index
            and str(row.get("attempt_id") or "") == attempt_id
            and safe_int(row.get("exit_code")) == expected_exit_code
        ]
        if len(submit_events) != 1 or len(outcome_events) != 1:
            return None

        submit_time = parse_iso_datetime(submit_events[0].get("time"))
        outcome_time = parse_iso_datetime(outcome_events[0].get("time"))
        item_time = parse_iso_datetime(item.get("submitStartedAt"))
        record_time = parse_iso_datetime(runner.get("startedAt"))
        if None in {submit_time, outcome_time, item_time, record_time}:
            return None
        assert submit_time is not None
        assert outcome_time is not None
        assert item_time is not None
        assert record_time is not None

        expected_runner_id = str(runner.get("runner_id") or "").strip()
        if not expected_runner_id:
            return None
        runner_candidates: list[tuple[dict, dt.datetime]] = []
        for row in rows:
            if (
                row.get("event") != "runner.started"
                or str(row.get("queue_index") or "") != queue_index
                or str(row.get("logical_submission_id") or "") != logical_id
                or str(row.get("candidate_sha256") or "").lower()
                != candidate_sha256
            ):
                continue
            event_attempt_id = str(row.get("attempt_id") or "").strip()
            event_runner_id = str(row.get("runner_id") or "").strip()
            if event_attempt_id and event_attempt_id != attempt_id:
                continue
            if event_runner_id and event_runner_id != expected_runner_id:
                continue
            runner_time = parse_iso_datetime(row.get("time"))
            if runner_time is None or runner_time > submit_time:
                continue
            if abs((runner_time - record_time).total_seconds()) > 300:
                continue
            if abs((submit_time - runner_time).total_seconds()) > 300:
                continue
            runner_candidates.append((row, runner_time))
        if len(runner_candidates) != 1:
            return None
        runner_event, runner_time = runner_candidates[0]

        if not runner_time <= submit_time <= outcome_time:
            return None
        if abs((submit_time - item_time).total_seconds()) > 300:
            return None

        chain = [runner_event, submit_events[0], outcome_events[0]]
        encoded = json.dumps(chain, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "eventChainSha256": hashlib.sha256(encoded).hexdigest(),
            "eventsSha256": hashlib.sha256(events_bytes).hexdigest(),
            "runnerEventAt": runner_event["time"],
            "submitEventAt": submit_events[0]["time"],
            "outcomeEventAt": outcome_events[0]["time"],
        }

    def legacy_no_dispatch_binding_plan_from_snapshots(
        self,
        doc: dict,
        active: dict | None,
        runner_doc: dict,
        events_bytes: bytes,
    ) -> dict | None:
        queue = doc.get("queue")
        runners = runner_doc.get("runners")
        if not isinstance(queue, list) or not isinstance(runners, list):
            return None
        if active or self.runner_lease_path.exists() or any(
            runner_process_liveness(row)[0] for row in runners
        ):
            return None
        blocking = [item for item in queue if item.get("status") in BLOCKING_QUEUE_STATUSES]
        if len(blocking) != 1:
            return None
        item = blocking[0]
        if item.get("status") != "outcome_unknown":
            return None
        runner_id = str(item.get("runnerId") or "").strip()
        attempt_id = str(item.get("attemptId") or "").strip()
        submit_started_at = parse_iso_datetime(item.get("submitStartedAt"))
        candidate_sha256 = self.queue_item_sha256(item)
        logical_id = str(item.get("logicalSubmissionId") or "").strip()
        if (
            not runner_id
            or not attempt_id
            or submit_started_at is None
            or not candidate_sha256
            or not logical_id
            or str(item.get("submittedAt") or "").strip()
            or str(item.get("acceptedAt") or "").strip()
        ):
            return None
        item_exit_code = safe_int(item.get("exitCode"))
        if item_exit_code not in {1, 5}:
            return None

        identity_runners = []
        for runner in runners:
            if not isinstance(runner, dict) or runner.get("mode") != "submit-once":
                continue
            intent = runner.get("intent")
            if not isinstance(intent, dict) or intent.get("action") != "submit_candidate":
                continue
            if str(intent.get("queueIndex") or "") != str(item.get("index") or ""):
                continue
            if str(intent.get("candidateSha256") or "").lower() != candidate_sha256:
                continue
            if str(intent.get("logicalSubmissionId") or "") != logical_id:
                continue
            runner_started_at = parse_iso_datetime(runner.get("startedAt"))
            if runner_started_at is None or abs((submit_started_at - runner_started_at).total_seconds()) > 300:
                continue
            identity_runners.append(runner)
        if len(identity_runners) != 1:
            return None
        runner = identity_runners[0]
        if str(runner.get("runner_id") or "") != runner_id:
            return None
        runner_intent = runner.get("intent")
        if not isinstance(runner_intent, dict):
            return None
        for value in (runner.get("attemptId"), runner_intent.get("attemptId")):
            if str(value or "").strip() not in {"", attempt_id}:
                return None
        if str(runner_intent.get("runnerId") or "").strip() not in {"", runner_id}:
            return None

        item_binding = item.get("legacyNoDispatchBinding")
        runner_binding = runner.get("legacyNoDispatchBinding")
        if item_binding or runner_binding:
            if not isinstance(item_binding, dict) or not isinstance(runner_binding, dict):
                return None
            if item_binding.get("bindingVersion") == "legacy-attempt-binding-v2":
                return None
            for binding in (item_binding, runner_binding):
                if binding.get("bindingVersion") != "legacy-attempt-binding-v1":
                    return None
                if str(binding.get("runnerId") or "") != runner_id:
                    return None
                if str(binding.get("attemptId") or "") != attempt_id:
                    return None

        if item_exit_code == 5:
            proof = self.no_dispatch_log_proof(runner)
        else:
            proof = self.cdp_connection_refused_no_dispatch_proof(runner)
        if proof is None or str(item.get("note") or "") != proof["outputTail"]:
            return None
        event_chain = self.legacy_event_chain(
            events_bytes,
            item,
            runner,
            expected_exit_code=int(proof.get("expectedExitCode") or item_exit_code or 5),
        )
        if event_chain is None:
            return None
        runner_revision = hashlib.sha256(
            json.dumps(runner_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "action": "bind_legacy_no_dispatch_attempt_v2",
            "queueIndex": int(item["index"]),
            "candidateSha256": candidate_sha256,
            "logicalSubmissionId": logical_id,
            "attemptId": attempt_id,
            "runnerId": runner_id,
            "evidenceSha256": proof["evidenceSha256"],
            "proofVersion": proof["proofVersion"],
            "queueNoteTailSha256": proof["outputTailSha256"],
            "eventChainSha256": event_chain["eventChainSha256"],
            "eventsSha256": event_chain["eventsSha256"],
            "queueRevision": self.semantic_queue_revision(queue, active),
            "runnerRevision": f"sha256:{runner_revision}",
        }

    def legacy_no_dispatch_binding_plan(self) -> dict | None:
        events_bytes = self.events_path.read_bytes() if self.events_path.is_file() else b""
        return self.legacy_no_dispatch_binding_plan_from_snapshots(
            self.read_queue_doc(),
            read_json(self.active_path, None),
            self.read_runner_doc(),
            events_bytes,
        )

    def bind_legacy_no_dispatch_attempt(self, arguments: dict) -> dict:
        if arguments.get("confirm_binding") is not True:
            raise ValueError("CONFIRM_LEGACY_NO_DISPATCH_BINDING_REQUIRED")
        supplied_intent = arguments.get("intent")
        if not isinstance(supplied_intent, dict):
            raise ValueError("LEGACY_NO_DISPATCH_BINDING_INTENT_REQUIRED")

        with self.control_lock():
            self.assert_queue_mutation_allowed()
            doc = self.read_queue_doc()
            active = read_json(self.active_path, None)
            runner_doc = self.read_runner_doc()
            events_bytes = self.events_path.read_bytes() if self.events_path.is_file() else b""
            current_intent = self.legacy_no_dispatch_binding_plan_from_snapshots(
                doc,
                active,
                runner_doc,
                events_bytes,
            )
            if current_intent is None or supplied_intent != current_intent:
                raise ValueError("STALE_LEGACY_NO_DISPATCH_BINDING_CONFIRMATION")

            item = next(item for item in doc["queue"] if int(item.get("index")) == current_intent["queueIndex"])
            runner = next(
                row for row in runner_doc["runners"] if str(row.get("runner_id") or "") == current_intent["runnerId"]
            )
            validated_item = json.loads(json.dumps(item))
            self.revalidate_queued_item_provenance(validated_item)
            if self.queue_item_sha256(validated_item) != current_intent["candidateSha256"]:
                raise ValueError("LEGACY_BINDING_CANDIDATE_SHA256_MISMATCH")

            bound_at = now_iso()
            self.reconciliation_backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup_nonce = uuid.uuid4().hex
            queue_backup = self.reconciliation_backup_dir / (
                f"{stamp}_legacy-binding-v2-queue-index-{item.get('index')}_{backup_nonce}.json"
            )
            runner_backup = self.reconciliation_backup_dir / (
                f"{stamp}_legacy-binding-v2-runners-index-{item.get('index')}_{backup_nonce}.json"
            )
            write_json(queue_backup, doc)
            write_json(runner_backup, runner_doc)

            prior_binding = item.get("legacyNoDispatchBinding")
            binding = {
                "bindingVersion": "legacy-attempt-binding-v2",
                "attemptId": current_intent["attemptId"],
                "runnerId": current_intent["runnerId"],
                "evidenceSha256": current_intent["evidenceSha256"],
                "proofVersion": current_intent["proofVersion"],
                "queueNoteTailSha256": current_intent["queueNoteTailSha256"],
                "eventChainSha256": current_intent["eventChainSha256"],
                "eventsSha256AtBinding": current_intent["eventsSha256"],
                "boundAt": bound_at,
                "queueBackup": str(queue_backup),
                "runnerBackup": str(runner_backup),
                "supersedes": prior_binding if isinstance(prior_binding, dict) else None,
            }
            runner["attemptId"] = current_intent["attemptId"]
            runner_intent = runner.get("intent")
            if not isinstance(runner_intent, dict):
                raise ValueError("LEGACY_RUNNER_INTENT_REQUIRED")
            runner_intent["attemptId"] = current_intent["attemptId"]
            runner_intent["runnerId"] = current_intent["runnerId"]
            runner["legacyNoDispatchBinding"] = binding
            item["submitDispatchState"] = "not_dispatched"
            item["legacyNoDispatchBinding"] = binding

            self.save_runner_doc(runner_doc)
            self.save_queue_doc(doc)
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": bound_at,
                            "event": "submit.legacy_no_dispatch_bound_v2",
                            "queue_index": item.get("index"),
                            "attempt_id": current_intent["attemptId"],
                            "runner_id": current_intent["runnerId"],
                            "logical_submission_id": item.get("logicalSubmissionId") or "",
                            "candidate_sha256": self.queue_item_sha256(item),
                            "evidence_sha256": current_intent["evidenceSha256"],
                            "event_chain_sha256": current_intent["eventChainSha256"],
                            "note": "maintainer exact-snapshot binding; operator reconciliation still required",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )

        return {
            "ok": True,
            "bound": True,
            "bindingVersion": "legacy-attempt-binding-v2",
            "queueIndex": item.get("index"),
            "attemptId": current_intent["attemptId"],
            "runnerId": current_intent["runnerId"],
            "candidateSha256": self.queue_item_sha256(item),
            "logicalSubmissionId": item.get("logicalSubmissionId") or "",
            "evidenceSha256": current_intent["evidenceSha256"],
            "eventChainSha256": current_intent["eventChainSha256"],
            "queueBackup": str(queue_backup),
            "runnerBackup": str(runner_backup),
        }

    def not_dispatched_reconciliation_intent(
        self,
        queue: list[dict],
        active: dict | None,
        *,
        runners: list[dict] | None = None,
    ) -> dict | None:
        if active:
            return None
        runner_lease = self.runner_lease_owner()
        if runner_lease and pid_is_running(runner_lease.get("pid")):
            return None
        statuses = runners if runners is not None else self.runner_statuses()
        if any(runner.get("running") for runner in statuses):
            return None
        blocking = [item for item in queue if item.get("status") in BLOCKING_QUEUE_STATUSES]
        if len(blocking) != 1:
            return None
        item = blocking[0]
        evidence = self.no_dispatch_evidence(item)
        if evidence is None:
            return None
        if runner_lease:
            lease_intent = runner_lease.get("intent")
            if not isinstance(lease_intent, dict):
                return None
            if str(lease_intent.get("queueIndex") or "") != str(item.get("index") or ""):
                return None
            if str(lease_intent.get("runnerId") or "") != evidence["runnerId"]:
                return None
            if str(lease_intent.get("attemptId") or "") != evidence["attemptId"]:
                return None
            if str(lease_intent.get("candidateSha256") or "").lower() != self.queue_item_sha256(item):
                return None
            if str(lease_intent.get("logicalSubmissionId") or "") != str(item.get("logicalSubmissionId") or ""):
                return None
        return {
            "action": "reconcile_not_dispatched",
            "queueIndex": int(item["index"]),
            "candidateSha256": self.queue_item_sha256(item),
            "logicalSubmissionId": str(item.get("logicalSubmissionId") or ""),
            "attemptId": evidence["attemptId"],
            "runnerId": evidence["runnerId"],
            "evidenceSha256": evidence["evidenceSha256"],
            "proofVersion": evidence["proofVersion"],
            "queueRevision": self.semantic_queue_revision(queue, active),
        }

    def queue_status(self) -> dict:
        doc = self.read_queue_doc()
        queue = doc["queue"]
        active = read_json(self.active_path, None)
        queued = [self.compact_item(item) for item in self.queued_items(queue)]
        failed_unscored = self.failed_unscored_items(queue)
        runners = self.runner_statuses()
        runner_intent, identity_issues = self.runner_start_intent(queue, active)
        runner_lease = self.runner_lease_owner()
        reconciliation_intent = self.not_dispatched_reconciliation_intent(
            queue,
            active,
            runners=runners,
        )
        public_leaderboard_recovery_intent = None
        public_leaderboard_recovery_reason = "ACTIVE_NOT_SCORE_MISSED"
        if isinstance(active, dict) and active.get("status") == "score_missed":
            try:
                candidate_intent = self.public_leaderboard_recovery_intent()
                if safe_int(candidate_intent.get("laterAcceptedSubmissionCount")):
                    public_leaderboard_recovery_reason = (
                        "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
                    )
                else:
                    public_leaderboard_recovery_intent = candidate_intent
                    recovery_claim = read_json(
                        Path(candidate_intent["recoveryClaimPath"]), None
                    )
                    if isinstance(recovery_claim, dict):
                        public_leaderboard_recovery_reason = (
                            "PUBLIC_LEADERBOARD_RECOVERY_CAPTURED_RECONCILE_READY"
                            if recovery_claim.get("state") == "captured"
                            else "PUBLIC_LEADERBOARD_RECOVERY_ALREADY_CONSUMED"
                        )
                    else:
                        public_leaderboard_recovery_reason = (
                            "EXACT_PUBLIC_LEADERBOARD_RECOVERY_INTENT_READY"
                        )
            except ValueError as exc:
                public_leaderboard_recovery_reason = compact_text(exc)
        live_runner = any(runner.get("running") for runner in runners) or bool(
            runner_lease and pid_is_running(runner_lease.get("pid"))
        )
        if live_runner:
            runner_intent = None
        if live_runner:
            state = "running"
            runner_intent = None
            reconciliation_intent = None
        elif reconciliation_intent and identity_issues == ["BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK"]:
            identity_issues = []
            state = "submit_not_dispatched_reconcile_required"
            runner_intent = None
        elif active and active.get("status") == "score_missed":
            state = "score_missed_blocked"
            runner_intent = None
        elif identity_issues:
            state = "blocked_identity_corruption"
            runner_intent = None
        elif active:
            state = "waiting_score"
        elif queued:
            state = "queued"
        elif failed_unscored:
            state = "needs_attention"
        else:
            state = "idle"
        return {
            "ok": True,
            "contractVersion": SUBMISSION_CONTRACT_VERSION,
            "queueSchemaVersion": QUEUE_SCHEMA_VERSION,
            "requiredCallerSandboxMode": CALLER_SANDBOX_MODE,
            "state": state,
            "root": str(self.root),
            "queue_path": str(self.queue_path),
            "active": active,
            "queued": queued,
            "failed_unscored": failed_unscored,
            "runnerStartIntent": runner_intent,
            "reconciliationIntent": reconciliation_intent,
            "publicLeaderboardRecoveryIntent": public_leaderboard_recovery_intent,
            "publicLeaderboardRecoveryReason": public_leaderboard_recovery_reason,
            "identityIssues": identity_issues,
            "counts": status_counts(queue),
            "runners": runners,
            "runnerLease": runner_lease,
            "stateText": self.state_path.read_text(encoding="utf-8", errors="replace") if self.state_path.exists() else "",
        }

    def queue_reconcile_not_dispatched(self, arguments: dict) -> dict:
        if arguments.get("confirm_retry_same_identity") is not True:
            raise ValueError("CONFIRM_NOT_DISPATCHED_RETRY_REQUIRED")
        supplied_intent = arguments.get("intent")
        if not isinstance(supplied_intent, dict):
            raise ValueError("RECONCILIATION_INTENT_REQUIRED: call queue_status and echo reconciliationIntent exactly")

        with self.control_lock():
            doc = self.read_queue_doc()
            queue = doc["queue"]
            active = read_json(self.active_path, None)
            current_intent = self.not_dispatched_reconciliation_intent(
                queue,
                active,
                runners=self.runner_statuses(),
            )
            if current_intent is None or supplied_intent != current_intent:
                raise ValueError("STALE_RECONCILIATION_CONFIRMATION: queue, runner, or evidence changed")

            matches = [
                item
                for item in queue
                if str(item.get("index")) == str(current_intent["queueIndex"])
            ]
            if len(matches) != 1:
                raise ValueError("RECONCILIATION_QUEUE_INDEX_NOT_UNIQUE")
            item = matches[0]
            evidence = self.no_dispatch_evidence(item)
            if evidence is None or evidence["evidenceSha256"] != current_intent["evidenceSha256"]:
                raise ValueError("NO_DISPATCH_EVIDENCE_CHANGED")
            stale_lease_owner = self.runner_lease_owner()
            if stale_lease_owner:
                if pid_is_running(stale_lease_owner.get("pid")):
                    raise ValueError("RUNNER_LEASE_ACTIVE: queue mutation is blocked while a runner owns the ledger")
                lease_intent = stale_lease_owner.get("intent")
                if not isinstance(lease_intent, dict):
                    raise ValueError("RUNNER_LEASE_STALE_RECONCILIATION_REQUIRED")
                if (
                    str(lease_intent.get("queueIndex") or "") != str(current_intent["queueIndex"])
                    or str(lease_intent.get("runnerId") or "") != current_intent["runnerId"]
                    or str(lease_intent.get("attemptId") or "") != current_intent["attemptId"]
                    or str(lease_intent.get("candidateSha256") or "").lower() != current_intent["candidateSha256"]
                    or str(lease_intent.get("logicalSubmissionId") or "") != current_intent["logicalSubmissionId"]
                ):
                    raise ValueError("RUNNER_LEASE_STALE_RECONCILIATION_REQUIRED")
            else:
                self.assert_queue_mutation_allowed()

            validated_item = json.loads(json.dumps(item))
            self.revalidate_queued_item_provenance(validated_item)
            if self.queue_item_sha256(validated_item) != current_intent["candidateSha256"]:
                raise ValueError("RECONCILIATION_CANDIDATE_SHA256_MISMATCH")
            if str(validated_item.get("logicalSubmissionId") or "") != current_intent["logicalSubmissionId"]:
                raise ValueError("RECONCILIATION_LOGICAL_ID_MISMATCH")

            reconciled_at = now_iso()
            self.reconciliation_backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = self.reconciliation_backup_dir / (
                f"{stamp}_queue-index-{item.get('index')}_{uuid.uuid4().hex}.json"
            )
            write_json(backup_path, doc)
            if stale_lease_owner:
                self.remove_lock_directory(
                    self.runner_lease_path,
                    token=str(stale_lease_owner.get("token") or ""),
                )

            attempt_record = {
                "attemptId": evidence["attemptId"],
                "runnerId": evidence["runnerId"],
                "submitStartedAt": str(item.get("submitStartedAt") or ""),
                "exitCode": item.get("exitCode"),
                "dispatchState": "not_dispatched",
                "proofVersion": evidence["proofVersion"],
                "evidencePath": evidence["evidencePath"],
                "evidenceSha256": evidence["evidenceSha256"],
                "reconciledAt": reconciled_at,
                "backupPath": str(backup_path),
            }
            history = item.get("submitAttemptHistory")
            if history is None:
                history = []
                item["submitAttemptHistory"] = history
            if not isinstance(history, list):
                raise ValueError("SUBMIT_ATTEMPT_HISTORY_CORRUPT")
            history.append(attempt_record)
            item["lastNoDispatchAttempt"] = attempt_record
            item["submitDispatchState"] = "not_dispatched"
            item["noDispatchReconciledAt"] = reconciled_at
            item["status"] = "queued"
            item["note"] = (
                f"{item.get('note') or ''}\nno-dispatch attempt reconciled at {reconciled_at}; "
                "same queue identity retained; fresh submit intent required"
            ).strip()[-1500:]
            self.save_queue_doc(doc)

            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": reconciled_at,
                            "event": "submit.not_dispatched_reconciled",
                            "queue_index": item.get("index"),
                            "attempt_id": evidence["attemptId"],
                            "runner_id": evidence["runnerId"],
                            "logical_submission_id": item.get("logicalSubmissionId") or "",
                            "candidate_sha256": self.queue_item_sha256(item),
                            "evidence_sha256": evidence["evidenceSha256"],
                            "proof_version": evidence["proofVersion"],
                            "backup_path": str(backup_path),
                            "note": "same queue item returned to queued; no external submit was replayed",
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )

        return {
            "ok": True,
            "queueIndex": item.get("index"),
            "logicalSubmissionId": item.get("logicalSubmissionId") or "",
            "candidateSha256": self.queue_item_sha256(item),
            "status": "queued",
            "sameIdentity": True,
            "evidenceSha256": evidence["evidenceSha256"],
            "backupPath": str(backup_path),
        }

    def queue_push(self, arguments: dict, front: bool) -> dict:
        files = arguments.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("files must be a non-empty array")
        provenance_manifests = arguments.get("provenance_manifests")
        if not isinstance(provenance_manifests, list) or len(provenance_manifests) != len(files):
            raise ValueError("PROVENANCE_MANIFEST_REQUIRED: provenance_manifests must align with files")
        if arguments.get("allow_duplicate") is True:
            raise ValueError("DUPLICATE_OVERRIDE_FORBIDDEN: ordinary queue tools cannot replay an artifact")

        with self.control_lock():
            self.assert_queue_mutation_allowed()
            doc = self.read_queue_doc()
            queue = doc["queue"]
            queued = self.queued_items(queue)
            seen_paths = {canonical_path(item.get("path")) for item in queue if canonical_path(item.get("path"))}
            seen_sha256 = {sha for item in queue if (sha := self.queue_item_sha256(item))}
            seen_logical_ids = {
                str(item.get("logicalSubmissionId"))
                for item in queue
                if str(item.get("logicalSubmissionId") or "").strip()
            }
            created: list[dict] = []
            next_index = self.next_index(queue)
            for raw, provenance_manifest in zip(files, provenance_manifests):
                item = self.item_for_file(str(raw), next_index, str(provenance_manifest))
                path_key = canonical_path(item["path"])
                candidate_sha256 = str(item["provenanceCandidateSha256"])
                logical_id = str(item["logicalSubmissionId"])
                conflicts = []
                if path_key in seen_paths:
                    conflicts.append("path")
                if candidate_sha256 in seen_sha256:
                    conflicts.append("candidate_sha256")
                if logical_id in seen_logical_ids:
                    conflicts.append("logical_submission_id")
                if conflicts:
                    raise ValueError(
                        "DUPLICATE_SUBMISSION_IDENTITY: "
                        f"file={item['path']} conflicts={','.join(conflicts)}"
                    )
                created.append(item)
                next_index += 1
                seen_paths.add(path_key)
                seen_sha256.add(candidate_sha256)
                seen_logical_ids.add(logical_id)

            queue.extend(created)
            if front:
                ordered = created + queued
            else:
                ordered = queued + created
            self.apply_deque_order(queue, ordered)
            self.save_queue_doc(doc)
            return {
                "ok": True,
                "added": [item["name"] for item in created],
                "queued": [self.compact_item(item) for item in self.queued_items(queue)],
            }

    def revalidate_queued_item_provenance(self, item: dict) -> None:
        path = str(item.get("path") or "")
        manifest = str(item.get("provenanceManifest") or "")
        if not path:
            raise ValueError(f"QUEUE_ITEM_PATH_REQUIRED: index={item.get('index')}")
        provenance = self.validate_provenance_for_file(path, manifest)
        stored_sha256 = str(item.get("provenanceCandidateSha256") or "").strip().lower()
        if stored_sha256 and stored_sha256 != provenance["candidate_sha256"]:
            raise ValueError(
                "PROVENANCE_QUEUE_SHA256_MISMATCH: "
                f"index={item.get('index')} stored={stored_sha256} current={provenance['candidate_sha256']}"
            )
        stored_model_id = str(item.get("provenanceModelId") or "").strip()
        if stored_model_id and stored_model_id != provenance["model_id"]:
            raise ValueError(
                "PROVENANCE_QUEUE_MODEL_ID_MISMATCH: "
                f"index={item.get('index')} stored={stored_model_id} current={provenance['model_id']}"
            )
        item["provenanceManifest"] = provenance["manifest"]
        item["provenanceExperimentId"] = provenance["experiment_id"]
        item["provenanceBackbone"] = provenance["backbone"]
        item["provenanceModelId"] = provenance["model_id"]
        item["provenanceCandidateSha256"] = provenance["candidate_sha256"]
        item["provenanceEvidenceFiles"] = provenance["evidence_files"]
        item["provenanceValidatedAt"] = now_iso()
        item["competitionScope"] = COMPETITION_SCOPE
        item["teamId"] = TEAM_ID
        item["contentId"] = content_id(provenance["candidate_sha256"])
        item["logicalSubmissionId"] = logical_submission_id(provenance["candidate_sha256"])
        item["submissionId"] = item.get("submissionId") or item["logicalSubmissionId"]

    def revalidate_queued_provenance_before_runner(self) -> None:
        doc = self.read_queue_doc()
        queued = self.queued_items(doc["queue"])
        for item in queued:
            self.revalidate_queued_item_provenance(item)
        if queued:
            self.save_queue_doc(doc)

    def queue_move(self, arguments: dict, front: bool) -> dict:
        selectors = arguments.get("items")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError("items must be a non-empty array")
        with self.control_lock():
            self.assert_queue_mutation_allowed()
            doc = self.read_queue_doc()
            queue = doc["queue"]
            queued = self.queued_items(queue)
            moved: list[dict] = []
            for selector in selectors:
                item = find_item(queue, str(selector))
                if item is None:
                    raise ValueError(f"ITEM_NOT_FOUND: {selector}")
                if item.get("status", "queued") != "queued":
                    raise ValueError(f"ITEM_NOT_QUEUED: {selector}")
                if item not in moved:
                    moved.append(item)
            remaining = [item for item in queued if item not in moved]
            ordered = moved + remaining if front else remaining + moved
            self.apply_deque_order(queue, ordered)
            self.save_queue_doc(doc)
            return {
                "ok": True,
                "moved": [item["name"] for item in moved],
                "queued": [self.compact_item(item) for item in self.queued_items(queue)],
            }

    def queue_remove(self, arguments: dict) -> dict:
        selectors = arguments.get("items")
        reason = str(arguments.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is required")
        if not isinstance(selectors, list) or not selectors:
            raise ValueError("items must be a non-empty array")
        with self.control_lock():
            self.assert_queue_mutation_allowed()
            doc = self.read_queue_doc()
            queue = doc["queue"]
            removed: list[str] = []
            for selector in selectors:
                item = find_item(queue, str(selector))
                if item is None:
                    raise ValueError(f"ITEM_NOT_FOUND: {selector}")
                if item.get("status", "queued") != "queued":
                    raise ValueError(f"ITEM_NOT_QUEUED: {selector}")
                item["status"] = "dropped"
                item["removeReason"] = reason
                item["droppedAt"] = now_iso()
                removed.append(str(item.get("name")))
            self.apply_deque_order(queue, self.queued_items(queue))
            self.save_queue_doc(doc)
            return {
                "ok": True,
                "removed": removed,
                "reason": reason,
                "queued": [self.compact_item(item) for item in self.queued_items(queue)],
            }

    def trusted_node_executable(self, error_code: str) -> Path:
        node = AICOMP_NODE_EXECUTABLE
        try:
            resolved = node.resolve(strict=True)
        except OSError:
            raise ValueError(error_code) from None
        if node.is_symlink() or not resolved.is_file():
            raise ValueError(error_code)
        return resolved

    def trusted_tool(self, name: str, expected_sha256: str, error_code: str) -> Path:
        path = self.tools_dir / name
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise ValueError(error_code) from None
        if (
            path.is_symlink()
            or not resolved.is_file()
            or sha256_file(resolved) != expected_sha256
        ):
            raise ValueError(error_code)
        return resolved

    def hermetic_node_environment(self) -> dict[str, str]:
        environment = {
            "AICOMP_ROOT": str(self.root),
            "AICOMP_DEBUG_URL": SUBMISSION_RECORDS_DEBUG_URL,
        }
        if os.name == "nt":
            environment.update({"SystemRoot": r"C:\Windows", "WINDIR": r"C:\Windows"})
        return environment

    def runner_command(self) -> list[str]:
        error_code = "AICOMP_RUNNER_TOOLCHAIN_UNTRUSTED"
        node = self.trusted_node_executable(error_code)
        runner = self.trusted_tool(
            "aicomp_submit_queue.mjs",
            AICOMP_RUNNER_HELPER_SHA256,
            error_code,
        )
        self.trusted_tool("aicomp_cdp.mjs", AICOMP_CDP_HELPER_SHA256, error_code)
        self.trusted_tool(
            "aicomp_manifest.mjs",
            AICOMP_MANIFEST_HELPER_SHA256,
            error_code,
        )
        return [str(node), str(runner), "run"]

    def leaderboard_command(self) -> list[str]:
        error_code = "AICOMP_LEADERBOARD_TOOLCHAIN_UNTRUSTED"
        node = self.trusted_node_executable(error_code)
        helper = self.trusted_tool(
            "aicomp_cdp.mjs",
            AICOMP_CDP_HELPER_SHA256,
            error_code,
        )
        return [str(node), str(helper), "leaderboard"]

    def submission_records_command(self) -> list[str]:
        node = SUBMISSION_RECORDS_NODE_EXECUTABLE
        try:
            resolved_node = node.resolve(strict=True)
        except OSError:
            raise ValueError("SUBMISSION_RECORDS_NODE_UNAVAILABLE")
        try:
            helper = SUBMISSION_RECORDS_HELPER.resolve(strict=True)
        except OSError:
            raise ValueError("SUBMISSION_RECORDS_HELPER_UNAVAILABLE")
        if node.is_symlink() or not resolved_node.is_file():
            raise ValueError("SUBMISSION_RECORDS_NODE_UNTRUSTED")
        if (
            SUBMISSION_RECORDS_HELPER.is_symlink()
            or not helper.is_file()
            or sha256_file(helper) != SUBMISSION_RECORDS_HELPER_SHA256
        ):
            raise ValueError("SUBMISSION_RECORDS_HELPER_UNTRUSTED")
        return [
            str(resolved_node),
            str(helper),
            "submission-records",
        ]

    def read_runner_doc(self) -> dict:
        doc = read_json(self.runner_state_path, {"updatedAt": now_iso(), "schemaVersion": 1, "runners": []})
        if not isinstance(doc, dict):
            return {"updatedAt": now_iso(), "schemaVersion": 1, "runners": []}
        if not isinstance(doc.get("runners"), list):
            doc["runners"] = []
        return doc

    def save_runner_doc(self, doc: dict) -> None:
        doc["updatedAt"] = now_iso()
        doc["schemaVersion"] = 1
        write_json(self.runner_state_path, doc)

    def persist_runner_record(self, record: dict) -> None:
        with self.runner_state_lock:
            with self.runner_registry_lock():
                doc = self.read_runner_doc()
                runners = doc["runners"]
                runner_id = record.get("runner_id")
                replaced = False
                for index, existing in enumerate(runners):
                    if existing.get("runner_id") == runner_id:
                        merged = {
                            **existing,
                            **{
                                key: value
                                for key, value in record.items()
                                if value is not None
                            },
                        }
                        runners[index] = merged
                        replaced = True
                        break
                if not replaced:
                    runners.append(record)
                self.save_runner_doc(doc)

    def persisted_runner_statuses(self) -> list[dict]:
        rows = []
        for runner in self.read_runner_doc().get("runners", []):
            pid = runner.get("pid")
            running, process_identity_status = runner_process_liveness(runner)
            exit_code = None if running else runner.get("exitCode")
            rows.append(
                {
                    "runner_id": runner.get("runner_id"),
                    "pid": pid,
                    "running": running,
                    "exitCode": exit_code,
                    "stdout": runner.get("stdout"),
                    "stderr": runner.get("stderr"),
                    "startedAt": runner.get("startedAt"),
                    "finishedAt": runner.get("finishedAt", ""),
                    "processIdentity": runner.get("processIdentity"),
                    "processIdentityStatus": process_identity_status,
                    "source": "persisted",
                }
            )
        return rows

    def queue_runner_start(self, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        with self.control_lock():
            running = [runner for runner in self.runner_statuses() if runner["running"]]
            if running:
                return {"ok": True, "started": False, "reason": "RUNNER_ALREADY_RUNNING", "runners": running}

            lease_owner = self.runner_lease_owner()
            if lease_owner and pid_is_running(lease_owner.get("pid")):
                return {
                    "ok": True,
                    "started": False,
                    "reason": "RUNNER_ALREADY_RUNNING",
                    "runners": [],
                    "lease": lease_owner,
                }

            supplied_intent = arguments.get("intent")
            if not isinstance(supplied_intent, dict):
                raise ValueError("RUNNER_INTENT_REQUIRED: call queue_status and echo runnerStartIntent exactly")

            doc = self.read_queue_doc()
            queue = doc["queue"]
            active = read_json(self.active_path, None)
            current_intent, identity_issues = self.runner_start_intent(queue, active)
            if identity_issues:
                raise ValueError(f"RUNNER_IDENTITY_BLOCKED: {','.join(identity_issues)}")
            if current_intent is None or supplied_intent != current_intent:
                raise ValueError("STALE_RUNNER_CONFIRMATION: queue or active identity changed")

            action = current_intent["action"]
            runner_id = f"runner-{uuid.uuid4()}"
            attempt_id = ""
            if action == "submit_candidate":
                if arguments.get("confirm_real_submit") is not True:
                    raise ValueError("confirm_real_submit must be true for submit_candidate")
                item = next(
                    candidate
                    for candidate in queue
                    if str(candidate.get("index")) == str(current_intent["queueIndex"])
                )
                self.revalidate_queued_item_provenance(item)
                refreshed_intent, refreshed_issues = self.runner_start_intent(queue, active)
                if refreshed_issues or refreshed_intent != current_intent:
                    raise ValueError("STALE_RUNNER_CONFIRMATION: provenance or candidate identity changed")
                run_mode = "submit-once"
                attempt_id = str(uuid.uuid4())
            elif action == "capture_score":
                if arguments.get("confirm_real_submit") is True:
                    raise ValueError("CAPTURE_ONLY_CONFIRMATION_MUST_BE_FALSE")
                run_mode = "capture-only"
            else:
                raise ValueError(f"RUNNER_ACTION_UNSUPPORTED: {action}")

            cdp_readiness = self.cdp_probe()
            if not isinstance(cdp_readiness, dict) or cdp_readiness.get("ready") is not True:
                reason = (
                    cdp_readiness.get("reason")
                    if isinstance(cdp_readiness, dict)
                    else "CDP_PROBE_INVALID"
                )
                raise ValueError(
                    "CDP_UNAVAILABLE_BEFORE_RUNNER_START: "
                    f"{reason}; no runner, lease, or submission attempt was created"
                )

            execution_intent = {
                **current_intent,
                "runnerId": runner_id,
            }
            if attempt_id:
                execution_intent["attemptId"] = attempt_id

            lease_token, owner = self.reserve_runner_lease(execution_intent)
            if lease_token is None:
                return {
                    "ok": True,
                    "started": False,
                    "reason": "RUNNER_ALREADY_RUNNING",
                    "runners": [],
                    "lease": owner,
                }

            self.submissions.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stdout_path = self.submissions / f"aicomp_mcp_runner_stdout_{stamp}.log"
            stderr_path = self.submissions / f"aicomp_mcp_runner_stderr_{stamp}.log"
            env = {
                **self.hermetic_node_environment(),
                **AICOMP_RUNNER_RUNTIME_ENV,
                "AICOMP_RUN_MODE": run_mode,
                "AICOMP_EXPECTED_QUEUE_INDEX": str(current_intent["queueIndex"]),
                "AICOMP_EXPECTED_CANDIDATE_SHA256": str(current_intent.get("candidateSha256") or ""),
                "AICOMP_EXPECTED_LOGICAL_SUBMISSION_ID": str(current_intent.get("logicalSubmissionId") or ""),
                "AICOMP_EXPECTED_QUEUE_REVISION": str(current_intent["queueRevision"]),
                "AICOMP_EXPECTED_ACCEPTED_AT": str(current_intent.get("acceptedAt") or ""),
                "AICOMP_RUNNER_LEASE_TOKEN": lease_token,
                "AICOMP_RUNNER_ID": runner_id,
                "AICOMP_SUBMIT_ATTEMPT_ID": attempt_id,
            }
            launching_at = now_iso()
            record = {
                "runner_id": runner_id,
                "pid": None,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "startedAt": launching_at,
                "phase": "launching",
                "exitCode": None,
                "leaseToken": lease_token,
                "attemptId": attempt_id,
                "intent": execution_intent,
                "mode": run_mode,
            }
            out = None
            err = None
            try:
                self.persist_runner_record(record)
                out = stdout_path.open("a", encoding="utf-8")
                err = stderr_path.open("a", encoding="utf-8")
                process = subprocess.Popen(
                    self.runner_command(),
                    cwd=str(self.root),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    text=True,
                    env=env,
                )
            except Exception as exc:
                try:
                    self.persist_runner_record(
                        {
                            "runner_id": runner_id,
                            "phase": "launch_failed",
                            "finishedAt": now_iso(),
                            "launchError": type(exc).__name__,
                        }
                    )
                except Exception:
                    pass
                finally:
                    self.release_runner_lease(lease_token)
                raise
            finally:
                if out is not None:
                    out.close()
                if err is not None:
                    err.close()

            process_identity = process_start_identity(process.pid)
            self.update_runner_lease(
                lease_token,
                pid=process.pid,
                phase="running",
                runner_id=runner_id,
                startedAt=now_iso(),
                processIdentity=process_identity,
            )
            record = {
                **record,
                "pid": process.pid,
                "phase": "running",
                "processIdentity": process_identity,
            }
            self.persist_runner_record(record)
            with self.runner_lock:
                self.runners[runner_id] = {
                    **record,
                    "process": process,
                }
            return {
                "ok": True,
                "started": True,
                "runner_id": runner_id,
                "pid": process.pid,
                "mode": run_mode,
                "intent": current_intent,
                "attempt_id": attempt_id,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
            }

    def runner_statuses(self) -> list[dict]:
        rows_by_id: dict[str, dict] = {}
        for row in self.persisted_runner_statuses():
            runner_id = str(row.get("runner_id") or "")
            if runner_id:
                rows_by_id[runner_id] = row
        with self.runner_lock:
            runners = list(self.runners.values())
        for runner in runners:
            process = runner.get("process")
            exit_code = process.poll() if process else None
            row = {
                "runner_id": runner["runner_id"],
                "pid": runner["pid"],
                "running": exit_code is None,
                "exitCode": exit_code,
                "stdout": runner["stdout"],
                "stderr": runner["stderr"],
                "startedAt": runner["startedAt"],
                "source": "memory",
            }
            if exit_code is not None:
                row["finishedAt"] = runner.get("finishedAt") or now_iso()
                runner["finishedAt"] = row["finishedAt"]
                self.persist_runner_record({"runner_id": runner["runner_id"], "exitCode": exit_code, "finishedAt": row["finishedAt"]})
            rows_by_id[runner["runner_id"]] = row
        return sorted(rows_by_id.values(), key=lambda row: str(row.get("startedAt") or ""))

    def terminal_runner_queue_item(self, runner: dict) -> dict:
        """Return a strictly identity-bound queue summary after a runner exits.

        This is read-only reporting. If the persisted queue no longer matches the
        runner intent, return an issue instead of exposing a potentially unrelated
        score as the runner's outcome.
        """
        try:
            intent = runner.get("intent")
            if not isinstance(intent, dict):
                return {"finalQueueItem": None, "finalQueueItemIssue": "RUNNER_INTENT_MISSING"}
            runner_id = str(runner.get("runner_id") or "")
            intent_runner_id = str(intent.get("runnerId") or "")
            queue_index = str(intent.get("queueIndex") or "")
            expected_sha256 = str(intent.get("candidateSha256") or "").strip().lower()
            expected_logical_id = str(intent.get("logicalSubmissionId") or "")
            if not runner_id or intent_runner_id != runner_id:
                return {"finalQueueItem": None, "finalQueueItemIssue": "RUNNER_IDENTITY_MISMATCH"}
            if not queue_index or not expected_sha256 or not expected_logical_id:
                return {"finalQueueItem": None, "finalQueueItemIssue": "RUNNER_INTENT_IDENTITY_INCOMPLETE"}

            queue = self.read_queue_doc().get("queue") or []
            matches = [item for item in queue if str(item.get("index") or "") == queue_index]
            if len(matches) != 1:
                return {"finalQueueItem": None, "finalQueueItemIssue": "FINAL_QUEUE_INDEX_NOT_UNIQUE"}
            item = matches[0]
            actual_sha256 = self.queue_item_sha256(item)
            if actual_sha256 != expected_sha256:
                return {"finalQueueItem": None, "finalQueueItemIssue": "FINAL_QUEUE_CANDIDATE_SHA256_MISMATCH"}
            if str(item.get("logicalSubmissionId") or "") != expected_logical_id:
                return {"finalQueueItem": None, "finalQueueItemIssue": "FINAL_QUEUE_LOGICAL_ID_MISMATCH"}
            return {"finalQueueItem": self.compact_item(item), "finalQueueItemIssue": ""}
        except Exception as exc:
            return {
                "finalQueueItem": None,
                "finalQueueItemIssue": f"FINAL_QUEUE_SUMMARY_UNAVAILABLE:{type(exc).__name__}",
            }

    def queue_runner_watch(self, arguments: dict) -> dict:
        runner_id = arguments.get("runner_id")
        wait_for_completion = arguments.get("wait_for_completion", False)
        if not isinstance(wait_for_completion, bool):
            raise ValueError("RUNNER_WATCH_WAIT_MODE_INVALID")
        timeout_seconds = arguments.get(
            "timeout_seconds",
            RUNNER_BLOCKING_WATCH_DEFAULT_SECONDS,
        )
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= RUNNER_BLOCKING_WATCH_MAX_SECONDS
        ):
            raise ValueError("RUNNER_WATCH_TIMEOUT_INVALID")
        with self.runner_lock:
            if runner_id:
                runner = self.runners.get(str(runner_id))
            else:
                runner = next((r for r in self.runners.values() if r["process"].poll() is None), None)
        if not runner:
            statuses = self.runner_statuses()
            if runner_id:
                runner = next((row for row in statuses if row.get("runner_id") == str(runner_id)), None)
            else:
                runner = next((row for row in statuses if row.get("running")), None)
            if not runner or not runner.get("running"):
                raise ValueError("RUNNER_NOT_FOUND")
            if wait_for_completion:
                raise ValueError("RUNNER_BLOCKING_WATCH_REQUIRES_IN_MEMORY_CHILD")
            thread = threading.Thread(target=self.watch_runner_pid_loop, args=(dict(runner),), daemon=True)
            thread.start()
            return {"ok": True, "watching": True, "runner_id": runner["runner_id"], "pid": runner["pid"], "source": "persisted"}
        if wait_for_completion:
            process = runner.get("process")
            if process is None:
                raise ValueError("RUNNER_BLOCKING_WATCH_REQUIRES_IN_MEMORY_CHILD")
            try:
                exit_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("RUNNER_BLOCKING_WATCH_TIMEOUT") from exc
            finished_at = now_iso()
            runner["finishedAt"] = finished_at
            self.persist_runner_record(
                {
                    "runner_id": runner["runner_id"],
                    "exitCode": exit_code,
                    "finishedAt": finished_at,
                    "phase": "finished",
                }
            )
            terminal_queue_summary = self.terminal_runner_queue_item(runner)
            return {
                "ok": True,
                "watching": False,
                "terminal": True,
                "runner_id": runner["runner_id"],
                "pid": runner["pid"],
                "exitCode": exit_code,
                "stdout": runner["stdout"],
                "stderr": runner["stderr"],
                "startedAt": runner["startedAt"],
                "finishedAt": finished_at,
                "source": "memory-blocking",
                **terminal_queue_summary,
            }
        thread = threading.Thread(target=self.watch_runner_loop, args=(dict(runner),), daemon=True)
        thread.start()
        return {"ok": True, "watching": True, "runner_id": runner["runner_id"], "pid": runner["pid"], "source": "memory"}

    def watch_runner_loop(self, runner: dict) -> None:
        process = runner["process"]
        exit_code = process.wait()
        finished_at = now_iso()
        self.persist_runner_record({"runner_id": runner["runner_id"], "exitCode": exit_code, "finishedAt": finished_at})
        self.write(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "notice" if exit_code == 0 else "warning",
                    "logger": "aicomp-leaderboard",
                    "data": {
                        "message": f"AICOMP queue runner {runner['runner_id']} exited with code {exit_code}",
                        "runner_id": runner["runner_id"],
                        "pid": runner["pid"],
                        "exitCode": exit_code,
                        "stdout": runner["stdout"],
                        "stderr": runner["stderr"],
                        "finishedAt": finished_at,
                    },
                },
            }
        )

    def watch_runner_pid_loop(self, runner: dict) -> None:
        while runner_process_liveness(runner)[0]:
            time.sleep(RUNNER_WATCH_POLL_INTERVAL_SECONDS)
        finished_at = now_iso()
        self.persist_runner_record({"runner_id": runner["runner_id"], "finishedAt": finished_at})
        self.write(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": "notice",
                    "logger": "aicomp-leaderboard",
                    "data": {
                        "message": f"AICOMP queue runner {runner['runner_id']} is no longer running",
                        "runner_id": runner["runner_id"],
                        "pid": runner["pid"],
                        "exitCode": runner.get("exitCode"),
                        "stdout": runner["stdout"],
                        "stderr": runner["stderr"],
                        "finishedAt": finished_at,
                        "source": "persisted",
                    },
                },
            }
        )

    def snapshot_freshness(self, parsed: dict, active: dict | None) -> dict:
        if not active:
            return {"fresh": False, "reason": "NO_ACTIVE_SUBMISSION"}
        if active.get("status") != "awaiting_score":
            return {"fresh": False, "reason": f"ACTIVE_STATUS_NOT_AWAITING_SCORE:{active.get('status')}"}
        if not parsed.get("score") or not parsed.get("scoreTime") or not parsed.get("teamSubmitTime"):
            return {"fresh": False, "reason": "TEAM_ROW_INCOMPLETE"}

        accepted_at = parse_iso_datetime(active.get("accepted_at") or active.get("submitted_at"))
        team_submit_at = parse_team_submit_datetime(parsed.get("teamSubmitTime"))
        score_at = parse_team_submit_datetime(parsed.get("scoreTime"))
        if accepted_at is None:
            return {"fresh": False, "reason": "ACTIVE_ACCEPTED_AT_MISSING"}
        if team_submit_at is None:
            return {"fresh": False, "reason": "TEAM_SUBMIT_TIME_UNPARSEABLE"}
        if score_at is None:
            return {"fresh": False, "reason": "SCORE_TIME_UNPARSEABLE"}
        window = dt.timedelta(seconds=60)
        if team_submit_at < accepted_at - window:
            return {
                "fresh": False,
                "reason": "PUBLIC_LEADERBOARD_PREVIOUS_SUBMISSION_BEFORE_ACTIVE_ACCEPTED_AT",
                "accepted_at": accepted_at.isoformat(),
                "team_submit_at": team_submit_at.isoformat(),
            }
        if abs(team_submit_at - accepted_at) > window:
            return {
                "fresh": False,
                "reason": "PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW",
                "accepted_at": accepted_at.isoformat(),
                "team_submit_at": team_submit_at.isoformat(),
            }
        if score_at < team_submit_at:
            return {
                "fresh": False,
                "reason": "PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME",
                "team_submit_at": team_submit_at.isoformat(),
                "score_at": score_at.isoformat(),
            }
        return {
            "fresh": True,
            "reason": "TEAM_SUBMIT_TIME_MATCHES_ACTIVE",
            "accepted_at": accepted_at.isoformat(),
            "team_submit_at": team_submit_at.isoformat(),
            "score_at": score_at.isoformat(),
        }

    def submission_record_target(self, arguments: dict) -> dict:
        active = read_json(self.active_path, None)
        queue = self.read_queue_doc()["queue"]
        requested_queue_index = safe_int(arguments.get("queue_index"))
        selector = first_nonempty(arguments.get("selector"), arguments.get("file"))
        queue_item = None

        if requested_queue_index is not None:
            queue_item = next((item for item in queue if safe_int(item.get("index")) == requested_queue_index), None)
            if queue_item is None:
                active_index = (
                    safe_int(active.get("queue_index"))
                    if isinstance(active, dict)
                    else None
                )
                if active_index != requested_queue_index:
                    raise ValueError("QUEUE_ITEM_NOT_FOUND")
        elif selector:
            queue_item = find_item(queue, selector)
            if queue_item is None and arguments.get("selector"):
                raise ValueError("QUEUE_ITEM_NOT_FOUND")
        if (
            requested_queue_index is None
            and queue_item is None
            and not arguments.get("file")
            and isinstance(active, dict)
        ):
            active_index = safe_int(active.get("queue_index"))
            if active_index is not None:
                queue_item = next((item for item in queue if safe_int(item.get("index")) == active_index), None)
            if queue_item is None and active.get("file"):
                queue_item = find_item(queue, str(active.get("file")))

        explicit_file = compact_text(arguments.get("file"))

        def require_exact_override(argument_name: str, bound_value: Any) -> None:
            override = compact_text(arguments.get(argument_name))
            if override and override != compact_text(bound_value):
                raise ValueError("SUBMISSION_RECORD_TARGET_TIME_MISMATCH")

        if queue_item is not None:
            item_path = compact_text(queue_item.get("path"))
            item_name = compact_text(queue_item.get("name"))
            if explicit_file:
                explicit_path = Path(explicit_file).expanduser()
                file_matches = (
                    canonical_path(explicit_path) == canonical_path(item_path)
                    if explicit_path.is_absolute()
                    else explicit_path.name == item_name
                )
                if not file_matches:
                    raise ValueError("SUBMISSION_RECORD_TARGET_FILE_MISMATCH")
            path_text = item_path
            name = first_nonempty(item_name, Path(path_text).name if path_text else "")
            queue_index = safe_int(queue_item.get("index"))
            if requested_queue_index is not None:
                require_exact_override("submitted_at", queue_item.get("submittedAt"))
                require_exact_override("accepted_at", queue_item.get("acceptedAt"))
                submitted_at = compact_text(queue_item.get("submittedAt"))
                accepted_at = compact_text(queue_item.get("acceptedAt"))
            else:
                submitted_at = first_nonempty(
                    arguments.get("submitted_at"),
                    queue_item.get("submittedAt"),
                )
                accepted_at = first_nonempty(
                    arguments.get("accepted_at"),
                    queue_item.get("acceptedAt"),
                )
        else:
            if requested_queue_index is not None:
                assert isinstance(active, dict)
                active_path = compact_text(active.get("path"))
                active_name = first_nonempty(
                    active.get("file"),
                    Path(active_path).name if active_path else "",
                )
                if explicit_file:
                    explicit_path = Path(explicit_file).expanduser()
                    file_matches = (
                        canonical_path(explicit_path) == canonical_path(active_path)
                        if explicit_path.is_absolute()
                        else explicit_path.name == active_name
                    )
                    if not file_matches:
                        raise ValueError("SUBMISSION_RECORD_TARGET_FILE_MISMATCH")
                require_exact_override("submitted_at", active.get("submitted_at"))
                require_exact_override("accepted_at", active.get("accepted_at"))
                path_text = active_path
                name = active_name
                queue_index = safe_int(active.get("queue_index"))
                submitted_at = compact_text(active.get("submitted_at"))
                accepted_at = compact_text(active.get("accepted_at"))
            else:
                path_text = first_nonempty(
                    explicit_file,
                    active.get("path") if isinstance(active, dict) else "",
                )
                name = first_nonempty(
                    Path(explicit_file).name if explicit_file else "",
                    active.get("file") if isinstance(active, dict) else "",
                    Path(path_text).name if path_text else "",
                )
                queue_index = (
                    safe_int(active.get("queue_index"))
                    if isinstance(active, dict)
                    else None
                )
                submitted_at = first_nonempty(
                    arguments.get("submitted_at"),
                    active.get("submitted_at") if isinstance(active, dict) else "",
                )
                accepted_at = first_nonempty(
                    arguments.get("accepted_at"),
                    active.get("accepted_at") if isinstance(active, dict) else "",
                )

        file_hash = ""
        if path_text:
            path = Path(path_text).expanduser()
            if path.exists() and path.is_file():
                file_hash = sha256_file(path)

        return {
            "queue_index": queue_index,
            "file": name,
            "path": path_text,
            "sha256": file_hash,
            "submitted_at": submitted_at,
            "accepted_at": accepted_at,
            "active": active,
            "queue_item": self.compact_item(queue_item) if queue_item else None,
        }

    def normalize_submission_record(self, raw: dict, evidence_path: Path) -> dict:
        flat = flatten_record_fields(raw)
        source = first_nonempty(raw.get("source"), raw.get("sourceKind"), raw.get("source_kind"), flat.get("source"))
        attachment_url = first_nonempty(
            flat.get("attachment_url"),
            dict_first(raw, "attachmentUrl", "fileUrl", "url", "downloadUrl"),
        )
        attachment_filename = first_nonempty(
            flat.get("attachment_filename"),
            dict_first(raw, "attachmentFilename", "filename", "fileName", "name"),
            filename_from_url(attachment_url),
        )
        score_text = first_nonempty(flat.get("score"), dict_first(raw, "score", "numericScore", "scoreValue"))
        score = score_as_float(score_text)
        status = first_nonempty(flat.get("status"), dict_first(raw, "status", "state", "resultStatus", "workStatus"))
        if not status and score is not None:
            status = "scored"
        return {
            "record_id": first_nonempty(flat.get("record_id"), dict_first(raw, "recordId", "workId", "id", "businessId")),
            "work_id": first_nonempty(dict_first(raw, "workId", "work_id"), flat.get("record_id")),
            "queue_index": safe_int(flat.get("queue_index")),
            "submit_time": first_nonempty(flat.get("submit_time"), dict_first(raw, "submitTime", "submittedAt", "createdAt", "createTime")),
            "create_time": first_nonempty(dict_first(raw, "createTime", "createdAt"), flat.get("submit_time")),
            "attachment_filename": attachment_filename,
            "attachment_hash": first_nonempty(flat.get("attachment_hash"), dict_first(raw, "sha256", "md5", "hash")),
            "attachment_url": attachment_url,
            "status": status,
            "failure_reason": first_nonempty(flat.get("failure_reason"), dict_first(raw, "failureReason", "failReason", "reason", "message")),
            "score": score,
            "score_text": score_text,
            "score_time": first_nonempty(flat.get("score_time"), dict_first(raw, "scoreTime", "scoredAt")),
            "competition_id": first_nonempty(flat.get("competition_id"), dict_first(raw, "competitionId", "compId")),
            "competition_name": first_nonempty(flat.get("competition_name"), dict_first(raw, "competitionName")),
            "team_id": first_nonempty(flat.get("team_id"), dict_first(raw, "teamId", "teamNo", "teamCode")),
            "team_name": first_nonempty(flat.get("team_name"), dict_first(raw, "teamName")),
            "source": source or "aicomp_submission_records",
            "raw_evidence_path": str(evidence_path),
            "raw_keys": sorted(str(key) for key in raw.keys())[:80],
        }

    def raw_submission_records(self, payload: dict) -> list[dict]:
        candidates: list[Any] = []
        for key in ("records", "submissionRecords", "items", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("records", "submissionRecords", "items", "rows", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
        elif isinstance(data, list):
            candidates.extend(data)
        return [item for item in candidates if isinstance(item, dict)]

    def extracted_submission_records(self, payload: dict, evidence_path: Path) -> list[dict]:
        if public_leaderboard_source(payload.get("source") or payload.get("sourceKind")):
            return []
        records = []
        seen = set()
        for raw in self.raw_submission_records(payload):
            if public_leaderboard_source(raw.get("source") or raw.get("sourceKind")):
                continue
            normalized = self.normalize_submission_record(raw, evidence_path)
            identity = (
                normalized.get("record_id"),
                normalized.get("attachment_filename"),
                normalized.get("submit_time"),
                normalized.get("score_time"),
                normalized.get("score_text"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            records.append(normalized)
        return records

    def match_submission_record(self, record: dict, target: dict) -> dict:
        matched_by: list[str] = []
        team_id = compact_text(record.get("team_id"))
        team_name = compact_text(record.get("team_name"))
        if team_id and team_id != TEAM_ID:
            return {"matched": False, "reason": "TEAM_ID_MISMATCH"}
        if team_name and team_name != TEAM_NAME:
            return {"matched": False, "reason": "TEAM_NAME_MISMATCH"}

        target_queue_index = safe_int(target.get("queue_index"))
        record_queue_index = safe_int(record.get("queue_index"))
        if target_queue_index is not None and record_queue_index is not None:
            if target_queue_index != record_queue_index:
                return {"matched": False, "reason": "QUEUE_INDEX_MISMATCH"}
            matched_by.append("QUEUE_INDEX_MATCH")

        target_file = compact_text(target.get("file")).replace("\\", "/").rsplit("/", 1)[-1].lower()
        record_file = (
            first_nonempty(record.get("attachment_filename"), filename_from_url(record.get("attachment_url")))
            .replace("\\", "/")
            .rsplit("/", 1)[-1]
            .lower()
        )
        if target_file and record_file:
            if target_file != record_file:
                return {"matched": False, "reason": "ATTACHMENT_FILENAME_MISMATCH"}
            matched_by.append("ATTACHMENT_FILENAME_MATCH")

        target_hash = compact_text(target.get("sha256")).lower()
        record_hash = compact_text(record.get("attachment_hash")).lower()
        if target_hash and record_hash:
            if target_hash != record_hash:
                return {"matched": False, "reason": "ATTACHMENT_HASH_MISMATCH"}
            matched_by.append("ATTACHMENT_HASH_MATCH")

        target_times = [parse_submission_datetime(target.get("accepted_at")), parse_submission_datetime(target.get("submitted_at"))]
        record_times = [parse_submission_datetime(record.get("submit_time")), parse_submission_datetime(record.get("create_time"))]
        comparable_target_times = [value for value in target_times if value is not None]
        comparable_record_times = [value for value in record_times if value is not None]
        delta_seconds: int | None = None
        if comparable_target_times and comparable_record_times:
            delta_seconds = int(
                min(
                    abs((record_time - target_time).total_seconds())
                    for target_time in comparable_target_times
                    for record_time in comparable_record_times
                )
            )
            if delta_seconds > 15 * 60:
                return {"matched": False, "reason": "SUBMIT_TIME_MISMATCH", "delta_seconds": delta_seconds}
            matched_by.append("TEAM_AND_SUBMIT_TIME_MATCH")

        strong_matches = {
            "QUEUE_INDEX_MATCH",
            "ATTACHMENT_HASH_MATCH",
            "TEAM_AND_SUBMIT_TIME_MATCH",
        }.intersection(matched_by)
        if matched_by and not strong_matches:
            return {
                "matched": False,
                "reason": PER_SUBMISSION_FIELDS_INSUFFICIENT,
                "matched_by": matched_by,
            }

        if matched_by:
            preferred_reason = next(
                (
                    reason
                    for reason in (
                        "QUEUE_INDEX_MATCH",
                        "ATTACHMENT_HASH_MATCH",
                        "TEAM_AND_SUBMIT_TIME_MATCH",
                        "ATTACHMENT_FILENAME_MATCH",
                    )
                    if reason in matched_by
                ),
                matched_by[0],
            )
            result = {"matched": True, "reason": preferred_reason, "matched_by": matched_by}
            if delta_seconds is not None:
                result["delta_seconds"] = delta_seconds
            return result

        if not any(target.get(key) for key in ("queue_index", "file", "accepted_at", "submitted_at", "sha256")):
            return {"matched": False, "reason": "NO_TARGET"}
        return {"matched": False, "reason": "NO_MATCHING_SUBMISSION_RECORD"}

    def path_revision(self, path: Path) -> str:
        return f"sha256:{sha256_file(path)}" if path.is_file() else "missing"

    def bounded_evidence_path(self, raw_path: Any, root: Path, code: str) -> Path:
        text = compact_text(raw_path)
        if not text:
            raise ValueError(f"{code}_PATH_REQUIRED")
        path = Path(text).expanduser().resolve()
        root = root.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"{code}_PATH_OUTSIDE_CONTROL_PLANE")
        if not path.is_file():
            raise ValueError(f"{code}_FILE_MISSING")
        return path

    def current_score_missed_binding(self) -> dict:
        doc = self.read_queue_doc()
        queue = doc["queue"]
        active = read_json(self.active_path, None)
        if not isinstance(active, dict) or active.get("status") != "score_missed":
            raise ValueError("ACTIVE_NOT_SCORE_MISSED")
        queue_index = safe_int(active.get("queue_index"))
        if queue_index is None:
            raise ValueError("ACTIVE_QUEUE_INDEX_REQUIRED")
        matches = [item for item in queue if safe_int(item.get("index")) == queue_index]
        if len(matches) != 1:
            raise ValueError("ACTIVE_QUEUE_INDEX_NOT_UNIQUE")
        item = matches[0]
        if item.get("status") != "score_missed":
            raise ValueError("QUEUE_ITEM_NOT_SCORE_MISSED")
        blocking = [candidate for candidate in queue if candidate.get("status") in BLOCKING_QUEUE_STATUSES]
        if len(blocking) != 1 or blocking[0] is not item:
            raise ValueError("ACTIVE_BLOCKING_ITEM_MISMATCH")

        logical_id = compact_text(item.get("logicalSubmissionId"))
        if not logical_id or logical_id != compact_text(active.get("logical_submission_id")):
            raise ValueError("ACTIVE_LOGICAL_ID_MISMATCH")
        candidate_sha256 = self.queue_item_sha256(item)
        active_sha256 = compact_text(active.get("candidate_sha256")).lower()
        if not candidate_sha256 or candidate_sha256 != active_sha256:
            raise ValueError("ACTIVE_CANDIDATE_SHA256_MISMATCH")
        item_path = Path(compact_text(item.get("path"))).expanduser().resolve()
        if not item_path.is_file() or sha256_file(item_path) != candidate_sha256:
            raise ValueError("CANDIDATE_ARTIFACT_IDENTITY_CHANGED")
        if canonical_path(active.get("path")) != canonical_path(item_path):
            raise ValueError("ACTIVE_CANDIDATE_PATH_MISMATCH")

        submitted_at = compact_text(item.get("submittedAt"))
        accepted_at = compact_text(item.get("acceptedAt"))
        if not submitted_at or submitted_at != compact_text(active.get("submitted_at")):
            raise ValueError("ACTIVE_SUBMITTED_AT_MISMATCH")
        if not accepted_at or accepted_at != compact_text(active.get("accepted_at")):
            raise ValueError("ACTIVE_ACCEPTED_AT_MISMATCH")

        capture_window_key = compact_text(item.get("scoreCaptureWindowKey"))
        if not capture_window_key or capture_window_key != compact_text(active.get("score_capture_window_key")):
            raise ValueError("SCORE_CAPTURE_WINDOW_KEY_MISMATCH")
        claim_path = self.bounded_evidence_path(
            active.get("score_capture_claim_path"),
            self.score_capture_claims_dir,
            "SCORE_CAPTURE_CLAIM",
        )
        claim = read_json(claim_path, None)
        if not isinstance(claim, dict):
            raise ValueError("SCORE_CAPTURE_CLAIM_INVALID")
        claim_expected = {
            "key": capture_window_key,
            "queueIndex": queue_index,
            "logicalSubmissionId": logical_id,
            "candidateSha256": candidate_sha256,
            "acceptedAt": accepted_at,
        }
        if any(compact_text(claim.get(key)) != compact_text(value) for key, value in claim_expected.items()):
            raise ValueError("SCORE_CAPTURE_CLAIM_IDENTITY_MISMATCH")
        original_expected_publish_at = compact_text(claim.get("expectedPublishAt"))
        if parse_iso_datetime(original_expected_publish_at) is None:
            raise ValueError("SCORE_CAPTURE_CLAIM_EXPECTED_PUBLISH_AT_INVALID")
        active_expected_publish_at = compact_text(active.get("expected_publish_at"))
        if original_expected_publish_at != active_expected_publish_at:
            raise ValueError("SCORE_CAPTURE_CLAIM_EXPECTED_PUBLISH_AT_MISMATCH")
        expected_capture_window_key = (
            f"aicomp-score-window-v1|{queue_index}|{logical_id}|{candidate_sha256}|"
            f"{accepted_at}|{original_expected_publish_at}"
        )
        if capture_window_key != expected_capture_window_key:
            raise ValueError("SCORE_CAPTURE_WINDOW_KEY_CONTENT_MISMATCH")
        expected_claim_path = (
            self.score_capture_claims_dir
            / f"{hashlib.sha256(capture_window_key.encode('utf-8')).hexdigest()}.json"
        ).resolve()
        if claim_path != expected_claim_path:
            raise ValueError("SCORE_CAPTURE_CLAIM_PATH_IDENTITY_MISMATCH")

        return {
            "doc": doc,
            "queue": queue,
            "item": item,
            "active": active,
            "queue_index": queue_index,
            "file": item.get("name") or item_path.name,
            "path": str(item_path),
            "candidate_sha256": candidate_sha256,
            "logical_submission_id": logical_id,
            "submitted_at": submitted_at,
            "accepted_at": accepted_at,
            "capture_window_key": capture_window_key,
            "claim_path": str(claim_path),
            "claim_sha256": sha256_file(claim_path),
            "original_expected_publish_at": original_expected_publish_at,
            "queue_revision": self.semantic_queue_revision(queue, active),
            "queue_document_digest": f"sha256:{sha256_json(doc)}",
            "active_sha256": sha256_file(self.active_path),
            "results_revision": self.path_revision(self.results_path),
        }

    def read_results_rows(self) -> list[dict[str, str]]:
        if not self.results_path.is_file():
            return []
        with self.results_path.open(
            "r", encoding="utf-8-sig", errors="strict", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != RESULT_FIELDS:
                raise ValueError("RESULTS_SCHEMA_MISMATCH")
            return [dict(row) for row in reader]

    def result_row_matches_queue_item(
        self,
        row: dict,
        item: dict,
        *,
        allow_missing_submitted_at: bool,
    ) -> bool:
        if safe_int(row.get("file_index")) != safe_int(item.get("index")):
            return False
        if compact_text(row.get("file_name")) != compact_text(item.get("name")):
            return False
        if canonical_path(row.get("file_path")) != canonical_path(item.get("path")):
            return False

        row_submitted_text = compact_text(row.get("submitted_at"))
        if not row_submitted_text:
            return allow_missing_submitted_at
        row_submitted_at = parse_iso_datetime(row_submitted_text)
        if row_submitted_at is None:
            return False
        queue_times = {
            parsed
            for parsed in (
                parse_iso_datetime(item.get("submittedAt")),
                parse_iso_datetime(item.get("acceptedAt")),
            )
            if parsed is not None
        }
        return row_submitted_at in queue_times

    def legacy_scored_history_queue_identity(self, item: dict) -> dict:
        return {
            "queueIndex": safe_int(item.get("index")),
            "logicalSubmissionId": compact_text(item.get("logicalSubmissionId")),
            "fileName": compact_text(item.get("name")),
            "canonicalFilePath": canonical_path(item.get("path")),
            "submittedAt": compact_text(item.get("submittedAt")),
            "acceptedAt": compact_text(item.get("acceptedAt")),
            "score": compact_text(item.get("score")),
            "status": compact_text(item.get("status")),
            "artifactSha256": compact_text(item.get("artifactSha256")),
        }

    def legacy_scored_history_result_row_sha256(self, row: dict) -> str:
        normalized = {
            field: compact_text(row.get(field)) for field in RESULT_FIELDS
        }
        return sha256_json(normalized)

    def read_legacy_snapshot_log(self, path: Path) -> list[dict]:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise ValueError("LEGACY_SCORED_HISTORY_SOURCE_LOG_INVALID") from exc
        decoder = json.JSONDecoder()
        snapshots: list[dict] = []
        for match in re.finditer(r"(?m)^\{", text):
            try:
                value, _end = decoder.raw_decode(text, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                snapshots.append(value)
        if not snapshots:
            raise ValueError("LEGACY_SCORED_HISTORY_SOURCE_LOG_EMPTY")
        return snapshots

    def read_legacy_scored_history_compatibility(self) -> dict | None:
        manifest_path = self.legacy_scored_history_compatibility_manifest_path
        if not manifest_path.is_file() or not sidecar_path_for(manifest_path).is_file():
            return None
        try:
            payload, _summary = read_evidence_receipt(
                manifest_path,
                root=self.root,
                require_sidecar=True,
                verify_bound_files=True,
            )
        except EvidenceReceiptError:
            return None
        return self.validate_legacy_scored_history_compatibility_payload(payload)

    def validate_legacy_scored_history_compatibility_payload(
        self,
        payload: dict,
    ) -> dict | None:
        required_keys = {
            "schemaVersion",
            "contractVersion",
            "competitionScope",
            "teamId",
            "teamName",
            "publicLeaderboardUrl",
            "reviewedAt",
            "sourceLog",
            "entries",
        }
        if set(payload) != required_keys:
            return None
        if (
            payload.get("schemaVersion")
            != LEGACY_SCORED_HISTORY_COMPATIBILITY_SCHEMA
            or payload.get("contractVersion") != SUBMISSION_CONTRACT_VERSION
            or payload.get("competitionScope") != COMPETITION_SCOPE
            or payload.get("teamId") != TEAM_ID
            or payload.get("teamName") != TEAM_NAME
            or payload.get("publicLeaderboardUrl") != PUBLIC_LEADERBOARD_URL
            or parse_iso_datetime(payload.get("reviewedAt")) is None
        ):
            return None
        source_log = payload.get("sourceLog")
        if not isinstance(source_log, dict) or set(source_log) != {"path", "sha256"}:
            return None
        source_log_path = Path(compact_text(source_log.get("path"))).expanduser().resolve()
        if source_log_path != self.snapshot_log_path.resolve():
            return None
        try:
            source_log_sha256 = sha256_file(source_log_path)
        except OSError:
            return None
        if compact_text(source_log.get("sha256")) != source_log_sha256:
            return None
        entries = payload.get("entries")
        if not isinstance(entries, list) or not entries:
            return None
        entries_by_result_sha256: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "queueIdentity",
                "queue_identity_sha256",
                "result_row_sha256",
                "sourceEvidence",
                "source_evidence_sha256",
            }:
                return None
            identity = entry.get("queueIdentity")
            source_evidence = entry.get("sourceEvidence")
            if (
                not isinstance(identity, dict)
                or not isinstance(source_evidence, dict)
                or set(source_evidence)
                != {
                    "queryTime",
                    "leaderboardPublishTime",
                    "teamRank",
                    "teamId",
                    "teamName",
                    "teamSubmitTime",
                    "score",
                    "scoreTime",
                }
            ):
                return None
            identity_sha256 = compact_text(entry.get("queue_identity_sha256"))
            result_sha256 = compact_text(entry.get("result_row_sha256"))
            source_evidence_sha256 = compact_text(
                entry.get("source_evidence_sha256")
            )
            if (
                not re.fullmatch(r"[0-9a-f]{64}", identity_sha256)
                or identity_sha256 != sha256_json(identity)
                or not re.fullmatch(r"[0-9a-f]{64}", result_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", source_evidence_sha256)
                or source_evidence_sha256 != sha256_json(source_evidence)
                or parse_iso_datetime(source_evidence.get("queryTime")) is None
                or result_sha256 in entries_by_result_sha256
            ):
                return None
            entries_by_result_sha256[result_sha256] = entry
        try:
            snapshots = self.read_legacy_snapshot_log(source_log_path)
        except ValueError:
            return None
        snapshots_by_time: dict[str, list[dict]] = {}
        for snapshot in snapshots:
            snapshots_by_time.setdefault(compact_text(snapshot.get("time")), []).append(
                snapshot
            )
        return {
            "entriesByResultSha256": entries_by_result_sha256,
            "snapshotsByTime": snapshots_by_time,
        }

    def legacy_scored_history_compatibility_evidence(
        self,
        item: dict,
        row: dict,
        compatibility: dict | None,
    ) -> dict | None:
        if not compatibility:
            return None
        result_sha256 = self.legacy_scored_history_result_row_sha256(row)
        entry = compatibility["entriesByResultSha256"].get(result_sha256)
        if not isinstance(entry, dict):
            return None
        identity = self.legacy_scored_history_queue_identity(item)
        source_evidence = entry.get("sourceEvidence")
        if not isinstance(source_evidence, dict):
            return None
        if (
            entry.get("queueIdentity") != identity
            or compact_text(entry.get("queue_identity_sha256"))
            != sha256_json(identity)
            or compact_text(entry.get("source_evidence_sha256"))
            != sha256_json(source_evidence)
            or canonical_path(row.get("snapshot_path"))
            != canonical_path(self.snapshot_log_path)
        ):
            return None
        row_query_at = parse_iso_datetime(row.get("query_time"))
        source_query_at = parse_iso_datetime(source_evidence.get("queryTime"))
        row_publish_at = parse_leaderboard_publish_datetime(
            row.get("leaderboard_publish_time"), row.get("team_submit_time")
        )
        source_publish_at = parse_leaderboard_publish_datetime(
            source_evidence.get("leaderboardPublishTime"),
            source_evidence.get("teamSubmitTime"),
        )
        if (
            row_query_at is None
            or source_query_at is None
            or row_publish_at is None
            or source_publish_at is None
            or compact_text(source_evidence.get("teamId")) != TEAM_ID
            or compact_text(source_evidence.get("teamName")) != TEAM_NAME
            or compact_text(source_evidence.get("teamSubmitTime"))
            != compact_text(row.get("team_submit_time"))
            or compact_text(source_evidence.get("scoreTime"))
            != compact_text(row.get("score_time"))
            or safe_int(source_evidence.get("teamRank"))
            != safe_int(row.get("team_rank"))
        ):
            return None
        source_score = score_as_float(source_evidence.get("score"))
        row_score = score_as_float(row.get("score"))
        if (
            source_score is None
            or row_score is None
            or not math.isclose(
                source_score, row_score, rel_tol=0.0, abs_tol=1e-12
            )
        ):
            return None
        snapshots = compatibility["snapshotsByTime"].get(
            compact_text(source_evidence.get("queryTime")), []
        )
        matching_snapshots = []
        for snapshot in snapshots:
            if not is_exact_public_leaderboard_url(snapshot.get("url")):
                continue
            parsed = parse_team_result(snapshot)
            parsed_evidence = {
                "queryTime": compact_text(snapshot.get("time")),
                "leaderboardPublishTime": compact_text(
                    parsed.get("leaderboardPublishTime")
                ),
                "teamRank": compact_text(parsed.get("rank")),
                "teamId": compact_text(parsed.get("teamId")),
                "teamName": compact_text(parsed.get("teamName")),
                "teamSubmitTime": compact_text(parsed.get("teamSubmitTime")),
                "score": compact_text(parsed.get("score")),
                "scoreTime": compact_text(parsed.get("scoreTime")),
            }
            if parsed_evidence != source_evidence:
                continue
            matching_snapshots.append(snapshot)
        if len(matching_snapshots) != 1:
            return None
        return {
            "queriedAt": source_query_at,
            "publishedAt": source_publish_at,
            "teamSubmitAt": parse_team_submit_datetime(
                source_evidence.get("teamSubmitTime")
            ),
            "scoreAt": parse_team_submit_datetime(source_evidence.get("scoreTime")),
        }

    def legacy_scored_history_compatibility_matches(
        self,
        item: dict,
        row: dict,
        compatibility: dict | None,
    ) -> bool:
        return (
            self.legacy_scored_history_compatibility_evidence(
                item, row, compatibility
            )
            is not None
        )

    def legacy_scored_history_proof(
        self,
        item: dict,
        result_rows: list[dict[str, str]],
        target_at: dt.datetime,
        compatibility: dict | None,
    ) -> dict | None:
        """Return a conservative pre-target upper bound for a legacy scored item.

        Queue schema migrations left the first historical rows without acceptedAt.
        They are safe for later-publication ordering only when a bound public-score
        result proves that the same queue identity was already scored before the
        target submission.  A blank field is eligible for this compatibility path;
        malformed non-empty timestamps remain corruption.
        """

        queue_index = safe_int(item.get("index"))
        if queue_index is None or compact_text(item.get("status")) != "scored":
            return None
        expected_logical_id = (
            f"{COMPETITION_SCOPE}:{TEAM_ID}:legacy-index-{queue_index}"
        )
        if compact_text(item.get("logicalSubmissionId")) != expected_logical_id:
            return None
        item_submitted_at = parse_iso_datetime(item.get("submittedAt"))
        item_score = score_as_float(item.get("score"))
        if (
            item_submitted_at is None
            or item_submitted_at >= target_at
            or item_score is None
            or not math.isfinite(item_score)
        ):
            return None

        proofs: list[dict[str, str]] = []
        for row in result_rows:
            if compact_text(row.get("accepted_at")):
                continue
            if not self.result_row_matches_queue_item(
                row, item, allow_missing_submitted_at=False
            ):
                continue
            row_score = score_as_float(row.get("score"))
            if (
                row_score is None
                or not math.isfinite(row_score)
                or not math.isclose(row_score, item_score, rel_tol=0.0, abs_tol=1e-12)
            ):
                continue
            if compact_text(row.get("team_id")) != TEAM_ID:
                continue
            if compact_text(row.get("team_name")) != TEAM_NAME:
                continue
            rank = safe_int(row.get("team_rank"))
            if rank is None or rank <= 0:
                continue
            if not compact_text(row.get("snapshot_path")):
                continue
            compatibility_evidence = (
                self.legacy_scored_history_compatibility_evidence(
                item, row, compatibility
                )
            )
            if compatibility_evidence is None:
                continue

            row_queried_at = parse_iso_datetime(row.get("query_time"))
            row_team_submit_at = parse_team_submit_datetime(
                row.get("team_submit_time")
            )
            row_score_at = parse_team_submit_datetime(row.get("score_time"))
            row_published_at = parse_leaderboard_publish_datetime(
                row.get("leaderboard_publish_time"), row.get("team_submit_time")
            )
            if any(
                value is None
                for value in (
                    row_queried_at,
                    row_team_submit_at,
                    row_score_at,
                    row_published_at,
                    compatibility_evidence.get("queriedAt"),
                    compatibility_evidence.get("teamSubmitAt"),
                    compatibility_evidence.get("scoreAt"),
                    compatibility_evidence.get("publishedAt"),
                )
            ):
                continue
            queried_at = compatibility_evidence["queriedAt"]
            team_submit_at = compatibility_evidence["teamSubmitAt"]
            score_at = compatibility_evidence["scoreAt"]
            published_at = compatibility_evidence["publishedAt"]
            assert row_queried_at is not None
            assert row_team_submit_at is not None
            assert row_score_at is not None
            assert row_published_at is not None
            if not (
                team_submit_at <= score_at <= published_at <= queried_at < target_at
                and item_submitted_at <= published_at <= queried_at
                and row_team_submit_at
                <= row_score_at
                <= row_published_at
                <= row_queried_at
                < target_at
                and item_submitted_at <= row_published_at <= row_queried_at
            ):
                continue
            proofs.append(
                (
                    queried_at,
                    {
                    "observedBeforeAt": queried_at.isoformat().replace("+00:00", "Z"),
                    "scoreTime": score_at.isoformat().replace("+00:00", "Z"),
                    "publishTime": published_at.isoformat().replace("+00:00", "Z"),
                    },
                )
            )

        if not proofs:
            return None
        return max(proofs, key=lambda proof: proof[0])[1]

    def legacy_accepted_time_historical_queue_item(self, item: dict) -> dict:
        return {
            "queueIndex": safe_int(item.get("index")),
            "fileName": compact_text(item.get("name")),
            "canonicalFilePath": canonical_path(item.get("path")),
            "size": safe_int(item.get("size")),
            "status": compact_text(item.get("status")),
            "submittedAt": compact_text(item.get("submittedAt")),
        }

    def read_legacy_accepted_time_compatibility(self) -> dict | None:
        manifest_path = self.legacy_accepted_time_compatibility_manifest_path
        if not manifest_path.is_file() or not sidecar_path_for(manifest_path).is_file():
            return None
        try:
            payload, _summary = read_evidence_receipt(
                manifest_path,
                root=self.root,
                require_sidecar=True,
                verify_bound_files=True,
            )
        except EvidenceReceiptError:
            return None
        return self.validate_legacy_accepted_time_compatibility_payload(payload)

    def validate_legacy_accepted_time_compatibility_payload(
        self,
        payload: dict,
    ) -> dict | None:
        """Validate a narrow submit-log + Git proof for one missing acceptedAt.

        This proof establishes only a historical acceptance timestamp.  It does
        not make a score attributable and cannot authorize a submit/capture
        replay.  Every source file and the pre-submit Git blob are revalidated.
        """

        required_keys = {
            "schemaVersion",
            "contractVersion",
            "competitionScope",
            "teamId",
            "teamName",
            "publicLeaderboardUrl",
            "reviewedAt",
            "entries",
        }
        if not isinstance(payload, dict) or set(payload) != required_keys:
            return None
        if (
            payload.get("schemaVersion")
            != LEGACY_ACCEPTED_TIME_COMPATIBILITY_SCHEMA
            or payload.get("contractVersion") != SUBMISSION_CONTRACT_VERSION
            or payload.get("competitionScope") != COMPETITION_SCOPE
            or payload.get("teamId") != TEAM_ID
            or payload.get("teamName") != TEAM_NAME
            or payload.get("publicLeaderboardUrl") != PUBLIC_LEADERBOARD_URL
            or parse_iso_datetime(payload.get("reviewedAt")) is None
        ):
            return None
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) != 1:
            return None

        try:
            queue = self.read_queue_doc()["queue"]
            result_rows = self.read_results_rows()
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None

        entries_by_queue_index: dict[int, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "proofKind",
                "queueIdentity",
                "queue_identity_sha256",
                "resultsPath",
                "resultRow",
                "result_row_sha256",
                "candidate",
                "runnerLog",
                "historicalQueueSnapshots",
                "gitEvidence",
                "acceptedTimeEvidence",
            }:
                return None
            if entry.get("proofKind") != "submit-log-git-v1":
                return None

            identity = entry.get("queueIdentity")
            result_row = entry.get("resultRow")
            candidate = entry.get("candidate")
            runner_log = entry.get("runnerLog")
            snapshots = entry.get("historicalQueueSnapshots")
            git_evidence = entry.get("gitEvidence")
            accepted_evidence = entry.get("acceptedTimeEvidence")
            if (
                not isinstance(identity, dict)
                or not isinstance(result_row, dict)
                or not isinstance(candidate, dict)
                or not isinstance(runner_log, dict)
                or not isinstance(snapshots, list)
                or not isinstance(git_evidence, dict)
                or not isinstance(accepted_evidence, dict)
            ):
                return None
            if (
                set(identity)
                != set(self.legacy_scored_history_queue_identity({}))
                or set(result_row) != set(RESULT_FIELDS)
                or set(candidate) != {"path", "sha256", "size"}
                or set(runner_log)
                != {"path", "sha256", "uploadedFileName"}
                or len(snapshots) != 2
                or set(git_evidence)
                != {
                    "repositoryPath",
                    "commit",
                    "commitTime",
                    "relativePath",
                    "blobId",
                    "blobSize",
                    "blob_sha256",
                }
                or set(accepted_evidence)
                != {
                    "submittedAt",
                    "teamSubmitTime",
                    "acceptedAt",
                    "scoreTime",
                    "publishTime",
                    "queryTime",
                    "maxPlatformDeltaSeconds",
                }
            ):
                return None

            queue_index = safe_int(identity.get("queueIndex"))
            if queue_index is None or queue_index in entries_by_queue_index:
                return None
            live_matches = [
                item
                for item in queue
                if safe_int(item.get("index")) == queue_index
            ]
            if len(live_matches) != 1:
                return None
            live_item = live_matches[0]
            live_identity = self.legacy_scored_history_queue_identity(live_item)
            if (
                identity != live_identity
                or compact_text(entry.get("queue_identity_sha256"))
                != sha256_json(identity)
                or compact_text(identity.get("acceptedAt"))
                or compact_text(identity.get("status")) != "scored"
                or compact_text(identity.get("logicalSubmissionId"))
                != f"{COMPETITION_SCOPE}:{TEAM_ID}:legacy-index-{queue_index}"
            ):
                return None

            normalized_row = {
                field: compact_text(result_row.get(field)) for field in RESULT_FIELDS
            }
            if normalized_row != result_row:
                return None
            result_row_sha256 = compact_text(entry.get("result_row_sha256"))
            if (
                result_row_sha256 != sha256_json(result_row)
                or canonical_path(entry.get("resultsPath"))
                != canonical_path(self.results_path)
            ):
                return None
            matching_rows = [
                row
                for row in result_rows
                if self.legacy_scored_history_result_row_sha256(row)
                == result_row_sha256
            ]
            if len(matching_rows) != 1:
                return None
            live_row = matching_rows[0]
            if (
                compact_text(live_row.get("accepted_at"))
                or not self.result_row_matches_queue_item(
                    live_row, live_item, allow_missing_submitted_at=False
                )
                or compact_text(live_row.get("team_id")) != TEAM_ID
                or compact_text(live_row.get("team_name")) != TEAM_NAME
                or safe_int(live_row.get("team_rank")) is None
                or (safe_int(live_row.get("team_rank")) or 0) <= 0
                or canonical_path(live_row.get("snapshot_path"))
                != canonical_path(self.snapshot_log_path)
            ):
                return None
            live_score = score_as_float(live_row.get("score"))
            queue_score = score_as_float(live_item.get("score"))
            if (
                live_score is None
                or queue_score is None
                or not math.isfinite(live_score)
                or not math.isclose(
                    live_score, queue_score, rel_tol=0.0, abs_tol=1e-12
                )
            ):
                return None

            candidate_path = Path(compact_text(candidate.get("path"))).expanduser().resolve()
            if (
                not candidate_path.is_file()
                or not candidate_path.is_relative_to(self.submissions.resolve())
                or canonical_path(candidate_path)
                != canonical_path(live_item.get("path"))
                or compact_text(candidate.get("sha256"))
                != compact_text(live_item.get("artifactSha256"))
                or compact_text(candidate.get("sha256"))
                != sha256_file(candidate_path)
                or safe_int(candidate.get("size")) != candidate_path.stat().st_size
                or safe_int(candidate.get("size")) != safe_int(live_item.get("size"))
            ):
                return None

            runner_log_path = Path(
                compact_text(runner_log.get("path"))
            ).expanduser().resolve()
            if (
                not runner_log_path.is_file()
                or not runner_log_path.is_relative_to(self.submissions.resolve())
                or compact_text(runner_log.get("sha256"))
                != sha256_file(runner_log_path)
            ):
                return None
            try:
                runner_lines = runner_log_path.read_text(
                    encoding="utf-8", errors="strict"
                ).splitlines()
            except (OSError, UnicodeError):
                return None
            submitted_text = compact_text(live_item.get("submittedAt"))
            selected_marker = f"set file input: true {candidate_path}"
            clicked_marker = f"SUBMIT_CLICKED_AT={submitted_text}"
            selected_indexes = [
                index
                for index, line in enumerate(runner_lines)
                if line.strip() == selected_marker
            ]
            clicked_indexes = [
                index
                for index, line in enumerate(runner_lines)
                if line.strip() == clicked_marker
            ]
            if len(selected_indexes) != 1 or len(clicked_indexes) != 1:
                return None
            selected_index = selected_indexes[0]
            clicked_index = clicked_indexes[0]
            next_selection = next(
                (
                    index
                    for index in range(selected_index + 1, len(runner_lines))
                    if runner_lines[index].startswith("set file input: true ")
                ),
                len(runner_lines),
            )
            if not (selected_index < clicked_index < next_selection):
                return None
            segment = runner_lines[selected_index + 1 : clicked_index]
            if sum(line.startswith("SUBMIT_CLICKED_AT=") for line in segment) != 0:
                return None
            uploaded_name = compact_text(runner_log.get("uploadedFileName"))
            if (
                not uploaded_name
                or Path(uploaded_name).name != uploaded_name
                or PUBLIC_LEADERBOARD_URL not in "\n".join(runner_lines[:selected_index])
            ):
                return None

            def upload_state_matches(line: str, prefix: str) -> bool:
                if not line.startswith(prefix):
                    return False
                try:
                    value = json.loads(line[len(prefix) :].strip())
                except json.JSONDecodeError:
                    return False
                states = value.get("state") if isinstance(value, dict) else value
                if not isinstance(states, list):
                    return False
                ready = [
                    state
                    for state in states
                    if isinstance(state, dict)
                    and state.get("ready") is True
                    and compact_text(state.get("text")).startswith(
                        uploaded_name + " "
                    )
                ]
                return len(ready) == 1

            ready_indexes = [
                selected_index + 1 + offset
                for offset, line in enumerate(segment)
                if upload_state_matches(line, "upload ready: ")
            ]
            settled_indexes = [
                selected_index + 1 + offset
                for offset, line in enumerate(segment)
                if upload_state_matches(line, "upload state after settle: ")
            ]
            if (
                len(ready_indexes) != 1
                or len(settled_indexes) != 1
                or not (
                    selected_index
                    < ready_indexes[0]
                    < settled_indexes[0]
                    < clicked_index
                )
            ):
                return None

            submitted_at = parse_iso_datetime(submitted_text)
            snapshot_times: list[dt.datetime] = []
            seen_snapshot_paths: set[str] = set()
            seen_snapshot_sha256: set[str] = set()
            for snapshot in snapshots:
                if not isinstance(snapshot, dict) or set(snapshot) != {
                    "path",
                    "sha256",
                    "updatedAt",
                    "queueItem",
                    "queue_item_sha256",
                }:
                    return None
                snapshot_path = Path(
                    compact_text(snapshot.get("path"))
                ).expanduser().resolve()
                path_identity = canonical_path(snapshot_path)
                snapshot_sha256 = compact_text(snapshot.get("sha256"))
                if (
                    path_identity in seen_snapshot_paths
                    or snapshot_sha256 in seen_snapshot_sha256
                    or not snapshot_path.is_file()
                    or not snapshot_path.is_relative_to(self.submissions.resolve())
                    or snapshot_sha256 != sha256_file(snapshot_path)
                ):
                    return None
                seen_snapshot_paths.add(path_identity)
                seen_snapshot_sha256.add(snapshot_sha256)
                try:
                    snapshot_doc = json.loads(snapshot_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    return None
                snapshot_queue = (
                    snapshot_doc.get("queue") if isinstance(snapshot_doc, dict) else None
                )
                if not isinstance(snapshot_queue, list):
                    return None
                historical_matches = [
                    item
                    for item in snapshot_queue
                    if safe_int(item.get("index")) == queue_index
                ]
                if len(historical_matches) != 1:
                    return None
                historical_identity = self.legacy_accepted_time_historical_queue_item(
                    historical_matches[0]
                )
                if (
                    snapshot.get("queueItem") != historical_identity
                    or compact_text(snapshot.get("queue_item_sha256"))
                    != sha256_json(historical_identity)
                    or historical_identity.get("fileName")
                    != identity.get("fileName")
                    or historical_identity.get("canonicalFilePath")
                    != identity.get("canonicalFilePath")
                    or historical_identity.get("size")
                    != safe_int(candidate.get("size"))
                    or historical_identity.get("status") != "awaiting_refresh"
                    or historical_identity.get("submittedAt") != submitted_text
                    or compact_text(snapshot_doc.get("updatedAt"))
                    != compact_text(snapshot.get("updatedAt"))
                ):
                    return None
                snapshot_at = parse_iso_datetime(snapshot.get("updatedAt"))
                if snapshot_at is None:
                    return None
                snapshot_times.append(snapshot_at)
            if (
                submitted_at is None
                or snapshot_times != sorted(snapshot_times)
                or len(set(snapshot_times)) != len(snapshot_times)
                or any(snapshot_at < submitted_at for snapshot_at in snapshot_times)
            ):
                return None

            repository_path = Path(
                compact_text(git_evidence.get("repositoryPath"))
            ).expanduser().resolve()
            relative_path_text = compact_text(git_evidence.get("relativePath"))
            relative_path = Path(relative_path_text)
            commit = compact_text(git_evidence.get("commit"))
            blob_id = compact_text(git_evidence.get("blobId"))
            if (
                repository_path != self.root.resolve()
                or relative_path.is_absolute()
                or ".." in relative_path.parts
                or not relative_path_text
                or (repository_path / relative_path).resolve() != candidate_path
                or not re.fullmatch(r"[0-9a-f]{40}", commit)
                or not re.fullmatch(r"[0-9a-f]{40}", blob_id)
                or not re.fullmatch(
                    r"[0-9a-f]{64}", compact_text(git_evidence.get("blob_sha256"))
                )
            ):
                return None

            git_env = dict(os.environ)
            git_env["GIT_OPTIONAL_LOCKS"] = "0"

            def run_git(*args: str, binary: bool = False) -> bytes | str | None:
                try:
                    completed = subprocess.run(
                        [
                            "git",
                            "--no-optional-locks",
                            "-C",
                            str(repository_path),
                            *args,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=15,
                        env=git_env,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    return None
                if completed.returncode != 0:
                    return None
                if binary:
                    return completed.stdout
                try:
                    return completed.stdout.decode("utf-8", errors="strict")
                except UnicodeError:
                    return None

            commit_output = run_git("show", "-s", "--format=%H%n%cI", commit)
            tree_output = run_git("ls-tree", commit, "--", relative_path_text)
            blob_bytes = run_git("show", f"{commit}:{relative_path_text}", binary=True)
            if (
                not isinstance(commit_output, str)
                or not isinstance(tree_output, str)
                or not isinstance(blob_bytes, bytes)
            ):
                return None
            commit_lines = commit_output.strip().splitlines()
            tree_lines = tree_output.strip().splitlines()
            if len(commit_lines) != 2 or len(tree_lines) != 1:
                return None
            tree_match = re.fullmatch(
                r"\d{6} blob ([0-9a-f]{40})\t(.+)", tree_lines[0]
            )
            if (
                tree_match is None
                or commit_lines[0] != commit
                or commit_lines[1] != compact_text(git_evidence.get("commitTime"))
                or tree_match.group(1) != blob_id
                or tree_match.group(2).replace("\\", "/")
                != relative_path_text.replace("\\", "/")
                or len(blob_bytes) != safe_int(git_evidence.get("blobSize"))
                or len(blob_bytes) != candidate_path.stat().st_size
                or hashlib.sha256(blob_bytes).hexdigest()
                != compact_text(git_evidence.get("blob_sha256"))
                or hashlib.sha256(blob_bytes).hexdigest()
                != compact_text(candidate.get("sha256"))
            ):
                return None
            commit_at = parse_iso_datetime(commit_lines[1])

            team_submit_at = parse_team_submit_datetime(
                result_row.get("team_submit_time")
            )
            score_at = parse_team_submit_datetime(result_row.get("score_time"))
            publish_at = parse_leaderboard_publish_datetime(
                result_row.get("leaderboard_publish_time"),
                result_row.get("team_submit_time"),
            )
            query_at = parse_iso_datetime(result_row.get("query_time"))
            accepted_at = parse_iso_datetime(accepted_evidence.get("acceptedAt"))
            max_delta = safe_int(accepted_evidence.get("maxPlatformDeltaSeconds"))
            if any(
                value is None
                for value in (
                    submitted_at,
                    commit_at,
                    team_submit_at,
                    score_at,
                    publish_at,
                    query_at,
                    accepted_at,
                )
            ):
                return None
            assert submitted_at is not None
            assert commit_at is not None
            assert team_submit_at is not None
            assert score_at is not None
            assert publish_at is not None
            assert query_at is not None
            assert accepted_at is not None
            if (
                max_delta != LEGACY_ACCEPTED_TIME_MAX_PLATFORM_DELTA_SECONDS
                or accepted_evidence.get("submittedAt") != submitted_text
                or accepted_evidence.get("teamSubmitTime")
                != result_row.get("team_submit_time")
                or accepted_evidence.get("acceptedAt")
                != team_submit_at.isoformat().replace("+00:00", "Z")
                or accepted_evidence.get("scoreTime")
                != score_at.isoformat().replace("+00:00", "Z")
                or accepted_evidence.get("publishTime")
                != publish_at.isoformat().replace("+00:00", "Z")
                or accepted_evidence.get("queryTime")
                != query_at.isoformat().replace("+00:00", "Z")
                or commit_at >= submitted_at
                or abs(team_submit_at - submitted_at)
                > dt.timedelta(seconds=max_delta)
                or not (
                    team_submit_at
                    <= score_at
                    <= publish_at
                    <= query_at
                )
                or snapshot_times[-1] > publish_at
                or accepted_at != team_submit_at
            ):
                return None

            entries_by_queue_index[queue_index] = {
                "entry": entry,
                "acceptedAt": accepted_at,
                "scoreAt": score_at,
                "publishAt": publish_at,
                "queryAt": query_at,
                "latestHistoricalQueueAt": snapshot_times[-1],
            }
        return {"entriesByQueueIndex": entries_by_queue_index}

    def legacy_accepted_time_proof(
        self,
        item: dict,
        result_rows: list[dict[str, str]],
        target_at: dt.datetime,
        compatibility: dict | None,
    ) -> dict | None:
        if not compatibility:
            return None
        queue_index = safe_int(item.get("index"))
        if queue_index is None:
            return None
        proof = compatibility.get("entriesByQueueIndex", {}).get(queue_index)
        if not isinstance(proof, dict):
            return None
        entry = proof.get("entry")
        if (
            not isinstance(entry, dict)
            or entry.get("queueIdentity")
            != self.legacy_scored_history_queue_identity(item)
        ):
            return None
        result_sha256 = compact_text(entry.get("result_row_sha256"))
        matching_rows = [
            row
            for row in result_rows
            if self.legacy_scored_history_result_row_sha256(row) == result_sha256
        ]
        if len(matching_rows) != 1:
            return None
        accepted_at = proof.get("acceptedAt")
        score_at = proof.get("scoreAt")
        publish_at = proof.get("publishAt")
        query_at = proof.get("queryAt")
        latest_historical_at = proof.get("latestHistoricalQueueAt")
        if (
            not isinstance(accepted_at, dt.datetime)
            or not isinstance(score_at, dt.datetime)
            or not isinstance(publish_at, dt.datetime)
            or not isinstance(query_at, dt.datetime)
            or not isinstance(latest_historical_at, dt.datetime)
            or not (
                accepted_at
                <= score_at
                <= publish_at
                <= query_at
                < target_at
            )
            or latest_historical_at >= target_at
        ):
            return None
        return {
            "source": "queue_legacy_submit_log_git_proof",
            "observedBeforeAt": accepted_at.isoformat().replace("+00:00", "Z"),
            "scoreTime": score_at.isoformat().replace("+00:00", "Z"),
            "publishTime": publish_at.isoformat().replace("+00:00", "Z"),
            "evidenceObservedAt": query_at.isoformat().replace("+00:00", "Z"),
        }

    def accepted_submission_history(self, binding: dict) -> dict:
        target_at = parse_iso_datetime(binding.get("accepted_at"))
        if target_at is None:
            raise ValueError("TARGET_ACCEPTED_AT_INVALID")
        result_rows = self.read_results_rows()
        legacy_compatibility = self.read_legacy_scored_history_compatibility()
        legacy_accepted_time_compatibility = (
            self.read_legacy_accepted_time_compatibility()
        )
        queue_by_index: dict[int, dict] = {}
        for item in binding["queue"]:
            queue_index = safe_int(item.get("index"))
            if queue_index is None:
                continue
            if queue_index in queue_by_index:
                raise ValueError("PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT")
            queue_by_index[queue_index] = item

        entries: list[dict[str, Any]] = []
        legacy_proofs: dict[int, dict] = {}
        for item in binding["queue"]:
            accepted_text = compact_text(item.get("acceptedAt"))
            accepted_at = parse_iso_datetime(accepted_text)
            if accepted_at is None:
                status = compact_text(item.get("status") or "queued")
                semantically_accepted = bool(
                    accepted_text
                    or status in {"accepted", "awaiting_score", "score_missed", "scored"}
                    or compact_text(item.get("score"))
                )
                if not semantically_accepted:
                    continue
                submitted_text = compact_text(item.get("submittedAt"))
                submitted_at = parse_iso_datetime(submitted_text)
                suffix = (
                    "_LATER_SUBMISSION"
                    if submitted_at is not None and submitted_at > target_at
                    else ""
                )
                if accepted_text:
                    raise ValueError(
                        f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                    )
                proof = self.legacy_scored_history_proof(
                    item, result_rows, target_at, legacy_compatibility
                )
                if proof is None:
                    proof = self.legacy_accepted_time_proof(
                        item,
                        result_rows,
                        target_at,
                        legacy_accepted_time_compatibility,
                    )
                if proof is None:
                    raise ValueError(
                        f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                    )
                queue_index = safe_int(item.get("index"))
                assert queue_index is not None
                legacy_proofs[queue_index] = proof
                accepted_at = parse_iso_datetime(proof["observedBeforeAt"])
                assert accepted_at is not None
            entries.append(
                {
                    "source": (
                        compact_text(
                            legacy_proofs[safe_int(item.get("index"))].get("source")
                        )
                        if safe_int(item.get("index")) in legacy_proofs
                        else "queue"
                    ),
                    "queueIndex": safe_int(item.get("index")),
                    "logicalSubmissionId": compact_text(item.get("logicalSubmissionId")),
                    "acceptedAt": accepted_at.isoformat().replace("+00:00", "Z"),
                }
            )
        for row in result_rows:
            accepted_text = compact_text(row.get("accepted_at"))
            accepted_at = parse_iso_datetime(accepted_text)
            row_score = score_as_float(row.get("score"))
            semantically_accepted = bool(
                accepted_text
                or row_score is not None
                or compact_text(row.get("team_submit_time"))
                or compact_text(row.get("score_time"))
            )
            queue_index = safe_int(row.get("file_index"))
            item = queue_by_index.get(queue_index) if queue_index is not None else None
            if semantically_accepted and (
                item is None
                or not self.result_row_matches_queue_item(
                    row,
                    item,
                    allow_missing_submitted_at=(
                        parse_iso_datetime(item.get("acceptedAt")) is not None
                        if item is not None
                        else False
                    ),
                )
            ):
                row_submitted_at = parse_iso_datetime(row.get("submitted_at"))
                suffix = (
                    "_LATER_SUBMISSION"
                    if (
                        accepted_at is not None and accepted_at >= target_at
                    )
                    or (
                        row_submitted_at is not None
                        and row_submitted_at > target_at
                    )
                    else ""
                )
                raise ValueError(
                    f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                )
            if accepted_at is None:
                submitted_at = parse_iso_datetime(row.get("submitted_at"))
                suffix = (
                    "_LATER_SUBMISSION"
                    if submitted_at is not None and submitted_at > target_at
                    else ""
                )
                if accepted_text:
                    raise ValueError(
                        f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                    )
                if item is None or not self.result_row_matches_queue_item(
                    row,
                    item,
                    allow_missing_submitted_at=(
                        parse_iso_datetime(item.get("acceptedAt")) is not None
                    ),
                ):
                    raise ValueError(
                        f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                    )
                if not semantically_accepted:
                    continue
                accepted_at = parse_iso_datetime(item.get("acceptedAt"))
                if accepted_at is None:
                    proof = legacy_proofs.get(queue_index)
                    if proof is None:
                        raise ValueError(
                            f"PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT{suffix}"
                        )
                    accepted_at = parse_iso_datetime(proof["observedBeforeAt"])
                    assert accepted_at is not None
            entries.append(
                {
                    "source": "results",
                    "queueIndex": safe_int(row.get("file_index")),
                    "logicalSubmissionId": compact_text(
                        item.get("logicalSubmissionId") if item else ""
                    ),
                    "acceptedAt": accepted_at.isoformat().replace("+00:00", "Z"),
                }
            )
        unique = {
            (
                compact_text(entry.get("source")),
                safe_int(entry.get("queueIndex")),
                compact_text(entry.get("logicalSubmissionId")),
                compact_text(entry.get("acceptedAt")),
            ): entry
            for entry in entries
        }
        entries = sorted(
            unique.values(),
            key=lambda entry: (
                compact_text(entry.get("acceptedAt")),
                compact_text(entry.get("source")),
                safe_int(entry.get("queueIndex")) or -1,
                compact_text(entry.get("logicalSubmissionId")),
            ),
        )
        target_queue_index = safe_int(binding.get("queue_index"))
        target_logical_id = compact_text(binding.get("logical_submission_id"))
        later = [
            entry
            for entry in entries
            if (parse_iso_datetime(entry.get("acceptedAt")) or target_at) >= target_at
            and not (
                safe_int(entry.get("queueIndex")) == target_queue_index
                and compact_text(entry.get("logicalSubmissionId"))
                == target_logical_id
            )
        ]
        latest = max(
            (parse_iso_datetime(entry.get("acceptedAt")) for entry in entries),
            default=target_at,
        )
        return {
            "digest": f"sha256:{sha256_json(entries)}",
            "count": len(entries),
            "laterCount": len(later),
            "latestAcceptedAt": latest.isoformat().replace("+00:00", "Z") if latest else "",
        }

    def public_leaderboard_recovery_intent(self) -> dict:
        binding = self.current_score_missed_binding()
        history = self.accepted_submission_history(binding)
        identity = {
            "queueIndex": binding["queue_index"],
            "logicalSubmissionId": binding["logical_submission_id"],
            "candidateSha256": binding["candidate_sha256"],
            "acceptedAt": binding["accepted_at"],
            "originalCaptureWindowKey": binding["capture_window_key"],
        }
        recovery_key = sha256_json(
            {"action": PUBLIC_LEADERBOARD_RECOVERY_ACTION, **identity}
        )
        recovery_claim_path = (
            self.public_leaderboard_recovery_claims_dir / f"{recovery_key}.json"
        )
        recovery_consumption_path = (
            self.public_leaderboard_recovery_consumptions_dir
            / f"{recovery_key}.json"
        )
        intent = {
            "action": PUBLIC_LEADERBOARD_RECOVERY_ACTION,
            "contractVersion": SUBMISSION_CONTRACT_VERSION,
            "queueSchemaVersion": QUEUE_SCHEMA_VERSION,
            "queueIndex": binding["queue_index"],
            "logicalSubmissionId": binding["logical_submission_id"],
            "candidateSha256": binding["candidate_sha256"],
            "fileName": binding["file"],
            "candidatePath": binding["path"],
            "submittedAt": binding["submitted_at"],
            "acceptedAt": binding["accepted_at"],
            "originalCaptureWindowKey": binding["capture_window_key"],
            "originalClaimPath": binding["claim_path"],
            "originalClaimSha256": binding["claim_sha256"],
            "originalExpectedPublishAt": binding["original_expected_publish_at"],
            "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
            "publicLeaderboardApiUrl": PUBLIC_LEADERBOARD_API_URL,
            "leaderboardPageId": PUBLIC_LEADERBOARD_PAGE_ID,
            "leaderboardRwId": PUBLIC_LEADERBOARD_RW_ID,
            "leaderboardStageId": PUBLIC_LEADERBOARD_STAGE_ID,
            "teamId": TEAM_ID,
            "teamName": TEAM_NAME,
            "acceptedHistoryDigest": history["digest"],
            "acceptedHistoryCount": history["count"],
            "laterAcceptedSubmissionCount": history["laterCount"],
            "latestAcceptedAt": history["latestAcceptedAt"],
            "queueRevision": binding["queue_revision"],
            "queueDocumentDigest": binding["queue_document_digest"],
            "activeSha256": binding["active_sha256"],
            "resultsRevision": binding["results_revision"],
            "recoveryKey": recovery_key,
            "recoveryClaimPath": str(recovery_claim_path),
            "recoveryConsumptionPath": str(recovery_consumption_path),
        }
        intent["intentDigest"] = f"sha256:{sha256_json(intent)}"
        return intent

    def validate_public_leaderboard_recovery_intent_digest(self, intent: dict) -> None:
        digest = compact_text(intent.get("intentDigest"))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_INTENT_DIGEST_INVALID")
        unsigned = dict(intent)
        unsigned.pop("intentDigest", None)
        if digest != f"sha256:{sha256_json(unsigned)}":
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_INTENT_DIGEST_MISMATCH")

    def public_leaderboard_recovery_consumption_path(self, intent: dict) -> Path:
        path = Path(compact_text(intent.get("recoveryConsumptionPath"))).expanduser().resolve()
        expected = (
            self.public_leaderboard_recovery_consumptions_dir
            / f"{compact_text(intent.get('recoveryKey'))}.json"
        ).resolve()
        if path != expected:
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_PATH_MISMATCH")
        return path

    def public_leaderboard_recovery_consumption_payload(
        self,
        intent: dict,
        consumed_at: str,
    ) -> dict:
        return {
            "schemaVersion": 1,
            "state": "consumed",
            "consumedAt": consumed_at,
            "recoveryKey": compact_text(intent.get("recoveryKey")),
            "recoveryIntentDigest": compact_text(intent.get("intentDigest")),
            "recoveryIntent": intent,
            "recovery_claim_path": compact_text(intent.get("recoveryClaimPath")),
            "original_claim": {
                "path": compact_text(intent.get("originalClaimPath")),
                "sha256": compact_text(intent.get("originalClaimSha256")),
            },
        }

    def read_public_leaderboard_recovery_consumption(
        self,
        path: Path,
        intent: dict,
    ) -> tuple[dict, str]:
        try:
            payload, summary = read_evidence_receipt(
                path,
                root=self.root,
                require_sidecar=True,
                verify_bound_files=True,
            )
        except EvidenceReceiptError as exc:
            raise ValueError(
                "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_INTEGRITY_INVALID"
            ) from exc
        consumed_at = compact_text(payload.get("consumedAt"))
        if parse_iso_datetime(consumed_at) is None:
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CONSUMED_AT_INVALID")
        if payload != self.public_leaderboard_recovery_consumption_payload(
            intent, consumed_at
        ):
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_CONFLICT")
        return payload, summary.receipt_sha256

    def write_public_leaderboard_recovery_consumption(
        self,
        path: Path,
        intent: dict,
        consumed_at: str,
    ) -> str:
        if path.exists() or sidecar_path_for(path).exists():
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_ALREADY_EXISTS")
        try:
            summary = write_evidence_receipt(
                path,
                self.public_leaderboard_recovery_consumption_payload(
                    intent, consumed_at
                ),
                root=self.root,
                verify_bound_files=True,
            )
        except EvidenceReceiptError as exc:
            raise ValueError(
                "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_WRITE_FAILED"
            ) from exc
        return summary.receipt_sha256

    def strict_score_record_match(self, record: dict, target: dict) -> dict:
        if public_leaderboard_source(record.get("source")):
            return {"ready": False, "reason": "PUBLIC_LEADERBOARD_SOURCE_REJECTED"}
        match = self.match_submission_record(record, target)
        if not match.get("matched"):
            return {"ready": False, "reason": match.get("reason") or "NO_MATCHING_SUBMISSION_RECORD", "match": match}

        matched_by = set(match.get("matched_by") or [])
        direct_strong = bool({"QUEUE_INDEX_MATCH", "ATTACHMENT_HASH_MATCH"}.intersection(matched_by))
        fallback_strong = bool(
            "TEAM_AND_SUBMIT_TIME_MATCH" in matched_by
            and "ATTACHMENT_FILENAME_MATCH" in matched_by
            and compact_text(record.get("team_id")) == TEAM_ID
            and first_nonempty(record.get("record_id"), record.get("work_id"))
            and isinstance(match.get("delta_seconds"), int)
            and int(match["delta_seconds"]) <= 60
        )
        if not direct_strong and not fallback_strong:
            return {"ready": False, "reason": "PER_SUBMISSION_IDENTITY_NOT_STRONG_ENOUGH", "match": match}

        score = record.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            return {"ready": False, "reason": "SUBMISSION_RECORD_SCORE_INVALID", "match": match}
        score_time = parse_submission_datetime(record.get("score_time"))
        record_submit_time = parse_submission_datetime(first_nonempty(record.get("submit_time"), record.get("create_time")))
        target_submit_time = parse_submission_datetime(first_nonempty(target.get("submitted_at"), target.get("accepted_at")))
        if score_time is None:
            return {"ready": False, "reason": "SUBMISSION_RECORD_SCORE_TIME_INVALID", "match": match}
        if record_submit_time is None:
            return {"ready": False, "reason": "SUBMISSION_RECORD_SUBMIT_TIME_INVALID", "match": match}
        if target_submit_time is None:
            return {"ready": False, "reason": "TARGET_SUBMIT_TIME_INVALID", "match": match}
        if score_time < record_submit_time or score_time < target_submit_time:
            return {"ready": False, "reason": "SUBMISSION_RECORD_SCORE_TIME_BEFORE_SUBMISSION", "match": match}
        return {
            "ready": True,
            "reason": "STRONG_SUBMISSION_RECORD_WITH_SCORE",
            "match": match,
            "score_time_utc": score_time.isoformat().replace("+00:00", "Z"),
        }

    def score_finalize_intent(
        self,
        target: dict,
        matched_record: dict,
        evidence_path: Path,
        captured_at: str,
    ) -> dict:
        binding = self.current_score_missed_binding()
        expected_target = {
            "queue_index": binding["queue_index"],
            "file": binding["file"],
            "path": binding["path"],
            "sha256": binding["candidate_sha256"],
            "submitted_at": binding["submitted_at"],
            "accepted_at": binding["accepted_at"],
        }
        if safe_int(target.get("queue_index")) != binding["queue_index"]:
            raise ValueError("SCORE_FINALIZE_TARGET_QUEUE_INDEX_MISMATCH")
        if compact_text(target.get("file")).lower() != compact_text(binding["file"]).lower():
            raise ValueError("SCORE_FINALIZE_TARGET_FILE_MISMATCH")
        if canonical_path(target.get("path")) != canonical_path(binding["path"]):
            raise ValueError("SCORE_FINALIZE_TARGET_PATH_MISMATCH")
        for key in ("sha256", "submitted_at", "accepted_at"):
            if compact_text(target.get(key)).lower() != compact_text(expected_target[key]).lower():
                raise ValueError(f"SCORE_FINALIZE_TARGET_{key.upper()}_MISMATCH")

        strong = self.strict_score_record_match(matched_record, expected_target)
        if not strong["ready"]:
            raise ValueError(str(strong["reason"]))
        evidence_path = self.bounded_evidence_path(evidence_path, self.submission_records_dir, "SUBMISSION_RECORD_EVIDENCE")
        score_text = first_nonempty(matched_record.get("score_text"), matched_record.get("score"))
        identity = {
            "queueIndex": binding["queue_index"],
            "logicalSubmissionId": binding["logical_submission_id"],
            "candidateSha256": binding["candidate_sha256"],
            "acceptedAt": binding["accepted_at"],
        }
        finalization_key = sha256_json(identity)
        intent = {
            "action": SCORE_FINALIZE_ACTION,
            "contractVersion": SUBMISSION_CONTRACT_VERSION,
            "queueSchemaVersion": QUEUE_SCHEMA_VERSION,
            **identity,
            "fileName": binding["file"],
            "candidatePath": binding["path"],
            "submittedAt": binding["submitted_at"],
            "captureWindowKey": binding["capture_window_key"],
            "claimPath": binding["claim_path"],
            "claimSha256": binding["claim_sha256"],
            "evidencePath": str(evidence_path),
            "evidenceSha256": sha256_file(evidence_path),
            "evidenceCapturedAt": captured_at,
            "recordId": compact_text(matched_record.get("record_id")),
            "workId": compact_text(matched_record.get("work_id")),
            "recordSubmitTime": first_nonempty(matched_record.get("submit_time"), matched_record.get("create_time")),
            "score": float(matched_record["score"]),
            "scoreText": score_text,
            "scoreTime": compact_text(matched_record.get("score_time")),
            "scoreTimeUtc": strong["score_time_utc"],
            "teamId": compact_text(matched_record.get("team_id")),
            "teamName": compact_text(matched_record.get("team_name")),
            "source": compact_text(matched_record.get("source")),
            "matchedBy": list(strong["match"].get("matched_by") or []),
            "matchReason": compact_text(strong["match"].get("reason")),
            "queueRevision": binding["queue_revision"],
            "queueDocumentDigest": binding["queue_document_digest"],
            "activeSha256": binding["active_sha256"],
            "resultsRevision": binding["results_revision"],
            "finalizationKey": finalization_key,
        }
        intent["intentDigest"] = f"sha256:{sha256_json(intent)}"
        return intent

    @contextmanager
    def submission_records_browser_lease(self, target: dict):
        queue_item = target.get("queue_item")
        logical_submission_id = (
            compact_text(queue_item.get("logicalSubmissionId"))
            if isinstance(queue_item, dict)
            else ""
        )
        intent = {
            "action": "inspect_submission_records",
            "queueIndex": target.get("queue_index"),
            "candidateSha256": compact_text(target.get("sha256")).lower(),
            "logicalSubmissionId": logical_submission_id,
            "fileName": compact_text(target.get("file")),
        }
        lease_token = ""
        try:
            with self.control_lock():
                try:
                    token, owner = self.reserve_runner_lease(intent)
                except ValueError as exc:
                    raise ValueError(
                        f"SUBMISSION_RECORDS_BROWSER_BUSY: {exc}"
                    ) from exc
                if token is None:
                    raise ValueError(
                        "SUBMISSION_RECORDS_BROWSER_BUSY: active runner owns the shared browser"
                    )
                lease_token = token
                self.update_runner_lease(
                    lease_token,
                    pid=os.getpid(),
                    phase="inspecting",
                )
            yield
        finally:
            if lease_token:
                self.release_runner_lease(lease_token)

    def aicomp_submission_records_fetch(self, arguments: dict | None = None) -> dict:
        arguments = arguments or {}
        target = self.submission_record_target(arguments)
        with self.submission_records_browser_lease(target):
            return self._aicomp_submission_records_fetch_locked(arguments, target)

    def _aicomp_submission_records_fetch_locked(
        self, arguments: dict, target: dict
    ) -> dict:
        requested_timeout = safe_int(arguments.get("timeout_seconds")) or 180
        timeout_seconds = max(
            30,
            min(requested_timeout, SUBMISSION_RECORDS_TIMEOUT_SECONDS),
        )
        env = {
            "AICOMP_DEBUG_URL": SUBMISSION_RECORDS_DEBUG_URL,
            "AICOMP_ROOT": str(self.root),
        }
        if os.name == "nt":
            env.update({"SystemRoot": r"C:\Windows", "WINDIR": r"C:\Windows"})
        # The CDP helper waits after opening each detail drawer.  Enforce the
        # bound at the MCP boundary so deployment environment overrides cannot
        # push one read-only call past the client's fixed tools/call deadline.
        env["AICOMP_RECORDS_MAX_DETAILS"] = str(
            SUBMISSION_RECORDS_MAX_DETAILS_PER_CALL
        )
        env.update(SUBMISSION_RECORDS_RUNTIME_ENV)
        target_env = {
            "AICOMP_RECORDS_TARGET_QUEUE_INDEX": target.get("queue_index"),
            "AICOMP_RECORDS_TARGET_FILE": target.get("file"),
            "AICOMP_RECORDS_TARGET_SHA256": target.get("sha256"),
            "AICOMP_RECORDS_TARGET_SUBMITTED_AT": target.get("submitted_at"),
            "AICOMP_RECORDS_TARGET_ACCEPTED_AT": target.get("accepted_at"),
        }
        for key, value in target_env.items():
            if value is not None and str(value):
                env[key] = str(value)
        try:
            result = subprocess.run(
                self.submission_records_command(),
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout if isinstance(exc.stdout, str) else ""
            captured_at = now_iso()
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            evidence_name = safe_snapshot_name(target.get("file") or target.get("queue_index") or "submission_records")
            evidence_path = self.submission_records_dir / f"{stamp}_aicomp_submission_records_{evidence_name}.json"
            write_json(
                evidence_path,
                {
                    "captured_at": captured_at,
                    "reason": "mcp_aicomp_submission_records_fetch_timeout",
                    "target": target,
                    "timeout_seconds": timeout_seconds,
                    "outputTail": output[-8000:],
                },
            )
            return {
                "ok": False,
                "reason": PER_SUBMISSION_SOURCE_UNAVAILABLE,
                "sourceReason": "CDP_COMMAND_TIMEOUT",
                "target": target,
                "records": [],
                "matched": False,
                "scoreAttributionReady": False,
                "scoreAttributionReason": PER_SUBMISSION_SOURCE_UNAVAILABLE,
                "rawEvidencePath": str(evidence_path),
                "outputTail": output[-2000:],
            }
        output = result.stdout or ""
        payload = parse_snapshot_output(output)
        captured_at = now_iso()
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        evidence_name = safe_snapshot_name(target.get("file") or target.get("queue_index") or "submission_records")
        evidence_path = self.submission_records_dir / f"{stamp}_aicomp_submission_records_{evidence_name}.json"
        write_json(
            evidence_path,
            {
                "captured_at": captured_at,
                "reason": "mcp_aicomp_submission_records_fetch",
                "target": target,
                "exitCode": result.returncode,
                "payload": payload,
                "outputTail": output[-8000:],
            },
        )

        if not payload:
            return {
                "ok": False,
                "reason": PER_SUBMISSION_SOURCE_UNAVAILABLE,
                "sourceReason": "CDP_OUTPUT_NOT_JSON",
                "exitCode": result.returncode,
                "target": target,
                "records": [],
                "matched": False,
                "scoreAttributionReady": False,
                "scoreAttributionReason": PER_SUBMISSION_SOURCE_UNAVAILABLE,
                "rawEvidencePath": str(evidence_path),
                "outputTail": output[-2000:],
            }

        if public_leaderboard_source(payload.get("source") or payload.get("sourceKind")):
            return {
                "ok": False,
                "reason": PER_SUBMISSION_SOURCE_UNAVAILABLE,
                "sourceReason": "PUBLIC_LEADERBOARD_SOURCE_REJECTED",
                "exitCode": result.returncode,
                "target": target,
                "records": [],
                "matched": False,
                "scoreAttributionReady": False,
                "scoreAttributionReason": "PUBLIC_LEADERBOARD_SOURCE_REJECTED",
                "rawEvidencePath": str(evidence_path),
            }

        records = self.extracted_submission_records(payload, evidence_path)
        payload_reason = compact_text(payload.get("reason"))
        source_gap = payload.get("sourceAvailable") is False or payload_reason == PER_SUBMISSION_SOURCE_UNAVAILABLE
        fields_gap = payload.get("fieldsSufficient") is False or payload_reason == PER_SUBMISSION_FIELDS_INSUFFICIENT
        command_failed = payload.get("ok") is False or result.returncode != 0
        if source_gap or fields_gap or command_failed:
            reason = payload_reason
            if not reason or reason == "OK":
                reason = (
                    PER_SUBMISSION_SOURCE_UNAVAILABLE
                    if source_gap or not fields_gap
                    else PER_SUBMISSION_FIELDS_INSUFFICIENT
                )
            return {
                "ok": False,
                "reason": reason,
                "sourceReason": "SOURCE_UNAVAILABLE" if source_gap else "FIELDS_INSUFFICIENT" if fields_gap else "CDP_COMMAND_FAILED",
                "exitCode": result.returncode,
                "source": first_nonempty(payload.get("source"), "aicomp_submission_records"),
                "target": target,
                "records": records,
                "matched": False,
                "matchReason": reason,
                "matchedRecord": None,
                "scoreAttributionReady": False,
                "scoreAttributionReason": reason,
                "scoreFinalizeReady": False,
                "scoreFinalizeReason": reason,
                "scoreFinalizeIntent": None,
                "rawEvidencePath": str(evidence_path),
                "diagnostics": payload.get("diagnostics") or {},
                "outputTail": output[-2000:] if result.returncode != 0 else "",
            }

        collection_state = submission_records_collection_state(payload)
        if records and collection_state != "complete":
            reason = (
                SUBMISSION_RECORDS_COLLECTION_TRUNCATED
                if collection_state == "truncated"
                else SUBMISSION_RECORDS_COLLECTION_COMPLETENESS_UNKNOWN
            )
            return {
                "ok": False,
                "reason": reason,
                "sourceReason": reason,
                "exitCode": result.returncode,
                "source": first_nonempty(
                    payload.get("source"),
                    "aicomp_submission_records",
                ),
                "target": target,
                "records": records,
                "matched": False,
                "matchReason": reason,
                "matchedRecord": None,
                "scoreAttributionReady": False,
                "scoreAttributionReason": reason,
                "scoreFinalizeReady": False,
                "scoreFinalizeReason": reason,
                "scoreFinalizeIntent": None,
                "rawEvidencePath": str(evidence_path),
                "diagnostics": payload.get("diagnostics") or {},
                "outputTail": "",
            }

        for record in records:
            record["targetMatch"] = self.match_submission_record(record, target)
        matched_records = [record for record in records if record["targetMatch"]["matched"]]
        insufficient_identity = any(
            record["targetMatch"].get("reason") == PER_SUBMISSION_FIELDS_INSUFFICIENT
            for record in records
        )
        matched_record = matched_records[0] if len(matched_records) == 1 else None
        strict_score_match = (
            self.strict_score_record_match(matched_record, target)
            if matched_record
            else None
        )
        score_attribution_ready = bool(
            strict_score_match and strict_score_match.get("ready")
        )
        if len(matched_records) > 1:
            score_attribution_reason = "AMBIGUOUS_MATCHING_SUBMISSION_RECORDS"
        elif not matched_record and insufficient_identity:
            score_attribution_reason = PER_SUBMISSION_FIELDS_INSUFFICIENT
        elif not matched_record:
            score_attribution_reason = "NO_MATCHING_SUBMISSION_RECORD"
        elif score_attribution_ready:
            score_attribution_reason = "STRONG_SUBMISSION_RECORD_WITH_SCORE"
        else:
            strict_reason = compact_text(strict_score_match.get("reason"))
            score_attribution_reason = (
                PER_SUBMISSION_FIELDS_INSUFFICIENT
                if strict_reason == "PER_SUBMISSION_IDENTITY_NOT_STRONG_ENOUGH"
                else strict_reason or PER_SUBMISSION_FIELDS_INSUFFICIENT
            )

        source_available = (
            result.returncode == 0
            and payload.get("ok") is not False
            and payload.get("fieldsSufficient") is not False
            and bool(records)
        )
        reason = "OK" if matched_record and score_attribution_ready else score_attribution_reason
        if not records:
            payload_reason = payload.get("reason")
            reason = payload_reason if payload_reason and payload_reason != "OK" else PER_SUBMISSION_SOURCE_UNAVAILABLE
        if result.returncode != 0 and not records:
            payload_reason = payload.get("reason")
            reason = payload_reason if payload_reason and payload_reason != "OK" else PER_SUBMISSION_SOURCE_UNAVAILABLE

        score_finalize_intent = None
        score_finalize_reason = (
            compact_text(strict_score_match.get("reason"))
            if strict_score_match and not strict_score_match.get("ready")
            else "SCORE_ATTRIBUTION_NOT_READY"
        )
        if source_available and score_attribution_ready and matched_record:
            try:
                score_finalize_intent = self.score_finalize_intent(
                    target,
                    matched_record,
                    evidence_path,
                    captured_at,
                )
                score_finalize_reason = "EXACT_SCORE_FINALIZE_INTENT_READY"
            except ValueError as exc:
                score_finalize_reason = compact_text(exc)

        return {
            "ok": source_available,
            "reason": reason,
            "exitCode": result.returncode,
            "source": first_nonempty(payload.get("source"), "aicomp_submission_records"),
            "target": target,
            "records": records,
            "matched": bool(matched_record),
            "matchReason": matched_record["targetMatch"]["reason"] if matched_record else reason,
            "matchedRecord": matched_record,
            "scoreAttributionReady": score_attribution_ready,
            "scoreAttributionReason": score_attribution_reason,
            "scoreFinalizeReady": bool(score_finalize_intent),
            "scoreFinalizeReason": score_finalize_reason,
            "scoreFinalizeIntent": score_finalize_intent,
            "rawEvidencePath": str(evidence_path),
            "diagnostics": payload.get("diagnostics") or {},
            "outputTail": output[-2000:] if result.returncode != 0 else "",
        }

    def validate_public_leaderboard_evidence(
        self,
        snapshot: dict | None,
        parsed: dict,
        binding: dict,
        captured_at: str,
    ) -> dict:
        if not isinstance(snapshot, dict):
            raise ValueError("PUBLIC_LEADERBOARD_OUTPUT_NOT_JSON")
        if not is_exact_public_leaderboard_url(snapshot.get("url")):
            raise ValueError("PUBLIC_LEADERBOARD_URL_MISMATCH")
        if compact_text(parsed.get("teamId")) != TEAM_ID:
            raise ValueError("PUBLIC_LEADERBOARD_TEAM_ID_MISMATCH")
        if compact_text(parsed.get("teamName")) != TEAM_NAME:
            raise ValueError("PUBLIC_LEADERBOARD_TEAM_NAME_MISMATCH")

        score = score_as_float(parsed.get("score"))
        if score is None or not math.isfinite(score):
            raise ValueError("PUBLIC_LEADERBOARD_SCORE_INVALID")
        accepted_at = parse_iso_datetime(binding.get("accepted_at"))
        team_submit_at = parse_team_submit_datetime(parsed.get("teamSubmitTime"))
        score_at = parse_team_submit_datetime(parsed.get("scoreTime"))
        published_at = parse_leaderboard_publish_datetime(
            parsed.get("leaderboardPublishTime"), parsed.get("teamSubmitTime")
        )
        captured_dt = parse_iso_datetime(captured_at)
        original_expected_at = parse_iso_datetime(
            binding.get("original_expected_publish_at")
        )
        if accepted_at is None:
            raise ValueError("PUBLIC_LEADERBOARD_ACCEPTED_AT_INVALID")
        if team_submit_at is None:
            raise ValueError("PUBLIC_LEADERBOARD_TEAM_SUBMIT_TIME_INVALID")
        if score_at is None:
            raise ValueError("PUBLIC_LEADERBOARD_SCORE_TIME_INVALID")
        if published_at is None:
            raise ValueError("PUBLIC_LEADERBOARD_PUBLISH_TIME_INVALID")
        if captured_dt is None:
            raise ValueError("PUBLIC_LEADERBOARD_CAPTURE_TIME_INVALID")
        if original_expected_at is None:
            raise ValueError("ORIGINAL_CAPTURE_EXPECTED_PUBLISH_AT_INVALID")
        if abs(team_submit_at - accepted_at) > dt.timedelta(seconds=60):
            raise ValueError("PUBLIC_LEADERBOARD_TEAM_SUBMIT_TIME_MISMATCH")
        if score_at < team_submit_at:
            raise ValueError("PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME")
        if published_at + dt.timedelta(minutes=1) <= score_at:
            raise ValueError("PUBLIC_LEADERBOARD_PUBLISH_TIME_BEFORE_SCORE_TIME")
        if published_at <= original_expected_at:
            raise ValueError("PUBLIC_LEADERBOARD_NOT_A_NEW_PUBLICATION_WINDOW")
        if published_at > captured_dt + dt.timedelta(minutes=10):
            raise ValueError("PUBLIC_LEADERBOARD_PUBLISH_TIME_IN_FUTURE")

        history = self.accepted_submission_history(binding)
        if history["laterCount"]:
            raise ValueError("PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS")
        return {
            "ready": True,
            "reason": "EXACT_TEAM_ROW_FROM_NEW_PUBLICATION_WINDOW",
            "score": score,
            "acceptedAtUtc": accepted_at.isoformat().replace("+00:00", "Z"),
            "teamSubmitTimeUtc": team_submit_at.isoformat().replace("+00:00", "Z"),
            "scoreTimeUtc": score_at.isoformat().replace("+00:00", "Z"),
            "leaderboardPublishTimeUtc": published_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "originalExpectedPublishAtUtc": original_expected_at.isoformat().replace(
                "+00:00", "Z"
            ),
            "acceptedHistoryDigest": history["digest"],
            "acceptedHistoryCount": history["count"],
            "laterAcceptedSubmissionCount": history["laterCount"],
            "latestAcceptedAt": history["latestAcceptedAt"],
        }

    def public_leaderboard_finalize_intent(
        self,
        recovery_intent: dict,
        parsed: dict,
        evidence_path: Path,
        captured_at: str,
    ) -> dict:
        binding = self.current_score_missed_binding()
        if recovery_intent != self.public_leaderboard_recovery_intent():
            raise ValueError("STALE_PUBLIC_LEADERBOARD_RECOVERY_CONFIRMATION")
        evidence_path = self.bounded_evidence_path(
            evidence_path, self.snapshot_dir, "PUBLIC_LEADERBOARD_EVIDENCE"
        )
        evidence = read_json(evidence_path, None)
        if not isinstance(evidence, dict):
            raise ValueError("PUBLIC_LEADERBOARD_EVIDENCE_INVALID")
        snapshot = evidence.get("snapshot")
        proof = self.validate_public_leaderboard_evidence(
            snapshot if isinstance(snapshot, dict) else None,
            parsed,
            binding,
            captured_at,
        )
        recovery_claim_path = Path(
            compact_text(recovery_intent.get("recoveryClaimPath"))
        ).expanduser().resolve()
        if recovery_claim_path.parent != self.public_leaderboard_recovery_claims_dir.resolve():
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_PATH_OUTSIDE_CONTROL_PLANE")
        recovery_claim = read_json(recovery_claim_path, None)
        if not isinstance(recovery_claim, dict) or recovery_claim.get("state") != "captured":
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_NOT_CAPTURED")
        if recovery_claim.get("intent") != recovery_intent:
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_INTENT_MISMATCH")
        if canonical_path(recovery_claim.get("evidencePath")) != canonical_path(evidence_path):
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_EVIDENCE_PATH_MISMATCH")
        if compact_text(recovery_claim.get("evidenceSha256")) != sha256_file(evidence_path):
            raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_EVIDENCE_HASH_MISMATCH")
        recovery_consumption_path = (
            self.public_leaderboard_recovery_consumption_path(recovery_intent)
        )
        _consumption, recovery_consumption_sha256 = (
            self.read_public_leaderboard_recovery_consumption(
                recovery_consumption_path,
                recovery_intent,
            )
        )

        identity = {
            "queueIndex": binding["queue_index"],
            "logicalSubmissionId": binding["logical_submission_id"],
            "candidateSha256": binding["candidate_sha256"],
            "acceptedAt": binding["accepted_at"],
        }
        finalization_key = sha256_json(identity)
        score_text = compact_text(parsed.get("score"))
        intent = {
            "action": PUBLIC_LEADERBOARD_FINALIZE_ACTION,
            "contractVersion": SUBMISSION_CONTRACT_VERSION,
            "queueSchemaVersion": QUEUE_SCHEMA_VERSION,
            **identity,
            "fileName": binding["file"],
            "candidatePath": binding["path"],
            "submittedAt": binding["submitted_at"],
            "captureWindowKey": binding["capture_window_key"],
            "claimPath": binding["claim_path"],
            "claimSha256": binding["claim_sha256"],
            "originalExpectedPublishAt": binding["original_expected_publish_at"],
            "recoveryKey": recovery_intent["recoveryKey"],
            "recoveryClaimPath": str(recovery_claim_path),
            "recoveryClaimSha256": sha256_file(recovery_claim_path),
            "recoveryConsumptionPath": str(recovery_consumption_path),
            "recoveryConsumptionSha256": recovery_consumption_sha256,
            "evidencePath": str(evidence_path),
            "evidenceSha256": sha256_file(evidence_path),
            "evidenceCapturedAt": captured_at,
            "recordId": "",
            "workId": "",
            "recordSubmitTime": compact_text(parsed.get("teamSubmitTime")),
            "score": float(proof["score"]),
            "scoreText": score_text,
            "scoreTime": compact_text(parsed.get("scoreTime")),
            "scoreTimeUtc": proof["scoreTimeUtc"],
            "teamId": TEAM_ID,
            "teamName": TEAM_NAME,
            "teamRank": compact_text(parsed.get("rank")),
            "leaderboardUrl": PUBLIC_LEADERBOARD_URL,
            "leaderboardApiUrl": PUBLIC_LEADERBOARD_API_URL,
            "leaderboardPublishTime": compact_text(
                parsed.get("leaderboardPublishTime")
            ),
            "leaderboardPublishTimeUtc": proof["leaderboardPublishTimeUtc"],
            "source": PUBLIC_LEADERBOARD_SOURCE,
            "matchedBy": [
                "TEAM_ID_MATCH",
                "TEAM_NAME_MATCH",
                "TEAM_SUBMIT_TIME_MATCH",
                "PUBLICATION_WINDOW_AFTER_ORIGINAL_CLAIM",
                "NO_LATER_ACCEPTED_SUBMISSION",
            ],
            "matchReason": proof["reason"],
            "acceptedHistoryDigest": proof["acceptedHistoryDigest"],
            "acceptedHistoryCount": proof["acceptedHistoryCount"],
            "laterAcceptedSubmissionCount": proof[
                "laterAcceptedSubmissionCount"
            ],
            "latestAcceptedAt": proof["latestAcceptedAt"],
            "queueRevision": binding["queue_revision"],
            "queueDocumentDigest": binding["queue_document_digest"],
            "activeSha256": binding["active_sha256"],
            "resultsRevision": binding["results_revision"],
            "finalizationKey": finalization_key,
        }
        intent["intentDigest"] = f"sha256:{sha256_json(intent)}"
        return intent

    def public_leaderboard_finalize_intent_from_evidence(
        self, evidence_path: Path
    ) -> dict:
        evidence_path = self.bounded_evidence_path(
            evidence_path, self.snapshot_dir, "PUBLIC_LEADERBOARD_EVIDENCE"
        )
        evidence = read_json(evidence_path, None)
        if not isinstance(evidence, dict):
            raise ValueError("PUBLIC_LEADERBOARD_EVIDENCE_INVALID")
        if evidence.get("reason") != "mcp_public_leaderboard_late_recovery":
            raise ValueError("PUBLIC_LEADERBOARD_EVIDENCE_REASON_INVALID")
        if evidence.get("exitCode") != 0:
            raise ValueError("PUBLIC_LEADERBOARD_EVIDENCE_COMMAND_FAILED")
        recovery_intent = evidence.get("recoveryIntent")
        parsed = evidence.get("parsed")
        snapshot = evidence.get("snapshot")
        if not isinstance(recovery_intent, dict) or not isinstance(parsed, dict):
            raise ValueError("PUBLIC_LEADERBOARD_EVIDENCE_FIELDS_REQUIRED")
        if not isinstance(snapshot, dict) or not is_exact_public_leaderboard_url(
            snapshot.get("url")
        ):
            raise ValueError("PUBLIC_LEADERBOARD_URL_MISMATCH")
        self.validate_public_leaderboard_recovery_intent_digest(recovery_intent)
        return self.public_leaderboard_finalize_intent(
            recovery_intent,
            parsed,
            evidence_path,
            compact_text(evidence.get("captured_at")),
        )

    def aicomp_public_leaderboard_fetch(
        self, arguments: dict | None = None
    ) -> dict:
        arguments = arguments or {}
        if arguments.get("confirm_public_leaderboard_read") is not True:
            raise ValueError("CONFIRM_PUBLIC_LEADERBOARD_READ_REQUIRED")
        supplied_intent = arguments.get("intent")
        if not isinstance(supplied_intent, dict):
            raise ValueError(
                "PUBLIC_LEADERBOARD_RECOVERY_INTENT_REQUIRED: call queue_status and echo publicLeaderboardRecoveryIntent exactly"
            )
        self.validate_public_leaderboard_recovery_intent_digest(supplied_intent)
        requested_timeout = safe_int(arguments.get("timeout_seconds")) or 300
        timeout_seconds = max(
            30,
            min(requested_timeout, PUBLIC_LEADERBOARD_TIMEOUT_SECONDS),
        )

        with self.control_lock():
            self.assert_queue_mutation_allowed()
            current_intent = self.public_leaderboard_recovery_intent()
            if supplied_intent != current_intent:
                raise ValueError("STALE_PUBLIC_LEADERBOARD_RECOVERY_CONFIRMATION")
            if safe_int(current_intent.get("laterAcceptedSubmissionCount")):
                raise ValueError(
                    "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
                )
            claim_path = Path(current_intent["recoveryClaimPath"]).resolve()
            consumption_path = self.public_leaderboard_recovery_consumption_path(
                current_intent
            )
            consumption_sidecar = sidecar_path_for(consumption_path)
            claim_present = claim_path.exists()
            consumption_present = (
                consumption_path.exists() or consumption_sidecar.exists()
            )
            consumption_sha256 = ""
            claim = None
            if claim_present or consumption_present:
                if not claim_present:
                    raise ValueError(
                        "PUBLIC_LEADERBOARD_RECOVERY_CLAIM_MISSING_AFTER_CONSUMPTION"
                    )
                if not consumption_present:
                    raise ValueError(
                        "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_RECEIPT_MISSING"
                    )
                _consumption, consumption_sha256 = (
                    self.read_public_leaderboard_recovery_consumption(
                        consumption_path,
                        current_intent,
                    )
                )
                claim = read_json(claim_path, None)
                if not isinstance(claim, dict):
                    raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_INVALID")
                if claim.get("intent") != current_intent:
                    raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_CONFLICT")
                if claim.get("state") == "captured":
                    evidence_path = self.bounded_evidence_path(
                        claim.get("evidencePath"),
                        self.snapshot_dir,
                        "PUBLIC_LEADERBOARD_EVIDENCE",
                    )
                    if compact_text(claim.get("evidenceSha256")) != sha256_file(
                        evidence_path
                    ):
                        raise ValueError(
                            "PUBLIC_LEADERBOARD_RECOVERY_EVIDENCE_HASH_MISMATCH"
                        )
                    finalize_intent = (
                        self.public_leaderboard_finalize_intent_from_evidence(
                            evidence_path
                        )
                    )
                    evidence = read_json(evidence_path, {})
                    return {
                        "ok": True,
                        "alreadyCaptured": True,
                        "source": PUBLIC_LEADERBOARD_SOURCE,
                        "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
                        "parsed": evidence.get("parsed") or {},
                        "rawEvidencePath": str(evidence_path),
                        "rawEvidenceSha256": sha256_file(evidence_path),
                        "scoreFinalizeReady": True,
                        "scoreFinalizeReason": "EXACT_PUBLIC_LEADERBOARD_FINALIZE_INTENT_READY",
                        "scoreFinalizeIntent": finalize_intent,
                        "recoveryClaimPath": str(claim_path),
                        "recoveryClaimSha256": sha256_file(claim_path),
                        "recoveryConsumptionPath": str(consumption_path),
                        "recoveryConsumptionSha256": consumption_sha256,
                    }
                return {
                    "ok": False,
                    "reason": "PUBLIC_LEADERBOARD_RECOVERY_ALREADY_CONSUMED",
                    "claimState": compact_text(claim.get("state")),
                    "recoveryClaimPath": str(claim_path),
                    "recoveryConsumptionPath": str(consumption_path),
                    "recoveryConsumptionSha256": consumption_sha256,
                    "scoreFinalizeReady": False,
                    "scoreFinalizeIntent": None,
                }

            claimed_at = now_iso()
            consumption_sha256 = (
                self.write_public_leaderboard_recovery_consumption(
                    consumption_path,
                    current_intent,
                    claimed_at,
                )
            )
            claim = {
                "schemaVersion": 1,
                "state": "claimed",
                "claimedAt": claimed_at,
                "updatedAt": claimed_at,
                "intent": current_intent,
            }
            write_json(claim_path, claim)
            try:
                result = subprocess.run(
                    self.leaderboard_command(),
                    cwd=str(self.root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    timeout=timeout_seconds,
                    env={
                        **self.hermetic_node_environment(),
                        **AICOMP_LEADERBOARD_RUNTIME_ENV,
                    },
                )
                output = result.stdout or ""
                exit_code = result.returncode
            except subprocess.TimeoutExpired as exc:
                output = exc.stdout if isinstance(exc.stdout, str) else ""
                exit_code = None

            captured_at = now_iso()
            snapshot = parse_snapshot_output(output)
            parsed = parse_team_result(snapshot)
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            evidence_name = safe_snapshot_name(current_intent.get("fileName"))
            evidence_path = self.snapshot_dir / (
                f"{stamp}_late_publication_{evidence_name}.json"
            )
            evidence = {
                "schemaVersion": 1,
                "captured_at": captured_at,
                "reason": "mcp_public_leaderboard_late_recovery",
                "source": PUBLIC_LEADERBOARD_SOURCE,
                "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
                "publicLeaderboardApiUrl": PUBLIC_LEADERBOARD_API_URL,
                "recoveryIntent": current_intent,
                "exitCode": exit_code,
                "snapshot": snapshot,
                "parsed": parsed,
                "outputTail": output[-8000:],
            }
            proof = None
            failure_reason = "PUBLIC_LEADERBOARD_COMMAND_FAILED"
            if exit_code == 0 and isinstance(snapshot, dict):
                try:
                    proof = self.validate_public_leaderboard_evidence(
                        snapshot,
                        parsed,
                        self.current_score_missed_binding(),
                        captured_at,
                    )
                except ValueError as exc:
                    failure_reason = compact_text(exc)
            elif exit_code is None:
                failure_reason = "PUBLIC_LEADERBOARD_COMMAND_TIMEOUT"
            evidence["proof"] = proof
            evidence["failureReason"] = "" if proof else failure_reason
            write_json(evidence_path, evidence)
            claim.update(
                {
                    "state": "captured" if proof else "failed",
                    "updatedAt": now_iso(),
                    "evidencePath": str(evidence_path),
                    "evidenceSha256": sha256_file(evidence_path),
                    "observedLeaderboardPublishTime": compact_text(
                        parsed.get("leaderboardPublishTime")
                    ),
                    "failureReason": "" if proof else failure_reason,
                }
            )
            write_json(claim_path, claim)
            if not proof:
                return {
                    "ok": False,
                    "reason": failure_reason,
                    "source": PUBLIC_LEADERBOARD_SOURCE,
                    "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
                    "parsed": parsed,
                    "rawEvidencePath": str(evidence_path),
                    "rawEvidenceSha256": sha256_file(evidence_path),
                    "scoreFinalizeReady": False,
                    "scoreFinalizeIntent": None,
                    "recoveryClaimPath": str(claim_path),
                    "recoveryClaimSha256": sha256_file(claim_path),
                    "recoveryConsumptionPath": str(consumption_path),
                    "recoveryConsumptionSha256": consumption_sha256,
                }

            finalize_intent = self.public_leaderboard_finalize_intent_from_evidence(
                evidence_path
            )
            return {
                "ok": True,
                "alreadyCaptured": False,
                "source": PUBLIC_LEADERBOARD_SOURCE,
                "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
                "parsed": parsed,
                "proof": proof,
                "rawEvidencePath": str(evidence_path),
                "rawEvidenceSha256": sha256_file(evidence_path),
                "scoreFinalizeReady": True,
                "scoreFinalizeReason": "EXACT_PUBLIC_LEADERBOARD_FINALIZE_INTENT_READY",
                "scoreFinalizeIntent": finalize_intent,
                "recoveryClaimPath": str(claim_path),
                "recoveryClaimSha256": sha256_file(claim_path),
                "recoveryConsumptionPath": str(consumption_path),
                "recoveryConsumptionSha256": consumption_sha256,
            }

    def score_finalize_intent_from_evidence(self, evidence_path: Path) -> dict:
        evidence_path = self.bounded_evidence_path(
            evidence_path,
            self.submission_records_dir,
            "SUBMISSION_RECORD_EVIDENCE",
        )
        evidence = read_json(evidence_path, None)
        if not isinstance(evidence, dict):
            raise ValueError("SUBMISSION_RECORD_EVIDENCE_INVALID")
        payload = evidence.get("payload")
        target = evidence.get("target")
        if not isinstance(payload, dict) or not isinstance(target, dict):
            raise ValueError("SUBMISSION_RECORD_EVIDENCE_FIELDS_REQUIRED")
        if evidence.get("exitCode") != 0 or payload.get("ok") is False:
            raise ValueError("SUBMISSION_RECORD_EVIDENCE_COMMAND_FAILED")
        if public_leaderboard_source(payload.get("source") or payload.get("sourceKind")):
            raise ValueError("PUBLIC_LEADERBOARD_SOURCE_REJECTED")
        collection_state = submission_records_collection_state(payload)
        if collection_state != "complete":
            raise ValueError(
                SUBMISSION_RECORDS_COLLECTION_TRUNCATED
                if collection_state == "truncated"
                else SUBMISSION_RECORDS_COLLECTION_COMPLETENESS_UNKNOWN
            )
        records = self.extracted_submission_records(payload, evidence_path)
        for record in records:
            record["targetMatch"] = self.match_submission_record(record, target)
        matched = [record for record in records if record["targetMatch"].get("matched")]
        if len(matched) != 1:
            raise ValueError(
                "AMBIGUOUS_MATCHING_SUBMISSION_RECORDS"
                if len(matched) > 1
                else "NO_MATCHING_SUBMISSION_RECORD"
            )
        return self.score_finalize_intent(
            target,
            matched[0],
            evidence_path,
            compact_text(evidence.get("captured_at")),
        )

    def score_finalization_receipt_path(self, finalization_key: Any) -> Path:
        key = compact_text(finalization_key).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("SCORE_FINALIZATION_KEY_INVALID")
        return self.score_finalizations_dir / f"{key}.json"

    def validate_score_finalization_receipt(self, receipt: Any) -> dict:
        if not isinstance(receipt, dict):
            raise ValueError("SCORE_FINALIZATION_RECEIPT_INVALID")
        unknown = set(receipt).difference(SCORE_FINALIZATION_RECEIPT_KEYS)
        if unknown:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_FIELDS_INVALID")
        required = {
            "schemaVersion",
            "state",
            "phase",
            "preparedAt",
            "updatedAt",
            "finalizationKey",
            "intent",
            "expectedRevisions",
        }
        if not required.issubset(receipt):
            raise ValueError("SCORE_FINALIZATION_RECEIPT_FIELDS_REQUIRED")
        if receipt.get("schemaVersion") != SCORE_FINALIZATION_RECEIPT_SCHEMA_VERSION:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_SCHEMA_MISMATCH")
        phase = compact_text(receipt.get("phase"))
        if phase not in SCORE_FINALIZATION_PHASES:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_PHASE_INVALID")
        expected_state = "committed" if phase == "committed" else "in_progress"
        if receipt.get("state") != expected_state:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_STATE_INVALID")
        if phase == "committed" and not compact_text(receipt.get("committedAt")):
            raise ValueError("SCORE_FINALIZATION_COMMITTED_AT_REQUIRED")
        intent = receipt.get("intent")
        if not isinstance(intent, dict):
            raise ValueError("SCORE_FINALIZATION_RECEIPT_INTENT_INVALID")
        if intent.get("action") not in {
            SCORE_FINALIZE_ACTION,
            PUBLIC_LEADERBOARD_FINALIZE_ACTION,
        }:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_ACTION_INVALID")
        if intent.get("contractVersion") != SUBMISSION_CONTRACT_VERSION:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_CONTRACT_MISMATCH")
        if intent.get("queueSchemaVersion") != QUEUE_SCHEMA_VERSION:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_QUEUE_SCHEMA_MISMATCH")
        if compact_text(receipt.get("finalizationKey")) != compact_text(intent.get("finalizationKey")):
            raise ValueError("SCORE_FINALIZATION_RECEIPT_KEY_MISMATCH")
        self.validate_score_finalize_intent_digest(intent)
        expected_revisions = receipt.get("expectedRevisions")
        expected_revision_keys = {
            "queueBefore",
            "queueAfter",
            "resultsBefore",
            "resultsAfter",
            "activeBefore",
        }
        if not isinstance(expected_revisions, dict) or set(expected_revisions) != expected_revision_keys:
            raise ValueError("SCORE_FINALIZATION_EXPECTED_REVISIONS_INVALID")
        for key, value in expected_revisions.items():
            if key == "resultsBefore" and value == "missing":
                continue
            if not isinstance(value, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", value
            ):
                raise ValueError("SCORE_FINALIZATION_EXPECTED_REVISION_INVALID")
        if expected_revisions["queueBefore"] != compact_text(
            intent.get("queueDocumentDigest")
        ):
            raise ValueError("SCORE_FINALIZATION_QUEUE_BEFORE_MISMATCH")
        if expected_revisions["resultsBefore"] != compact_text(
            intent.get("resultsRevision")
        ):
            raise ValueError("SCORE_FINALIZATION_RESULTS_BEFORE_MISMATCH")
        if expected_revisions["activeBefore"] != (
            f"sha256:{compact_text(intent.get('activeSha256'))}"
        ):
            raise ValueError("SCORE_FINALIZATION_ACTIVE_BEFORE_MISMATCH")
        previous_receipt_digest = receipt.get("previousReceiptDigest")
        if previous_receipt_digest is not None and (
            previous_receipt_digest != "missing"
            and not re.fullmatch(
                r"sha256:[0-9a-f]{64}", compact_text(previous_receipt_digest)
            )
        ):
            raise ValueError("SCORE_FINALIZATION_PREVIOUS_RECEIPT_DIGEST_INVALID")
        return receipt

    def read_score_finalization_receipt(self, receipt_path: Path) -> dict:
        try:
            receipt, _summary = read_evidence_receipt(
                receipt_path,
                root=self.root,
                require_sidecar=True,
                verify_bound_files=False,
            )
        except EvidenceReceiptError as exc:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_INTEGRITY_INVALID") from exc
        return self.validate_score_finalization_receipt(receipt)

    def read_or_recover_score_finalization_receipt(
        self, receipt_path: Path
    ) -> dict:
        try:
            return self.read_score_finalization_receipt(receipt_path)
        except ValueError as strict_error:
            try:
                receipt, _summary = read_evidence_receipt(
                    receipt_path,
                    root=self.root,
                    require_sidecar=False,
                    verify_bound_files=False,
                )
                receipt = self.validate_score_finalization_receipt(receipt)
            except (EvidenceReceiptError, ValueError) as exc:
                raise strict_error from exc

            finalization_key = compact_text(receipt.get("finalizationKey"))
            if receipt_path.stem != finalization_key:
                raise ValueError("SCORE_FINALIZATION_RECEIPT_PATH_MISMATCH")
            previous_digest = compact_text(receipt.get("previousReceiptDigest"))
            sidecar_path = sidecar_path_for(receipt_path)
            if previous_digest == "missing":
                if sidecar_path.exists():
                    raise strict_error
            elif re.fullmatch(r"sha256:[0-9a-f]{64}", previous_digest):
                try:
                    previous_sidecar_digest = sidecar_path.read_text(
                        encoding="ascii"
                    ).strip()
                except (OSError, UnicodeError) as exc:
                    raise strict_error from exc
                if previous_digest != f"sha256:{previous_sidecar_digest}":
                    raise strict_error
            else:
                raise strict_error
            return self.write_score_finalization_receipt(receipt_path, receipt)

    def write_score_finalization_receipt(self, receipt_path: Path, receipt: dict) -> dict:
        receipt = self.validate_score_finalization_receipt(receipt)
        try:
            write_evidence_receipt(
                receipt_path,
                receipt,
                root=self.root,
                verify_bound_files=False,
            )
        except EvidenceReceiptError as exc:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_WRITE_FAILED") from exc
        return self.read_score_finalization_receipt(receipt_path)

    def advance_score_finalization_receipt(
        self,
        receipt_path: Path,
        receipt: dict,
        phase: str,
    ) -> dict:
        current_phase = compact_text(receipt.get("phase"))
        if current_phase not in SCORE_FINALIZATION_PHASES or phase not in SCORE_FINALIZATION_PHASES:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_PHASE_INVALID")
        if SCORE_FINALIZATION_PHASES.index(phase) != SCORE_FINALIZATION_PHASES.index(current_phase) + 1:
            raise ValueError("SCORE_FINALIZATION_PHASE_TRANSITION_INVALID")
        advanced = dict(receipt)
        advanced["phase"] = phase
        advanced["state"] = "committed" if phase == "committed" else "in_progress"
        advanced["previousReceiptDigest"] = f"sha256:{sha256_file(receipt_path)}"
        advanced["updatedAt"] = now_iso()
        if phase == "committed":
            advanced["committedAt"] = advanced["updatedAt"]
        return self.write_score_finalization_receipt(receipt_path, advanced)

    def validate_score_finalize_intent_digest(self, intent: dict) -> None:
        digest = compact_text(intent.get("intentDigest"))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("SCORE_FINALIZE_INTENT_DIGEST_INVALID")
        unsigned = dict(intent)
        unsigned.pop("intentDigest", None)
        if digest != f"sha256:{sha256_json(unsigned)}":
            raise ValueError("SCORE_FINALIZE_INTENT_DIGEST_MISMATCH")

    def render_results_rows(self, rows: list[dict[str, Any]]) -> str:
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            [
                {field: compact_text(row.get(field)) for field in RESULT_FIELDS}
                for row in rows
            ]
        )
        return output.getvalue()

    def results_revision_after_row(self, row: dict[str, Any]) -> str:
        rows = self.read_results_rows()
        rows.append({field: compact_text(row.get(field)) for field in RESULT_FIELDS})
        encoded = self.render_results_rows(rows).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def results_row_state(self, row: dict[str, Any]) -> str:
        rows = self.read_results_rows()
        same_index = [candidate for candidate in rows if compact_text(candidate.get("file_index")) == compact_text(row["file_index"])]
        exact = [
            candidate
            for candidate in same_index
            if all(
                (
                    canonical_path(candidate.get(field)) == canonical_path(row.get(field))
                    if field in {"file_path", "snapshot_path"}
                    else compact_text(candidate.get(field)) == compact_text(row.get(field))
                )
                for field in RESULT_FIELDS
            )
        ]
        if len(exact) == 1 and len(same_index) == 1:
            return "exact"
        if same_index:
            raise ValueError("RESULT_IDENTITY_CONFLICT")
        return "missing"

    def write_results_row_atomic(self, row: dict[str, Any]) -> bool:
        state = self.results_row_state(row)
        if state == "exact":
            return False
        rows = self.read_results_rows()
        rows.append({field: compact_text(row.get(field)) for field in RESULT_FIELDS})
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.results_path.with_suffix(self.results_path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(self.render_results_rows(rows), encoding="utf-8", newline="")
        tmp.replace(self.results_path)
        return True

    def finalization_event_state(self, event: dict) -> str:
        finalization_key = compact_text(event.get("finalization_key"))
        if self.events_path.is_file():
            for line in self.events_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    existing.get("event") == event.get("event")
                    and compact_text(existing.get("finalization_key")) == finalization_key
                ):
                    comparable = dict(existing)
                    comparable.pop("time", None)
                    if comparable != event:
                        raise ValueError("SCORE_FINALIZATION_EVENT_CONFLICT")
                    return "exact"
        return "missing"

    def append_finalization_event_once(self, event: dict) -> bool:
        if self.finalization_event_state(event) == "exact":
            return False
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            persisted = {"time": now_iso(), **event}
            stream.write(json.dumps(persisted, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True

    def score_finalization_result_row(self, item: dict, intent: dict, evidence_path: Path) -> dict:
        score_text = compact_text(intent.get("scoreText")) or compact_text(intent.get("score"))
        is_public = intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
        return {
            "query_time": compact_text(intent.get("evidenceCapturedAt")),
            "file_index": safe_int(intent.get("queueIndex")),
            "file_name": item.get("name") or "",
            "file_path": item.get("path") or "",
            "submitted_at": item.get("submittedAt") or "",
            "accepted_at": item.get("acceptedAt") or "",
            "leaderboard_publish_time": (
                compact_text(intent.get("leaderboardPublishTime")) if is_public else ""
            ),
            "team_rank": compact_text(intent.get("teamRank")) if is_public else "",
            "team_id": intent.get("teamId") or TEAM_ID,
            "team_name": intent.get("teamName") or TEAM_NAME,
            "team_submit_time": intent.get("recordSubmitTime") or "",
            "score": score_text,
            "score_time": intent.get("scoreTime") or "",
            "snapshot_path": str(evidence_path),
        }

    def score_finalization_event(self, item: dict, intent: dict, evidence_path: Path, claim_path: Path) -> dict:
        score_text = compact_text(intent.get("scoreText")) or compact_text(intent.get("score"))
        is_public = intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
        event = {
            "event": (
                "score.captured_public_leaderboard_recovery"
                if is_public
                else "score.captured_submission_record"
            ),
            "finalization_key": intent["finalizationKey"],
            "queue_index": safe_int(intent.get("queueIndex")),
            "file": item.get("name") or "",
            "path": item.get("path") or "",
            "logical_submission_id": intent["logicalSubmissionId"],
            "candidate_sha256": intent["candidateSha256"],
            "score": score_text,
            "score_time": intent["scoreTime"],
            "record_id": first_nonempty(intent.get("recordId"), intent.get("workId")),
            "evidence_path": str(evidence_path),
            "evidence_sha256": intent["evidenceSha256"],
            "claim_path": str(claim_path),
            "claim_sha256": intent["claimSha256"],
        }
        if is_public:
            event.update(
                {
                    "team_submit_time": compact_text(intent.get("recordSubmitTime")),
                    "leaderboard_publish_time": compact_text(
                        intent.get("leaderboardPublishTime")
                    ),
                    "team_rank": compact_text(intent.get("teamRank")),
                    "leaderboard_url": compact_text(intent.get("leaderboardUrl")),
                    "recovery_claim_path": canonical_path(
                        intent.get("recoveryClaimPath")
                    ),
                    "recovery_claim_sha256": compact_text(
                        intent.get("recoveryClaimSha256")
                    ),
                    "recovery_consumption_path": canonical_path(
                        intent.get("recoveryConsumptionPath")
                    ),
                    "recovery_consumption_sha256": compact_text(
                        intent.get("recoveryConsumptionSha256")
                    ),
                }
            )
        return event

    def score_finalization_queue_update(
        self,
        item: dict,
        intent: dict,
        evidence_path: Path,
        receipt_path: Path,
        applied_at: str,
    ) -> dict:
        score_text = compact_text(intent.get("scoreText")) or compact_text(
            intent.get("score")
        )
        is_public = intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
        update = {
            "status": "scored",
            "score": score_text,
            "scoreTime": compact_text(intent.get("scoreTime")),
            "leaderboardPublishTime": (
                compact_text(intent.get("leaderboardPublishTime"))
                if is_public
                else ""
            ),
            "teamSubmitTime": compact_text(intent.get("recordSubmitTime")),
            "nextRefreshAnchor": "",
            "scoreEvidenceSource": compact_text(intent.get("source")),
            "scoreEvidencePath": str(evidence_path),
            "scoreEvidenceSha256": compact_text(
                intent.get("evidenceSha256")
            ).removeprefix("sha256:"),
            "scoreRecordId": first_nonempty(
                intent.get("recordId"), intent.get("workId")
            ),
            "scoreFinalizationReceipt": str(receipt_path),
            "scoreFinalizationKey": intent["finalizationKey"],
        }
        if is_public:
            update.update(
                {
                    "teamRank": compact_text(intent.get("teamRank")),
                    "scoreRecoveryClaimPath": compact_text(
                        intent.get("recoveryClaimPath")
                    ),
                    "scoreRecoveryClaimSha256": compact_text(
                        intent.get("recoveryClaimSha256")
                    ),
                    "scoreRecoveryConsumptionPath": compact_text(
                        intent.get("recoveryConsumptionPath")
                    ),
                    "scoreRecoveryConsumptionSha256": compact_text(
                        intent.get("recoveryConsumptionSha256")
                    ),
                    "scoreLeaderboardUrl": compact_text(
                        intent.get("leaderboardUrl")
                    ),
                }
            )
            note = (
                f"scored {score_text} from exact canonical public leaderboard "
                f"late-publication evidence at {applied_at}"
            )
        else:
            note = (
                f"scored {score_text} from strongly matched per-submission "
                f"evidence at {applied_at}"
            )
        update["note"] = f"{item.get('note') or ''}\n{note}".strip()[-1500:]
        return update

    def score_finalization_expected_revisions(
        self,
        intent: dict,
        state: dict,
        receipt_path: Path,
        prepared_at: str,
    ) -> dict:
        queue_after = json.loads(json.dumps(state["doc"], ensure_ascii=False))
        queue_index = safe_int(intent.get("queueIndex"))
        matches = [
            item
            for item in queue_after["queue"]
            if safe_int(item.get("index")) == queue_index
        ]
        if len(matches) != 1:
            raise ValueError("SCORE_FINALIZE_QUEUE_INDEX_NOT_UNIQUE")
        matches[0].update(
            self.score_finalization_queue_update(
                matches[0],
                intent,
                state["evidence_path"],
                receipt_path,
                prepared_at,
            )
        )
        queue_after["updatedAt"] = prepared_at
        queue_after["schemaVersion"] = QUEUE_SCHEMA_VERSION
        return {
            "queueBefore": compact_text(intent.get("queueDocumentDigest")),
            "queueAfter": f"sha256:{sha256_json(queue_after)}",
            "resultsBefore": compact_text(intent.get("resultsRevision")),
            "resultsAfter": self.results_revision_after_row(state["result_row"]),
            "activeBefore": f"sha256:{compact_text(intent.get('activeSha256'))}",
        }

    def validate_score_finalization_phase_revisions(
        self,
        intent: dict,
        receipt: dict | None,
        state: dict,
    ) -> None:
        current = {
            "queue": f"sha256:{sha256_json(state['doc'])}",
            "results": self.path_revision(self.results_path),
            "active": self.path_revision(self.active_path),
        }
        if receipt is None:
            expected = {
                "queue": compact_text(intent.get("queueDocumentDigest")),
                "results": compact_text(intent.get("resultsRevision")),
                "active": f"sha256:{compact_text(intent.get('activeSha256'))}",
            }
        else:
            revisions = receipt["expectedRevisions"]
            phase = compact_text(receipt.get("phase"))
            queue_expected = revisions["queueBefore"]
            results_expected = revisions["resultsBefore"]
            active_expected = revisions["activeBefore"]
            if phase == "prepared" and state["result_state"] == "exact":
                results_expected = revisions["resultsAfter"]
            elif phase == "result_applied":
                results_expected = revisions["resultsAfter"]
                if state["queue_state"] == "exact":
                    queue_expected = revisions["queueAfter"]
            elif phase in {
                "queue_applied",
                "active_clear_prepared",
                "active_cleared",
                "event_applied",
                "committed",
            }:
                queue_expected = revisions["queueAfter"]
                results_expected = revisions["resultsAfter"]
            if phase == "active_clear_prepared" and state["active_state"] == "missing":
                active_expected = "missing"
            elif phase in {"active_cleared", "event_applied", "committed"}:
                active_expected = "missing"
            expected = {
                "queue": queue_expected,
                "results": results_expected,
                "active": active_expected,
            }
        for name in ("queue", "results", "active"):
            if current[name] != expected[name]:
                raise ValueError(
                    f"SCORE_FINALIZATION_{name.upper()}_REVISION_CONFLICT"
                )

        if (
            intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
            and receipt is not None
            and receipt.get("phase") != "committed"
        ):
            history = self.accepted_submission_history(
                {
                    "queue": state["doc"]["queue"],
                    "accepted_at": intent.get("acceptedAt"),
                    "queue_index": intent.get("queueIndex"),
                    "logical_submission_id": intent.get(
                        "logicalSubmissionId"
                    ),
                }
            )
            if history["laterCount"]:
                raise ValueError(
                    "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
                )

    def verify_score_finalization_immutable_bindings(self, intent: dict, item: dict) -> tuple[Path, Path, Path]:
        is_public = intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
        evidence_path = self.bounded_evidence_path(
            intent.get("evidencePath"),
            self.snapshot_dir if is_public else self.submission_records_dir,
            "PUBLIC_LEADERBOARD_EVIDENCE" if is_public else "SUBMISSION_RECORD_EVIDENCE",
        )
        claim_path = self.bounded_evidence_path(
            intent.get("claimPath"), self.score_capture_claims_dir, "SCORE_CAPTURE_CLAIM"
        )
        expected_evidence_sha = compact_text(intent.get("evidenceSha256")).removeprefix("sha256:")
        expected_claim_sha = compact_text(intent.get("claimSha256")).removeprefix("sha256:")
        if sha256_file(evidence_path) != expected_evidence_sha:
            raise ValueError("SUBMISSION_RECORD_EVIDENCE_CHANGED")
        if sha256_file(claim_path) != expected_claim_sha:
            raise ValueError("SCORE_CAPTURE_CLAIM_CHANGED")

        candidate_path = Path(str(item.get("path") or "")).expanduser().resolve()
        root = self.root.resolve()
        if candidate_path != root and root not in candidate_path.parents:
            raise ValueError("SCORE_FINALIZE_CANDIDATE_PATH_OUTSIDE_ROOT")
        if canonical_path(candidate_path) != canonical_path(intent.get("candidatePath")):
            raise ValueError("SCORE_FINALIZE_CANDIDATE_PATH_MISMATCH")
        if not candidate_path.is_file():
            raise ValueError("SCORE_FINALIZE_CANDIDATE_MISSING")
        if sha256_file(candidate_path) != compact_text(intent.get("candidateSha256")).lower():
            raise ValueError("SCORE_FINALIZE_CANDIDATE_BYTES_CHANGED")

        claim = read_json(claim_path, None)
        if not isinstance(claim, dict):
            raise ValueError("SCORE_CAPTURE_CLAIM_INVALID")
        expected_claim = {
            "queueIndex": safe_int(intent.get("queueIndex")),
            "logicalSubmissionId": compact_text(intent.get("logicalSubmissionId")),
            "candidateSha256": compact_text(intent.get("candidateSha256")).lower(),
            "acceptedAt": compact_text(intent.get("acceptedAt")),
            "key": compact_text(intent.get("captureWindowKey")),
        }
        actual_claim = {
            "queueIndex": safe_int(claim.get("queueIndex")),
            "logicalSubmissionId": compact_text(claim.get("logicalSubmissionId")),
            "candidateSha256": compact_text(claim.get("candidateSha256")).lower(),
            "acceptedAt": compact_text(claim.get("acceptedAt")),
            "key": compact_text(claim.get("key")),
        }
        if actual_claim != expected_claim:
            raise ValueError("SCORE_CAPTURE_CLAIM_IDENTITY_MISMATCH")

        if is_public:
            if not is_exact_public_leaderboard_url(intent.get("leaderboardUrl")):
                raise ValueError("PUBLIC_LEADERBOARD_URL_MISMATCH")
            if compact_text(intent.get("leaderboardApiUrl")) != PUBLIC_LEADERBOARD_API_URL:
                raise ValueError("PUBLIC_LEADERBOARD_API_URL_MISMATCH")
            recovery_claim_path = self.bounded_evidence_path(
                intent.get("recoveryClaimPath"),
                self.public_leaderboard_recovery_claims_dir,
                "PUBLIC_LEADERBOARD_RECOVERY_CLAIM",
            )
            expected_recovery_claim_sha = compact_text(
                intent.get("recoveryClaimSha256")
            ).removeprefix("sha256:")
            if sha256_file(recovery_claim_path) != expected_recovery_claim_sha:
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_CHANGED")
            recovery_claim = read_json(recovery_claim_path, None)
            if not isinstance(recovery_claim, dict) or recovery_claim.get("state") != "captured":
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_NOT_CAPTURED")
            recovery_intent = recovery_claim.get("intent")
            if not isinstance(recovery_intent, dict):
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_INTENT_INVALID")
            self.validate_public_leaderboard_recovery_intent_digest(recovery_intent)
            expected_recovery = {
                "recoveryKey": compact_text(intent.get("recoveryKey")),
                "queueIndex": safe_int(intent.get("queueIndex")),
                "logicalSubmissionId": compact_text(intent.get("logicalSubmissionId")),
                "candidateSha256": compact_text(intent.get("candidateSha256")).lower(),
                "acceptedAt": compact_text(intent.get("acceptedAt")),
                "originalCaptureWindowKey": compact_text(intent.get("captureWindowKey")),
                "originalClaimPath": canonical_path(intent.get("claimPath")),
                "originalClaimSha256": compact_text(intent.get("claimSha256")),
                "originalExpectedPublishAt": compact_text(
                    intent.get("originalExpectedPublishAt")
                ),
                "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
                "publicLeaderboardApiUrl": PUBLIC_LEADERBOARD_API_URL,
            }
            actual_recovery = {
                "recoveryKey": compact_text(recovery_intent.get("recoveryKey")),
                "queueIndex": safe_int(recovery_intent.get("queueIndex")),
                "logicalSubmissionId": compact_text(
                    recovery_intent.get("logicalSubmissionId")
                ),
                "candidateSha256": compact_text(
                    recovery_intent.get("candidateSha256")
                ).lower(),
                "acceptedAt": compact_text(recovery_intent.get("acceptedAt")),
                "originalCaptureWindowKey": compact_text(
                    recovery_intent.get("originalCaptureWindowKey")
                ),
                "originalClaimPath": canonical_path(
                    recovery_intent.get("originalClaimPath")
                ),
                "originalClaimSha256": compact_text(
                    recovery_intent.get("originalClaimSha256")
                ),
                "originalExpectedPublishAt": compact_text(
                    recovery_intent.get("originalExpectedPublishAt")
                ),
                "publicLeaderboardUrl": compact_text(
                    recovery_intent.get("publicLeaderboardUrl")
                ),
                "publicLeaderboardApiUrl": compact_text(
                    recovery_intent.get("publicLeaderboardApiUrl")
                ),
            }
            if actual_recovery != expected_recovery:
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_CLAIM_IDENTITY_MISMATCH")
            if canonical_path(recovery_claim.get("evidencePath")) != canonical_path(
                evidence_path
            ):
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_EVIDENCE_PATH_MISMATCH")
            if compact_text(recovery_claim.get("evidenceSha256")) != expected_evidence_sha:
                raise ValueError("PUBLIC_LEADERBOARD_RECOVERY_EVIDENCE_HASH_MISMATCH")
            recovery_consumption_path = (
                self.public_leaderboard_recovery_consumption_path(recovery_intent)
            )
            if canonical_path(recovery_consumption_path) != canonical_path(
                intent.get("recoveryConsumptionPath")
            ):
                raise ValueError(
                    "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_PATH_MISMATCH"
                )
            _consumption, recovery_consumption_sha256 = (
                self.read_public_leaderboard_recovery_consumption(
                    recovery_consumption_path,
                    recovery_intent,
                )
            )
            if recovery_consumption_sha256 != compact_text(
                intent.get("recoveryConsumptionSha256")
            ):
                raise ValueError(
                    "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION_CHANGED"
                )
        return evidence_path, claim_path, candidate_path

    def score_finalization_queue_state(
        self,
        intent: dict,
        receipt_path: Path,
    ) -> tuple[dict, dict, str]:
        doc = self.read_queue_doc()
        queue_index = safe_int(intent.get("queueIndex"))
        matches = [item for item in doc["queue"] if safe_int(item.get("index")) == queue_index]
        if len(matches) != 1:
            raise ValueError("SCORE_FINALIZE_QUEUE_INDEX_NOT_UNIQUE")
        item = matches[0]
        expected_identity = {
            "candidate_sha256": compact_text(intent.get("candidateSha256")).lower(),
            "logical_submission_id": compact_text(intent.get("logicalSubmissionId")),
            "submitted_at": compact_text(intent.get("submittedAt")),
            "accepted_at": compact_text(intent.get("acceptedAt")),
            "capture_window_key": compact_text(intent.get("captureWindowKey")),
            "file_name": compact_text(intent.get("fileName")).lower(),
            "candidate_path": canonical_path(intent.get("candidatePath")),
        }
        actual_identity = {
            "candidate_sha256": self.queue_item_sha256(item),
            "logical_submission_id": compact_text(item.get("logicalSubmissionId")),
            "submitted_at": compact_text(item.get("submittedAt")),
            "accepted_at": compact_text(item.get("acceptedAt")),
            "capture_window_key": compact_text(item.get("scoreCaptureWindowKey")),
            "file_name": compact_text(item.get("name")).lower(),
            "candidate_path": canonical_path(item.get("path")),
        }
        if actual_identity != expected_identity:
            raise ValueError("SCORE_FINALIZE_QUEUE_IDENTITY_MISMATCH")
        if item.get("status") == "score_missed":
            return doc, item, "pending"
        if item.get("status") != "scored":
            raise ValueError("QUEUE_ITEM_NOT_SCORE_FINALIZABLE")
        score_text = compact_text(intent.get("scoreText")) or compact_text(intent.get("score"))
        is_public = intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION
        expected_scored = {
            "score": score_text,
            "scoreTime": compact_text(intent.get("scoreTime")),
            "scoreEvidenceSource": compact_text(intent.get("source")),
            "scoreEvidencePath": canonical_path(intent.get("evidencePath")),
            "scoreEvidenceSha256": compact_text(intent.get("evidenceSha256")).removeprefix("sha256:"),
            "scoreRecordId": first_nonempty(intent.get("recordId"), intent.get("workId")),
            "scoreFinalizationReceipt": canonical_path(receipt_path),
            "scoreFinalizationKey": compact_text(intent.get("finalizationKey")),
        }
        actual_scored = {
            "score": compact_text(item.get("score")),
            "scoreTime": compact_text(item.get("scoreTime")),
            "scoreEvidenceSource": compact_text(item.get("scoreEvidenceSource")),
            "scoreEvidencePath": canonical_path(item.get("scoreEvidencePath")),
            "scoreEvidenceSha256": compact_text(item.get("scoreEvidenceSha256")),
            "scoreRecordId": compact_text(item.get("scoreRecordId")),
            "scoreFinalizationReceipt": canonical_path(item.get("scoreFinalizationReceipt")),
            "scoreFinalizationKey": compact_text(item.get("scoreFinalizationKey")),
        }
        if is_public:
            expected_scored.update(
                {
                    "leaderboardPublishTime": compact_text(
                        intent.get("leaderboardPublishTime")
                    ),
                    "teamSubmitTime": compact_text(intent.get("recordSubmitTime")),
                    "teamRank": compact_text(intent.get("teamRank")),
                    "scoreRecoveryClaimPath": canonical_path(
                        intent.get("recoveryClaimPath")
                    ),
                    "scoreRecoveryClaimSha256": compact_text(
                        intent.get("recoveryClaimSha256")
                    ),
                    "scoreRecoveryConsumptionPath": canonical_path(
                        intent.get("recoveryConsumptionPath")
                    ),
                    "scoreRecoveryConsumptionSha256": compact_text(
                        intent.get("recoveryConsumptionSha256")
                    ),
                    "scoreLeaderboardUrl": compact_text(intent.get("leaderboardUrl")),
                }
            )
            actual_scored.update(
                {
                    "leaderboardPublishTime": compact_text(
                        item.get("leaderboardPublishTime")
                    ),
                    "teamSubmitTime": compact_text(item.get("teamSubmitTime")),
                    "teamRank": compact_text(item.get("teamRank")),
                    "scoreRecoveryClaimPath": canonical_path(
                        item.get("scoreRecoveryClaimPath")
                    ),
                    "scoreRecoveryClaimSha256": compact_text(
                        item.get("scoreRecoveryClaimSha256")
                    ),
                    "scoreRecoveryConsumptionPath": canonical_path(
                        item.get("scoreRecoveryConsumptionPath")
                    ),
                    "scoreRecoveryConsumptionSha256": compact_text(
                        item.get("scoreRecoveryConsumptionSha256")
                    ),
                    "scoreLeaderboardUrl": compact_text(
                        item.get("scoreLeaderboardUrl")
                    ),
                }
            )
        if actual_scored != expected_scored:
            raise ValueError("SCORE_FINALIZATION_IDENTITY_CONFLICT")
        return doc, item, "exact"

    def score_finalization_active_state(self, intent: dict) -> str:
        active = read_json(self.active_path, None)
        if active is None:
            return "missing"
        if not isinstance(active, dict):
            raise ValueError("ACTIVE_SCORE_FINALIZATION_INVALID")
        expected = {
            "queue_index": safe_int(intent.get("queueIndex")),
            "logical_submission_id": compact_text(intent.get("logicalSubmissionId")),
            "candidate_sha256": compact_text(intent.get("candidateSha256")).lower(),
            "file": compact_text(intent.get("fileName")).lower(),
            "path": canonical_path(intent.get("candidatePath")),
            "submitted_at": compact_text(intent.get("submittedAt")),
            "accepted_at": compact_text(intent.get("acceptedAt")),
            "capture_window_key": compact_text(intent.get("captureWindowKey")),
            "claim_path": canonical_path(intent.get("claimPath")),
            "status": "score_missed",
        }
        actual = {
            "queue_index": safe_int(active.get("queue_index")),
            "logical_submission_id": compact_text(active.get("logical_submission_id")),
            "candidate_sha256": compact_text(active.get("candidate_sha256")).lower(),
            "file": compact_text(active.get("file")).lower(),
            "path": canonical_path(active.get("path")),
            "submitted_at": compact_text(active.get("submitted_at")),
            "accepted_at": compact_text(active.get("accepted_at")),
            "capture_window_key": compact_text(active.get("score_capture_window_key")),
            "claim_path": canonical_path(active.get("score_capture_claim_path")),
            "status": active.get("status"),
        }
        if actual != expected:
            raise ValueError("ACTIVE_SCORE_FINALIZATION_IDENTITY_CONFLICT")
        return "exact"

    def preflight_score_finalization(
        self,
        intent: dict,
        receipt_path: Path,
        receipt: dict | None,
    ) -> dict:
        phase = compact_text(receipt.get("phase")) if receipt is not None else None
        doc, item, queue_state = self.score_finalization_queue_state(intent, receipt_path)
        evidence_path, claim_path, _candidate_path = self.verify_score_finalization_immutable_bindings(intent, item)
        result_row = self.score_finalization_result_row(item, intent, evidence_path)
        result_state = self.results_row_state(result_row)
        active_state = self.score_finalization_active_state(intent)
        event = self.score_finalization_event(item, intent, evidence_path, claim_path)
        event_state = self.finalization_event_state(event)

        allowed = {
            None: ({"pending"}, {"missing"}, {"exact"}, {"missing"}),
            "prepared": ({"pending"}, {"missing", "exact"}, {"exact"}, {"missing"}),
            "result_applied": ({"pending", "exact"}, {"exact"}, {"exact"}, {"missing"}),
            "queue_applied": ({"exact"}, {"exact"}, {"exact"}, {"missing"}),
            "active_clear_prepared": ({"exact"}, {"exact"}, {"exact", "missing"}, {"missing"}),
            "active_cleared": ({"exact"}, {"exact"}, {"missing"}, {"missing", "exact"}),
            "event_applied": ({"exact"}, {"exact"}, {"missing"}, {"exact"}),
            "committed": ({"exact"}, {"exact"}, {"missing"}, {"exact"}),
        }
        if phase not in allowed:
            raise ValueError("SCORE_FINALIZATION_RECEIPT_PHASE_INVALID")
        queue_allowed, result_allowed, active_allowed, event_allowed = allowed[phase]
        if queue_state not in queue_allowed:
            raise ValueError("SCORE_FINALIZATION_QUEUE_PHASE_CONFLICT")
        if result_state not in result_allowed:
            raise ValueError("SCORE_FINALIZATION_RESULTS_PHASE_CONFLICT")
        if active_state not in active_allowed:
            raise ValueError("SCORE_FINALIZATION_ACTIVE_PHASE_CONFLICT")
        if event_state not in event_allowed:
            raise ValueError("SCORE_FINALIZATION_EVENT_PHASE_CONFLICT")
        state = {
            "doc": doc,
            "item": item,
            "queue_state": queue_state,
            "evidence_path": evidence_path,
            "claim_path": claim_path,
            "result_row": result_row,
            "result_state": result_state,
            "active_state": active_state,
            "event": event,
            "event_state": event_state,
        }
        self.validate_score_finalization_phase_revisions(intent, receipt, state)
        return state

    def apply_score_finalization_receipt(self, receipt_path: Path, receipt: dict) -> dict:
        receipt = self.validate_score_finalization_receipt(receipt)
        intent = receipt["intent"]
        was_committed = receipt.get("phase") == "committed"
        while receipt.get("phase") != "committed":
            phase = compact_text(receipt.get("phase"))
            state = self.preflight_score_finalization(intent, receipt_path, receipt)
            if phase == "prepared":
                if state["result_state"] == "missing":
                    self.write_results_row_atomic(state["result_row"])
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "result_applied"
                )
                continue
            if phase == "result_applied":
                if state["queue_state"] == "pending":
                    item = state["item"]
                    item.update(
                        self.score_finalization_queue_update(
                            item,
                            intent,
                            state["evidence_path"],
                            receipt_path,
                            receipt["preparedAt"],
                        )
                    )
                    self.save_queue_doc(
                        state["doc"], updated_at=receipt["preparedAt"]
                    )
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "queue_applied"
                )
                continue
            if phase == "queue_applied":
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "active_clear_prepared"
                )
                continue
            if phase == "active_clear_prepared":
                if state["active_state"] == "exact":
                    self.active_path.unlink(missing_ok=False)
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "active_cleared"
                )
                continue
            if phase == "active_cleared":
                if state["event_state"] == "missing":
                    self.append_finalization_event_once(state["event"])
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "event_applied"
                )
                continue
            if phase == "event_applied":
                self.verify_score_finalization_immutable_bindings(intent, state["item"])
                receipt = self.advance_score_finalization_receipt(
                    receipt_path, receipt, "committed"
                )
                continue
            raise ValueError("SCORE_FINALIZATION_RECEIPT_PHASE_INVALID")

        final_state = self.preflight_score_finalization(intent, receipt_path, receipt)
        item = final_state["item"]
        evidence_path = final_state["evidence_path"]
        claim_path = final_state["claim_path"]
        recovery_claim_path = None
        recovery_consumption_path = None
        if intent.get("action") == PUBLIC_LEADERBOARD_FINALIZE_ACTION:
            recovery_claim_path = self.bounded_evidence_path(
                intent.get("recoveryClaimPath"),
                self.public_leaderboard_recovery_claims_dir,
                "PUBLIC_LEADERBOARD_RECOVERY_CLAIM",
            )
            recovery_consumption_path = self.bounded_evidence_path(
                intent.get("recoveryConsumptionPath"),
                self.public_leaderboard_recovery_consumptions_dir,
                "PUBLIC_LEADERBOARD_RECOVERY_CONSUMPTION",
            )
        queue_index = safe_int(intent.get("queueIndex"))
        score_text = compact_text(intent.get("scoreText")) or compact_text(intent.get("score"))
        return {
            "ok": True,
            "alreadyFinalized": was_committed,
            "queueIndex": queue_index,
            "logicalSubmissionId": intent["logicalSubmissionId"],
            "candidateSha256": intent["candidateSha256"],
            "status": "scored",
            "score": score_text,
            "scoreTime": intent["scoreTime"],
            "source": intent["source"],
            "recordId": first_nonempty(intent.get("recordId"), intent.get("workId")),
            "evidencePath": str(evidence_path),
            "evidenceSha256": intent["evidenceSha256"],
            "captureClaimPreserved": claim_path.is_file(),
            "recoveryClaimPreserved": (
                recovery_claim_path.is_file() if recovery_claim_path is not None else None
            ),
            "recoveryConsumptionPreserved": (
                recovery_consumption_path.is_file()
                and sidecar_path_for(recovery_consumption_path).is_file()
                if recovery_consumption_path is not None
                else None
            ),
            "recoveryConsumptionPath": (
                str(recovery_consumption_path)
                if recovery_consumption_path is not None
                else ""
            ),
            "recoveryConsumptionSha256": compact_text(
                intent.get("recoveryConsumptionSha256")
            ),
            "leaderboardPublishTime": compact_text(
                intent.get("leaderboardPublishTime")
            ),
            "teamSubmitTime": compact_text(intent.get("recordSubmitTime")),
            "teamRank": compact_text(intent.get("teamRank")),
            "finalizationReceipt": str(receipt_path),
        }

    def queue_finalize_score(
        self,
        arguments: dict,
        *,
        expected_action: str,
    ) -> dict:
        if arguments.get("confirm_score_attribution") is not True:
            raise ValueError("CONFIRM_SCORE_ATTRIBUTION_REQUIRED")
        intent = arguments.get("intent")
        if not isinstance(intent, dict):
            raise ValueError("SCORE_FINALIZE_INTENT_REQUIRED")
        if intent.get("action") != expected_action:
            raise ValueError("SCORE_FINALIZE_ACTION_MISMATCH")
        if intent.get("contractVersion") != SUBMISSION_CONTRACT_VERSION:
            raise ValueError("SCORE_FINALIZE_CONTRACT_VERSION_MISMATCH")
        if intent.get("queueSchemaVersion") != QUEUE_SCHEMA_VERSION:
            raise ValueError("SCORE_FINALIZE_QUEUE_SCHEMA_VERSION_MISMATCH")
        self.validate_score_finalize_intent_digest(intent)
        receipt_path = self.score_finalization_receipt_path(intent.get("finalizationKey"))

        with self.control_lock():
            self.assert_queue_mutation_allowed()
            sidecar_path = sidecar_path_for(receipt_path)
            if receipt_path.exists():
                existing = self.read_or_recover_score_finalization_receipt(
                    receipt_path
                )
                if existing.get("intent") != intent:
                    raise ValueError("SCORE_FINALIZATION_RECEIPT_CONFLICT")
                return self.apply_score_finalization_receipt(receipt_path, existing)
            if sidecar_path.exists():
                raise ValueError("SCORE_FINALIZATION_RECEIPT_ORPHANED_SIDECAR")

            evidence_path = Path(str(intent.get("evidencePath") or ""))
            if expected_action == PUBLIC_LEADERBOARD_FINALIZE_ACTION:
                recomputed = self.public_leaderboard_finalize_intent_from_evidence(
                    evidence_path
                )
            else:
                recomputed = self.score_finalize_intent_from_evidence(evidence_path)
            if recomputed != intent:
                raise ValueError("STALE_SCORE_FINALIZE_CONFIRMATION")
            binding = self.current_score_missed_binding()
            expected_revisions = {
                "queueRevision": binding["queue_revision"],
                "queueDocumentDigest": binding["queue_document_digest"],
                "activeSha256": binding["active_sha256"],
                "resultsRevision": binding["results_revision"],
                "claimSha256": binding["claim_sha256"],
            }
            if any(compact_text(intent.get(key)) != compact_text(value) for key, value in expected_revisions.items()):
                raise ValueError("STALE_SCORE_FINALIZE_CONFIRMATION")
            initial_state = self.preflight_score_finalization(
                intent, receipt_path, None
            )
            prepared_at = now_iso()
            prepared = {
                "schemaVersion": SCORE_FINALIZATION_RECEIPT_SCHEMA_VERSION,
                "state": "in_progress",
                "phase": "prepared",
                "preparedAt": prepared_at,
                "updatedAt": prepared_at,
                "finalizationKey": intent["finalizationKey"],
                "intent": intent,
                "previousReceiptDigest": "missing",
                "expectedRevisions": self.score_finalization_expected_revisions(
                    intent,
                    initial_state,
                    receipt_path,
                    prepared_at,
                ),
            }
            prepared = self.write_score_finalization_receipt(receipt_path, prepared)
            return self.apply_score_finalization_receipt(receipt_path, prepared)

    def queue_finalize_submission_record_score(self, arguments: dict) -> dict:
        return self.queue_finalize_score(
            arguments,
            expected_action=SCORE_FINALIZE_ACTION,
        )

    def queue_finalize_public_leaderboard_score(self, arguments: dict) -> dict:
        return self.queue_finalize_score(
            arguments,
            expected_action=PUBLIC_LEADERBOARD_FINALIZE_ACTION,
        )

    def leaderboard_snapshot(self) -> dict:
        result = subprocess.run(
            self.leaderboard_command(),
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        output = result.stdout or ""
        captured_at = now_iso()
        active = read_json(self.active_path, None)
        self.snapshot_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_log_path.write_text(
            (self.snapshot_log_path.read_text(encoding="utf-8", errors="replace") if self.snapshot_log_path.exists() else "")
            + f"\n===== {captured_at} mcp_snapshot exit={result.returncode} =====\n{output}\n",
            encoding="utf-8",
        )
        snapshot = parse_snapshot_output(output)
        parsed = parse_team_result(snapshot)
        freshness = self.snapshot_freshness(parsed, active)
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        snapshot_name = safe_snapshot_name(active.get("file") if isinstance(active, dict) else None)
        snapshot_path = self.snapshot_dir / f"{stamp}_mcp_snapshot_{snapshot_name}.json"
        payload = {
            "captured_at": captured_at,
            "reason": "mcp_leaderboard_snapshot",
            "active": active,
            "exitCode": result.returncode,
            "parsed": parsed,
            "freshness": freshness,
            "snapshot": snapshot,
            "outputTail": output[-4000:],
        }
        write_json(snapshot_path, payload)
        return {
            "ok": result.returncode == 0 and bool(snapshot),
            "exitCode": result.returncode,
            "fresh": freshness["fresh"],
            "freshness": freshness,
            "parsed": parsed,
            "snapshot": snapshot,
            "snapshotPath": str(snapshot_path),
            "outputTail": output[-2000:],
        }

    def queue_skip_score_wait(self, arguments: dict) -> dict:
        reason = str(arguments.get("reason") or "").strip()
        if not reason:
            raise ValueError("reason is required")
        selector = str(arguments.get("selector") or "").strip()
        with self.control_lock():
            self.assert_queue_mutation_allowed()
            doc = self.read_queue_doc()
            queue = doc["queue"]
            active = read_json(self.active_path, None)
            if not isinstance(active, dict):
                raise ValueError("NO_ACTIVE_SUBMISSION_LOCK")
            queue_index = active.get("queue_index")
            if queue_index in (None, ""):
                raise ValueError("ACTIVE_QUEUE_INDEX_REQUIRED")
            matches = [item for item in queue if str(item.get("index")) == str(queue_index)]
            if len(matches) != 1:
                raise ValueError("ACTIVE_QUEUE_INDEX_NOT_UNIQUE")
            item = matches[0]
            allowed_statuses = {"awaiting_score", "score_missed"}
            if active.get("status") not in allowed_statuses or item.get("status") != active.get("status"):
                raise ValueError("ACTIVE_NOT_SKIP_READY")
            item_accepted = str(item.get("acceptedAt") or "").strip()
            active_accepted = str(active.get("accepted_at") or "").strip()
            if not item_accepted or item_accepted != active_accepted:
                raise ValueError("ACTIVE_ACCEPTED_AT_MISMATCH")
            item_logical_id = str(item.get("logicalSubmissionId") or "").strip()
            active_logical_id = str(active.get("logical_submission_id") or "").strip()
            if not item_logical_id or item_logical_id != active_logical_id:
                raise ValueError("ACTIVE_LOGICAL_ID_MISMATCH")
            item_candidate_sha256 = self.queue_item_sha256(item)
            active_candidate_sha256 = str(active.get("candidate_sha256") or "").strip().lower()
            if not item_candidate_sha256 or item_candidate_sha256 != active_candidate_sha256:
                raise ValueError("ACTIVE_CANDIDATE_SHA256_MISMATCH")
            allowed_selectors = {str(queue_index), str(item.get("logicalSubmissionId") or "")}
            allowed_selectors.discard("")
            if selector and selector not in allowed_selectors:
                raise ValueError("SKIP_SELECTOR_IDENTITY_MISMATCH")
            blocking = [candidate for candidate in queue if candidate.get("status") in BLOCKING_QUEUE_STATUSES]
            if len(blocking) != 1 or blocking[0] is not item:
                raise ValueError("ACTIVE_BLOCKING_ITEM_MISMATCH")

            item["status"] = "skipped"
            item["skipReason"] = reason
            item["note"] = (
                f"{item.get('note') or ''}\nskipped score manually at {now_iso()}: {reason}"
            ).strip()[-1500:]
            self.save_queue_doc(doc)
            self.active_path.unlink(missing_ok=True)
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": now_iso(),
                            "event": "score.skipped_manual",
                            "queue_index": item.get("index"),
                            "file": item.get("name") or "",
                            "path": item.get("path") or "",
                            "logical_submission_id": item.get("logicalSubmissionId") or "",
                            "note": reason,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        return {
            "ok": True,
            "queueIndex": item.get("index"),
            "logicalSubmissionId": item.get("logicalSubmissionId") or "",
            "status": "skipped",
            "reason": reason,
        }

    def write(self, message: dict) -> None:
        with self.write_lock:
            self.output_stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.output_stream.flush()


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    JinyinsaiSubmitServer(sys.stdin, sys.stdout, sys.stderr).run()


if __name__ == "__main__":
    main()
