import io
import csv
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import hashlib
import zipfile

import pytest

from mak.aic.evidence_receipt import write_evidence_receipt
from mak.mcp import jinyinsai_submit as submit_module
from mak.mcp.jinyinsai_submit import (
    COMPETITION_SCOPE,
    JinyinsaiSubmitServer,
    LEGACY_SCORED_HISTORY_COMPATIBILITY_SCHEMA,
    PUBLIC_LEADERBOARD_SOURCE,
    PUBLIC_LEADERBOARD_URL,
    RESULT_FIELDS,
    SUBMISSION_RECORDS_COLLECTION_TRUNCATED,
    SUBMISSION_RECORDS_DEBUG_URL,
    SUBMISSION_RECORDS_HELPER,
    SUBMISSION_RECORDS_NODE_EXECUTABLE,
    SUBMISSION_CONTRACT_VERSION,
    TEAM_ID,
    TEAM_NAME,
    is_exact_public_leaderboard_url,
    parse_submission_datetime,
    parse_team_result,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-11 06:31:40", "2026-07-10T22:31:40+00:00"),
        ("2026-07-11T06:31:40", "2026-07-10T22:31:40+00:00"),
        ("2026-07-11 06:31:40.123", "2026-07-10T22:31:40.123000+00:00"),
        ("2026-07-11T06:31:40Z", "2026-07-11T06:31:40+00:00"),
        ("2026-07-11T06:31:40+08:00", "2026-07-10T22:31:40+00:00"),
    ],
)
def test_parse_submission_datetime_uses_beijing_for_naive_platform_times(value, expected):
    assert parse_submission_datetime(value).isoformat() == expected


def _server(root):
    (root / "data" / "test").mkdir(parents=True, exist_ok=True)
    (root / "data" / "test" / "a.jpg").write_text("fake", encoding="utf-8")
    (root / "data" / "train" / "0000").mkdir(parents=True, exist_ok=True)
    server = JinyinsaiSubmitServer(
        io.StringIO(),
        io.StringIO(),
        io.StringIO(),
        root=root,
        cdp_probe=lambda: {"ready": True, "reason": "TEST_CDP_READY"},
    )
    server.runner_command = lambda: ["trusted-node", "trusted-runner", "run"]
    server.leaderboard_command = lambda: [
        "trusted-node",
        "trusted-cdp",
        "leaderboard",
    ]
    return server


def _zip(path, name="pred_results.csv", body="a.jpg,0000\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, body)
    return path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance(tmp_path, archive, *, manifest_name="provenance.json", candidate_sha256=None, model_id="vit_base_patch32_clip_224.openai"):
    evidence = tmp_path / f"{manifest_name}.log"
    evidence.write_text(f"loaded model_id={model_id}\n", encoding="utf-8")
    manifest = tmp_path / manifest_name
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "experiment_id": "exp_vitb32",
                "candidate_zip": str(archive),
                "candidate_artifact_id": "candidate",
                "candidate_sha256": candidate_sha256 or _sha256(archive),
                "backbone": "CLIP ViT-B/32",
                "model_id": model_id,
                "evidence_files": [
                    {
                        "artifact_id": "model-evidence",
                        "path": str(evidence),
                        "sha256": _sha256(evidence),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_validate_submission_file_accepts_pred_results_zip(tmp_path):
    (tmp_path / "data" / "test").mkdir(parents=True)
    (tmp_path / "data" / "test" / "a.jpg").write_text("fake", encoding="utf-8")
    (tmp_path / "data" / "train" / "0000").mkdir(parents=True)
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")

    result = server.validate_submission_file({"file": str(archive)})

    assert result["ok"]
    assert result["archiveNames"] == ["pred_results.csv"]
    assert result["rows"] == 1


def test_validate_submission_file_rejects_missing_trusted_test_data(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    for entry in (tmp_path / "data" / "test").iterdir():
        entry.unlink()
    (tmp_path / "data" / "test").rmdir()

    with pytest.raises(ValueError, match="TRUSTED_TEST_DATA_MISSING"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_missing_trusted_train_data(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    (tmp_path / "data" / "train" / "0000").rmdir()
    (tmp_path / "data" / "train").rmdir()

    with pytest.raises(ValueError, match="TRUSTED_TRAIN_DATA_MISSING"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_excessive_zip_ratio(tmp_path, monkeypatch):
    server = _server(tmp_path)
    archive = _zip(
        tmp_path / "submissions" / "candidate.zip",
        body="a.jpg,0000" + (" " * 20000) + "\n",
    )
    monkeypatch.setattr(submit_module, "SUBMISSION_MAX_LINE_BYTES", 65536)
    monkeypatch.setattr(submit_module, "SUBMISSION_MAX_FIELD_CHARS", 65536)

    with pytest.raises(ValueError, match="ZIP_COMPRESSION_RATIO_TOO_HIGH"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_long_csv_line(tmp_path, monkeypatch):
    server = _server(tmp_path)
    archive = _zip(
        tmp_path / "submissions" / "candidate.zip",
        body="a.jpg," + ("0" * 200) + "\n",
    )
    monkeypatch.setattr(submit_module, "SUBMISSION_MAX_LINE_BYTES", 64)

    with pytest.raises(ValueError, match="CSV_LINE_TOO_LONG"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_row_limit(tmp_path, monkeypatch):
    server = _server(tmp_path)
    archive = _zip(
        tmp_path / "submissions" / "candidate.zip",
        body="a.jpg,0000\nb.jpg,0000\n",
    )
    monkeypatch.setattr(submit_module, "SUBMISSION_MAX_ROWS", 1)

    with pytest.raises(ValueError, match="CSV_TOO_MANY_ROWS"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_wrong_class_id(tmp_path):
    (tmp_path / "data" / "test").mkdir(parents=True)
    (tmp_path / "data" / "test" / "a.jpg").write_text("fake", encoding="utf-8")
    (tmp_path / "data" / "train" / "0000").mkdir(parents=True)
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip", body="a.jpg,9999\n")

    with pytest.raises(ValueError, match="INVALID_CLASS_ID"):
        server.validate_submission_file({"file": str(archive)})


def test_validate_submission_file_rejects_zip_without_pred_results(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip", name="wrong.csv")

    with pytest.raises(ValueError, match="ZIP_MUST_CONTAIN_ONLY_pred_results.csv"):
        server.validate_submission_file({"file": str(archive)})


def test_queue_push_front_writes_queue_without_submitting(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)

    result = server.queue_push({"files": [str(archive)], "provenance_manifests": [str(manifest)]}, front=True)
    with pytest.raises(ValueError, match="DUPLICATE_SUBMISSION_IDENTITY"):
        server.queue_push({"files": [str(archive)], "provenance_manifests": [str(manifest)]}, front=True)

    assert result["ok"]
    assert result["added"] == ["candidate.zip"]
    assert (tmp_path / "submissions" / "aicomp_submit_queue.json").exists()


def test_queue_push_requires_provenance_manifest(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")

    with pytest.raises(ValueError, match="PROVENANCE_MANIFEST_REQUIRED"):
        server.queue_push({"files": [str(archive)]}, front=True)


def test_queue_push_persists_validated_provenance_fields(tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)

    result = server.queue_push({"files": [str(archive)], "provenance_manifests": [str(manifest)]}, front=True)

    item = json.loads((tmp_path / "submissions" / "aicomp_submit_queue.json").read_text(encoding="utf-8"))["queue"][0]
    assert item["provenanceManifest"] == str(manifest.resolve())
    assert item["provenanceModelId"] == "vit_base_patch32_clip_224.openai"
    assert item["provenanceCandidateSha256"] == _sha256(archive)
    assert item["contentId"].startswith("sha256:")
    assert item["logicalSubmissionId"].endswith(":r0")
    assert item["provenanceValidatedAt"]
    assert result["queued"][0]["provenanceManifest"] == str(manifest.resolve())


def test_queue_runner_start_revalidates_queued_provenance_before_external_submit(monkeypatch, tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)
    server.queue_push({"files": [str(archive)], "provenance_manifests": [str(manifest)]}, front=True)
    intent = server.queue_status()["runnerStartIntent"]
    _zip(archive, body="a.jpg,0001\n")

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("queue_runner_start must fail before spawning the external runner")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    with pytest.raises(ValueError, match="PROVENANCE_CANDIDATE_SHA256_MISMATCH"):
        server.queue_runner_start({"confirm_real_submit": True, "intent": intent})


def test_queue_runner_start_requires_cdp_before_lease_or_attempt(monkeypatch, tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)
    server.queue_push(
        {"files": [str(archive)], "provenance_manifests": [str(manifest)]},
        front=True,
    )
    intent = server.queue_status()["runnerStartIntent"]
    server.cdp_probe = lambda: {
        "ready": False,
        "reason": "CDP_ENDPOINT_UNAVAILABLE",
    }
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not run without CDP"),
    )

    with pytest.raises(ValueError, match="CDP_UNAVAILABLE_BEFORE_RUNNER_START"):
        server.queue_runner_start({"confirm_real_submit": True, "intent": intent})

    assert not server.runner_lease_path.exists()
    assert not server.runner_state_path.exists()
    assert server.queue_status()["runnerStartIntent"] == intent


def test_queue_runner_start_releases_lease_when_initial_registry_write_fails(monkeypatch, tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)
    server.queue_push(
        {"files": [str(archive)], "provenance_manifests": [str(manifest)]},
        front=True,
    )
    intent = server.queue_status()["runnerStartIntent"]

    def fail_registry_write(_record):
        raise OSError("injected registry write failure")

    monkeypatch.setattr(server, "persist_runner_record", fail_registry_write)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not run"),
    )

    with pytest.raises(OSError, match="injected registry write failure"):
        server.queue_runner_start(
            {"confirm_real_submit": True, "intent": intent}
        )

    assert not server.runner_lease_path.exists()


def test_queue_runner_start_releases_lease_when_log_open_fails(monkeypatch, tmp_path):
    server = _server(tmp_path)
    archive = _zip(tmp_path / "submissions" / "candidate.zip")
    manifest = _provenance(tmp_path, archive)
    server.queue_push(
        {"files": [str(archive)], "provenance_manifests": [str(manifest)]},
        front=True,
    )
    intent = server.queue_status()["runnerStartIntent"]
    original_open = Path.open

    def fail_runner_log_open(path, *args, **kwargs):
        if path.name.startswith("aicomp_mcp_runner_stdout_"):
            raise OSError("injected runner log open failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_runner_log_open)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen must not run"),
    )

    with pytest.raises(OSError, match="injected runner log open failure"):
        server.queue_runner_start(
            {"confirm_real_submit": True, "intent": intent}
        )

    assert not server.runner_lease_path.exists()


def test_queue_runner_start_allows_bound_score_capture_without_legacy_provenance(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path = tmp_path / "submissions" / "legacy-active.zip"
    server.queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "queue": [
                    {
                        "index": 164,
                        "name": "legacy-active.zip",
                        "path": str(active_path),
                        "status": "awaiting_score",
                        "submittedAt": "2026-07-08T22:59:25.505Z",
                        "acceptedAt": "2026-07-08T22:59:26.657Z",
                        "artifactSha256": "a" * 64,
                        "logicalSubmissionId": "legacy-index-164",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "file": "legacy-active.zip",
                "path": str(active_path),
                    "queue_index": 164,
                    "logical_submission_id": "legacy-index-164",
                    "candidate_sha256": "a" * 64,
                    "accepted_at": "2026-07-08T22:59:26.657Z",
                "submitted_at": "2026-07-08T22:59:25.505Z",
            }
        ),
        encoding="utf-8",
    )

    class FakeProcess:
        pid = 43210

        def poll(self):
            return None

    popen_kwargs = {}

    def fake_popen(*_args, **kwargs):
        popen_kwargs.update(kwargs)
        return FakeProcess()

    monkeypatch.setenv("AICOMP_MCP_RUNNER_COMMAND_JSON", '["malicious-runner"]')
    monkeypatch.setenv("AICOMP_MCP_LEADERBOARD_COMMAND_JSON", '["malicious-board"]')
    monkeypatch.setenv("AICOMP_ROOT", "D:/malicious-root")
    monkeypatch.setenv("AICOMP_DEBUG_URL", "http://malicious.invalid:9999")
    monkeypatch.setenv("NODE_OPTIONS", "--require=malicious.js")
    monkeypatch.setenv("PATH", "D:/malicious-path")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    intent = server.queue_status()["runnerStartIntent"]
    result = server.queue_runner_start({"confirm_real_submit": False, "intent": intent})

    assert result["ok"]
    assert result["started"]
    assert result["pid"] == 43210
    assert popen_kwargs["env"]["AICOMP_RUN_MODE"] == "capture-only"
    assert popen_kwargs["env"]["AICOMP_EXPECTED_QUEUE_INDEX"] == "164"
    assert popen_kwargs["env"]["AICOMP_ROOT"] == str(server.root)
    assert popen_kwargs["env"]["AICOMP_DEBUG_URL"] == "http://127.0.0.1:9222"
    for inherited in (
        "AICOMP_MCP_RUNNER_COMMAND_JSON",
        "AICOMP_MCP_LEADERBOARD_COMMAND_JSON",
        "NODE_OPTIONS",
        "PATH",
    ):
        assert inherited not in popen_kwargs["env"]


def test_submit_tools_include_runner_as_explicit_real_submit_boundary(tmp_path):
    tools = _server(tmp_path).tools()
    names = {tool["name"] for tool in tools}
    runner = next(tool for tool in tools if tool["name"] == "queue_runner_start")

    assert "queue_push_front" in names
    assert "queue_runner_start" in names
    assert "leaderboard_snapshot" not in names
    assert "aicomp_submission_records_fetch" in names
    assert "aicomp_public_leaderboard_fetch" in names
    assert "queue_finalize_submission_record_score" in names
    assert "queue_finalize_public_leaderboard_score" in names
    assert "validate_submission_file" in names
    assert "allow_duplicate" not in next(tool for tool in tools if tool["name"] == "queue_push_front")["inputSchema"]["properties"]
    assert runner["inputSchema"]["required"] == ["confirm_real_submit", "intent"]


def test_queue_runner_watch_schema_supports_bounded_blocking_mode(tmp_path):
    tool = next(
        item
        for item in _server(tmp_path).tools()
        if item["name"] == "queue_runner_watch"
    )

    properties = tool["inputSchema"]["properties"]
    assert properties["wait_for_completion"] == {"type": "boolean"}
    assert properties["timeout_seconds"]["minimum"] == 1
    assert properties["timeout_seconds"]["maximum"] == 240


def test_queue_runner_watch_blocking_mode_owns_child_until_terminal(tmp_path):
    server = _server(tmp_path)

    class FakeProcess:
        def __init__(self):
            self.wait_timeout = None

        def wait(self, timeout=None):
            self.wait_timeout = timeout
            return 0

        def poll(self):
            return None

    process = FakeProcess()
    runner = {
        "runner_id": "runner-blocking",
        "pid": 43210,
        "stdout": str(tmp_path / "stdout.log"),
        "stderr": str(tmp_path / "stderr.log"),
        "startedAt": "2026-07-15T00:00:00Z",
        "process": process,
    }
    server.runners[runner["runner_id"]] = runner
    server.persist_runner_record(
        {key: value for key, value in runner.items() if key != "process"}
    )

    result = server.queue_runner_watch(
        {
            "runner_id": runner["runner_id"],
            "wait_for_completion": True,
            "timeout_seconds": 120,
        }
    )

    assert process.wait_timeout == 120
    assert result["terminal"] is True
    assert result["watching"] is False
    assert result["exitCode"] == 0
    assert result["source"] == "memory-blocking"
    persisted = server.read_runner_doc()["runners"][0]
    assert persisted["exitCode"] == 0
    assert persisted["phase"] == "finished"
    assert persisted["finishedAt"]


def test_blocking_watch_returns_only_identity_bound_terminal_queue_item(tmp_path):
    server = _server(tmp_path)
    candidate = tmp_path / "candidate.zip"
    candidate.write_bytes(b"candidate")
    candidate_sha256 = _sha256(candidate)
    queue_item = {
        "index": 9,
        "name": candidate.name,
        "path": str(candidate),
        "status": "scored",
        "provenanceCandidateSha256": candidate_sha256,
        "logicalSubmissionId": "logical-9",
        "score": "77.6825",
        "scoreTime": "2026-07-15 12:21:02",
        "leaderboardPublishTime": "07月15日 13时00分",
        "teamSubmitTime": "2026-07-15 12:16:59",
    }
    server.save_queue_doc({"schemaVersion": 3, "queue": [queue_item]})

    class FakeProcess:
        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    runner = {
        "runner_id": "runner-summary",
        "pid": 43212,
        "stdout": str(tmp_path / "stdout.log"),
        "stderr": str(tmp_path / "stderr.log"),
        "startedAt": "2026-07-15T00:00:00Z",
        "process": FakeProcess(),
        "intent": {
            "runnerId": "runner-summary",
            "queueIndex": 9,
            "candidateSha256": candidate_sha256,
            "logicalSubmissionId": "logical-9",
        },
    }
    server.runners[runner["runner_id"]] = runner

    result = server.queue_runner_watch(
        {
            "runner_id": runner["runner_id"],
            "wait_for_completion": True,
            "timeout_seconds": 120,
        }
    )

    assert result["finalQueueItemIssue"] == ""
    assert result["finalQueueItem"]["index"] == 9
    assert result["finalQueueItem"]["score"] == "77.6825"
    assert result["finalQueueItem"]["scoreTime"] == "2026-07-15 12:21:02"
    assert result["finalQueueItem"]["leaderboardPublishTime"] == "07月15日 13时00分"
    assert result["finalQueueItem"]["teamSubmitTime"] == "2026-07-15 12:16:59"


def test_queue_runner_watch_blocking_timeout_fails_closed(tmp_path):
    server = _server(tmp_path)

    class TimeoutProcess:
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("runner", timeout)

        def poll(self):
            return None

    server.runners["runner-timeout"] = {
        "runner_id": "runner-timeout",
        "pid": 43211,
        "stdout": str(tmp_path / "stdout.log"),
        "stderr": str(tmp_path / "stderr.log"),
        "startedAt": "2026-07-15T00:00:00Z",
        "process": TimeoutProcess(),
    }

    with pytest.raises(ValueError, match="RUNNER_BLOCKING_WATCH_TIMEOUT"):
        server.queue_runner_watch(
            {
                "runner_id": "runner-timeout",
                "wait_for_completion": True,
                "timeout_seconds": 120,
            }
        )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (PUBLIC_LEADERBOARD_URL, True),
        (
            "https://reg.aicomp.cn/special/phb/detail?stbh=4829238709759119431"
            "&id=4832828643476639839&rwId=4829238709759119407",
            True,
        ),
        ("https://reg.aicomp.cn/app/JSGLPT/639980063d903c241eb85102", False),
        ("https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842", False),
        (PUBLIC_LEADERBOARD_URL + "&extra=1", False),
        (PUBLIC_LEADERBOARD_URL + "#team", False),
    ],
)
def test_public_leaderboard_url_is_bound_to_exact_canonical_page(url, expected):
    assert is_exact_public_leaderboard_url(url) is expected


def test_queue_runner_start_requires_real_submit_confirmation(tmp_path):
    with pytest.raises(ValueError, match="RUNNER_INTENT_REQUIRED"):
        _server(tmp_path).queue_runner_start({})


def test_queue_runner_start_respects_persisted_running_pid(monkeypatch, tmp_path):
    monkeypatch.setattr(
        submit_module,
        "process_start_identity",
        lambda _pid: "process-current",
    )
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-existing",
                        "pid": os.getpid(),
                        "stdout": str(tmp_path / "stdout.log"),
                        "stderr": str(tmp_path / "stderr.log"),
                        "startedAt": "2026-07-08T00:00:00Z",
                        "processIdentity": "process-current",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = server.queue_runner_start({"confirm_real_submit": True})

    assert result["ok"]
    assert not result["started"]
    assert result["reason"] == "RUNNER_ALREADY_RUNNING"
    assert result["runners"][0]["runner_id"] == "runner-existing"


def test_queue_status_does_not_treat_reused_pid_as_live_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(
        submit_module,
        "process_start_identity",
        lambda _pid: "process-current",
    )
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-stale",
                        "pid": os.getpid(),
                        "stdout": str(tmp_path / "stdout.log"),
                        "stderr": str(tmp_path / "stderr.log"),
                        "startedAt": "2026-07-08T00:00:00Z",
                        "processIdentity": "process-old",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "idle"
    assert status["runners"][0]["running"] is False
    assert status["runners"][0]["processIdentityStatus"] == "pid_reused"


def test_queue_status_detects_legacy_runner_pid_reuse_from_start_time(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        submit_module,
        "process_start_epoch",
        lambda _pid: 1_800_000_000.0,
    )
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True)
    server.runner_state_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "runners": [
                    {
                        "runner_id": "runner-legacy-stale",
                        "pid": os.getpid(),
                        "stdout": str(tmp_path / "stdout.log"),
                        "stderr": str(tmp_path / "stderr.log"),
                        "startedAt": "2026-07-08T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    status = server.queue_status()

    assert status["state"] == "idle"
    assert status["runners"][0]["running"] is False
    assert status["runners"][0]["processIdentityStatus"] == "legacy_pid_reused"


def test_parse_team_result_extracts_configured_team_row():
    snapshot = {
        "text": "发布时间：07月02日 18时00分 3 AIC-2026-58579595 swpu_1 "
        "2026-07-02 17:52:58 78.2713 2026-07-02 17:56:02"
    }

    result = parse_team_result(snapshot)

    assert result["rank"] == "3"
    assert result["score"] == "78.2713"
    assert result["leaderboardPublishTime"] == "07月02日 18时00分"


def _leaderboard_stdout(team_submit_time="2026-07-08 17:02:26", score="78.1872", score_time="2026-07-08 17:06:03"):
    text = (
        "发布时间：07月08日 18时00分 1 AIC-2026-58579595 swpu_1 "
        f"{team_submit_time} {score} {score_time}"
    )
    return json.dumps({"text": text}, ensure_ascii=False)


def _write_active(server, tmp_path, **overrides):
    server.active_path.parent.mkdir(parents=True, exist_ok=True)
    active = {
        "status": "awaiting_score",
        "file": "pred_results_repro_champion512_tta_balanced.zip",
        "path": str(tmp_path / "submissions" / "pred_results_repro_champion512_tta_balanced.zip"),
        "queue_index": 164,
        "accepted_at": "2026-07-08T22:59:26.657Z",
        "submitted_at": "2026-07-08T22:59:25.505Z",
    }
    active.update(overrides)
    server.active_path.write_text(json.dumps(active), encoding="utf-8")
    return active


def _submission_records_stdout(records=None, **extra):
    payload = {
        "ok": True,
        "source": "aicomp_submission_records_cdp",
        "records": records if records is not None else [],
        "diagnostics": {
            "clicked": {"clicked": True, "text": "作品打分结果"},
            "collection": {"truncated": False},
        },
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _write_score_missed_case(
    server,
    tmp_path,
    *,
    queue_index=164,
    expected_publish_at="2026-07-08T23:00:00.000Z",
):
    archive = _zip(tmp_path / "submissions" / "pred_results_repro_champion512_tta_balanced.zip")
    candidate_sha256 = _sha256(archive)
    logical_id = f"jinyinsai:aicomp-2026:AIC-2026-58579595:{candidate_sha256}:r0"
    submitted_at = "2026-07-08T22:59:25.505Z"
    accepted_at = "2026-07-08T22:59:26.657Z"
    capture_key = (
        f"aicomp-score-window-v1|{queue_index}|{logical_id}|{candidate_sha256}|"
        f"{accepted_at}|{expected_publish_at}"
    )
    claim_dir = tmp_path / "submissions" / "score_capture_claims"
    claim_dir.mkdir(parents=True, exist_ok=True)
    claim_path = claim_dir / f"{hashlib.sha256(capture_key.encode()).hexdigest()}.json"
    claim_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "key": capture_key,
                "queueIndex": queue_index,
                "logicalSubmissionId": logical_id,
                "candidateSha256": candidate_sha256,
                "acceptedAt": accepted_at,
                "expectedPublishAt": expected_publish_at,
                "claimedAt": "2026-07-08T23:05:00.000Z",
                "runnerId": "runner-capture-once",
            }
        ),
        encoding="utf-8",
    )
    item = {
        "index": queue_index,
        "name": archive.name,
        "path": str(archive),
        "status": "score_missed",
        "submittedAt": submitted_at,
        "acceptedAt": accepted_at,
        "logicalSubmissionId": logical_id,
        "provenanceCandidateSha256": candidate_sha256,
        "scoreCaptureWindowKey": capture_key,
    }
    server.queue_path.write_text(
        json.dumps({"schemaVersion": 3, "queue": [item]}),
        encoding="utf-8",
    )
    server.active_path.write_text(
        json.dumps(
            {
                "schemaVersion": 3,
                "status": "score_missed",
                "queue_index": queue_index,
                "logical_submission_id": logical_id,
                "candidate_sha256": candidate_sha256,
                "file": archive.name,
                "path": str(archive),
                "submitted_at": submitted_at,
                "accepted_at": accepted_at,
                "expected_publish_at": expected_publish_at,
                "score_capture_window_key": capture_key,
                "score_capture_claim_path": str(claim_path),
                "score_capture_attempts": 1,
            }
        ),
        encoding="utf-8",
    )
    return item, claim_path


def _legacy_public_snapshot(row, **overrides):
    snapshot = {
        "time": row["query_time"],
        "url": PUBLIC_LEADERBOARD_URL,
        "title": "AICOMP 公共排行榜",
        "text": (
            f"发布时间：{row['leaderboard_publish_time']} "
            f"{row['team_rank']} {row['team_id']} {row['team_name']} "
            f"{row['team_submit_time']} {row['score']} {row['score_time']}"
        ),
    }
    snapshot.update(overrides)
    return snapshot


def _write_legacy_scored_history_compatibility(
    server,
    item_rows,
    *,
    snapshots=None,
):
    snapshots = snapshots or [
        _legacy_public_snapshot(row) for _item, row in item_rows
    ]
    server.snapshot_log_path.parent.mkdir(parents=True, exist_ok=True)
    server.snapshot_log_path.write_text(
        "\n".join(json.dumps(snapshot, ensure_ascii=False) for snapshot in snapshots)
        + "\n",
        encoding="utf-8",
    )
    entries = []
    for item, row in item_rows:
        identity = server.legacy_scored_history_queue_identity(item)
        source_evidence = {
            "queryTime": row["query_time"],
            "leaderboardPublishTime": row["leaderboard_publish_time"],
            "teamRank": row["team_rank"],
            "teamId": row["team_id"],
            "teamName": row["team_name"],
            "teamSubmitTime": row["team_submit_time"],
            "score": row["score"],
            "scoreTime": row["score_time"],
        }
        entries.append(
            {
                "queueIdentity": identity,
                "queue_identity_sha256": hashlib.sha256(
                    json.dumps(
                        identity,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "result_row_sha256": server.legacy_scored_history_result_row_sha256(
                    row
                ),
                "sourceEvidence": source_evidence,
                "source_evidence_sha256": hashlib.sha256(
                    json.dumps(
                        source_evidence,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
        )
    payload = {
        "schemaVersion": LEGACY_SCORED_HISTORY_COMPATIBILITY_SCHEMA,
        "contractVersion": SUBMISSION_CONTRACT_VERSION,
        "competitionScope": COMPETITION_SCOPE,
        "teamId": TEAM_ID,
        "teamName": TEAM_NAME,
        "publicLeaderboardUrl": PUBLIC_LEADERBOARD_URL,
        "reviewedAt": "2026-07-13T00:00:00Z",
        "sourceLog": {
            "path": str(server.snapshot_log_path),
            "sha256": _sha256(server.snapshot_log_path),
        },
        "entries": entries,
    }
    write_evidence_receipt(
        server.legacy_scored_history_compatibility_manifest_path,
        payload,
        root=server.root,
        verify_bound_files=True,
    )


def _write_results_rows(server, rows):
    server.results_path.parent.mkdir(parents=True, exist_ok=True)
    with server.results_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _append_legacy_scored_history(
    server,
    tmp_path,
    *,
    include_score_evidence=True,
):
    archive = _zip(tmp_path / "submissions" / "legacy-scored.zip")
    submitted_at = "2026-07-07T22:59:00.000Z"
    item = {
        "index": 1,
        "name": archive.name,
        "path": str(archive),
        "status": "scored",
        "submittedAt": submitted_at,
        "acceptedAt": "",
        "logicalSubmissionId": (
            "jinyinsai:aicomp-2026:AIC-2026-58579595:legacy-index-1"
        ),
        "score": "75.0000",
    }
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"].insert(0, item)
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    if include_score_evidence:
        row = {field: "" for field in RESULT_FIELDS}
        row.update(
            {
                "query_time": "2026-07-08T00:05:00.000Z",
                "file_index": "1",
                "file_name": archive.name,
                "file_path": str(archive),
                "submitted_at": submitted_at,
                "leaderboard_publish_time": "07月08日 08时00分",
                "team_rank": "3",
                "team_id": "AIC-2026-58579595",
                "team_name": "swpu_1",
                "team_submit_time": "2026-07-08 07:00:00",
                "score": "75.0000",
                "score_time": "2026-07-08 07:03:00",
                "snapshot_path": str(server.snapshot_log_path),
            }
        )
        _write_results_rows(server, [row])
        _write_legacy_scored_history_compatibility(server, [(item, row)])
    return item


def _fetch_strong_score_finalize_intent(monkeypatch, server, item):
    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "record_id": f"work-{item['index']}",
                        "attachment_filename": item["name"],
                        "status": "已打分",
                        "score": "78.4567",
                        "score_time": "2026-07-09 07:03:00",
                        "submit_time": "2026-07-09 06:59:26",
                        "team_id": "AIC-2026-58579595",
                        "team_name": "swpu_1",
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetched = server.aicomp_submission_records_fetch({"queue_index": item["index"]})
    assert fetched["scoreFinalizeReady"] is True, fetched
    return fetched["scoreFinalizeIntent"], Path(fetched["rawEvidencePath"])


def _public_leaderboard_stdout(
    *,
    url=PUBLIC_LEADERBOARD_URL,
    publish_time="07月09日 08时00分",
    team_submit_time="2026-07-09 06:59:26",
    score="78.4567",
    score_time="2026-07-09 07:03:00",
):
    text = (
        f"发布时间：{publish_time} 2 AIC-2026-58579595 swpu_1 "
        f"{team_submit_time} {score} {score_time}"
    )
    return json.dumps(
        {
            "time": "2026-07-09T00:05:00Z",
            "url": url,
            "title": "AICOMP 公共排行榜",
            "text": text,
        },
        ensure_ascii=False,
    )


def _fetch_public_score_finalize_intent(monkeypatch, server, **stdout_overrides):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        assert command[-1] == "leaderboard"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_public_leaderboard_stdout(**stdout_overrides),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    status = server.queue_status()
    recovery_intent = status["publicLeaderboardRecoveryIntent"]
    assert recovery_intent is not None, status
    fetched = server.aicomp_public_leaderboard_fetch(
        {
            "confirm_public_leaderboard_read": True,
            "intent": recovery_intent,
        }
    )
    return fetched, recovery_intent, calls


def test_submission_records_fetch_returns_structured_matched_record(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)
    monkeypatch.setenv("AICOMP_RECORDS_MAX_DETAILS", "1000")
    monkeypatch.setenv(
        "AICOMP_MCP_SUBMISSION_RECORDS_COMMAND_JSON",
        json.dumps(["malicious-records-helper"]),
    )
    monkeypatch.setenv("PATH", str(tmp_path / "poisoned-path"))
    monkeypatch.setenv("AICOMP_DEBUG_URL", "http://attacker.invalid:9999")
    monkeypatch.setenv("AICOMP_ROOT", str(tmp_path / "wrong-root"))
    monkeypatch.setenv("NODE_OPTIONS", "--require=malicious-node-hook.js")

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        assert Path(command[0]).resolve() == SUBMISSION_RECORDS_NODE_EXECUTABLE.resolve()
        assert Path(command[1]).resolve() == SUBMISSION_RECORDS_HELPER.resolve()
        assert "malicious-records-helper" not in command
        assert _kwargs["timeout"] == 180
        env = _kwargs["env"]
        assert env["AICOMP_DEBUG_URL"] == SUBMISSION_RECORDS_DEBUG_URL
        assert env["AICOMP_ROOT"] == str(server.root)
        assert "NODE_OPTIONS" not in env
        assert "PATH" not in env
        assert env["AICOMP_RECORDS_TARGET_QUEUE_INDEX"] == "164"
        assert env["AICOMP_RECORDS_TARGET_FILE"] == "pred_results_repro_champion512_tta_balanced.zip"
        assert env["AICOMP_RECORDS_TARGET_ACCEPTED_AT"] == "2026-07-08T22:59:26.657Z"
        assert env["AICOMP_RECORDS_MAX_DETAILS"] == "24"
        assert env["AICOMP_RECORDS_MAX_PAGES"] == "2"
        assert env["AICOMP_RECORDS_PAGE_WAIT_MS"] == "1500"
        assert env["AICOMP_RECORDS_DETAIL_WAIT_MS"] == "500"
        assert env["AICOMP_RECORDS_NAVIGATION_TIMEOUT_MS"] == "20000"
        assert env["AICOMP_RECORDS_READINESS_POLL_MS"] == "500"
        assert env["AICOMP_CDP_COMMAND_TIMEOUT_MS"] == "30000"
        assert env["AICOMP_RECORDS_RESET_AFTER_EMPTY_SEARCH"] == "0"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "record_id": "work-164",
                        "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip",
                        "status": "已打分",
                        "score": "78.4567",
                        "score_time": "2026-07-09 07:03:00",
                        "submit_time": "2026-07-09 06:59:26",
                        "team_id": "AIC-2026-58579595",
                        "team_name": "swpu_1",
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch(
        {"queue_index": 164, "timeout_seconds": 600}
    )

    assert result["ok"]
    assert result["matched"] is True, result
    assert result["matchReason"] == "TEAM_AND_SUBMIT_TIME_MATCH"
    assert result["matchedRecord"]["score"] == 78.4567
    assert result["matchedRecord"]["status"] == "已打分"
    assert result["scoreAttributionReady"] is True
    assert result["scoreAttributionReason"] == "STRONG_SUBMISSION_RECORD_WITH_SCORE"
    assert Path(result["rawEvidencePath"]).exists()


def test_generic_platform_index_is_not_local_queue_identity(tmp_path):
    server = _server(tmp_path)
    normalized = server.normalize_submission_record(
        {
            "source": "aicomp_submission_records_api",
            "index": 164,
            "attachment_filename": "candidate.zip",
            "score": "78.0",
        },
        tmp_path / "evidence.json",
    )

    assert normalized["queue_index"] is None
    match = server.match_submission_record(
        normalized,
        {
            "queue_index": 164,
            "file": "candidate.zip",
            "sha256": "a" * 64,
            "accepted_at": "2026-07-09T00:00:00Z",
            "submitted_at": "2026-07-09T00:00:00Z",
        },
    )
    assert match["matched"] is False
    assert match["reason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"


def test_submission_records_fetch_holds_exclusive_browser_lease(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        owner = json.loads(
            (server.runner_lease_path / "owner.json").read_text(encoding="utf-8")
        )
        assert owner["intent"]["action"] == "inspect_submission_records"
        assert owner["intent"]["queueIndex"] == 164
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout([]),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    server.aicomp_submission_records_fetch({"queue_index": 164})

    assert not server.runner_lease_path.exists()


def test_submission_records_fetch_refuses_active_runner_lease(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)
    server.runner_lease_path.mkdir(parents=True)
    (server.runner_lease_path / "owner.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "token": "active-runner-token",
                "pid": os.getpid(),
                "phase": "running",
                "intent": {"action": "submit_candidate", "queueIndex": 164},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shared Chrome page must not be touched")
        ),
    )

    with pytest.raises(ValueError, match="SUBMISSION_RECORDS_BROWSER_BUSY"):
        server.aicomp_submission_records_fetch({"queue_index": 164})


def test_runner_and_leaderboard_commands_ignore_environment_and_bind_helpers(
    monkeypatch, tmp_path
):
    node = tmp_path / "node.exe"
    tools = tmp_path / "tools"
    runner = tools / "aicomp_submit_queue.mjs"
    cdp = tools / "aicomp_cdp.mjs"
    manifest = tools / "aicomp_manifest.mjs"
    tools.mkdir(parents=True)
    node.write_bytes(b"node")
    runner.write_bytes(b"runner")
    cdp.write_bytes(b"cdp")
    manifest.write_bytes(b"manifest")
    monkeypatch.setattr(submit_module, "AICOMP_NODE_EXECUTABLE", node)
    monkeypatch.setattr(
        submit_module, "AICOMP_RUNNER_HELPER_SHA256", _sha256(runner)
    )
    monkeypatch.setattr(
        submit_module, "AICOMP_CDP_HELPER_SHA256", _sha256(cdp)
    )
    monkeypatch.setattr(
        submit_module, "AICOMP_MANIFEST_HELPER_SHA256", _sha256(manifest)
    )
    monkeypatch.setenv("AICOMP_MCP_RUNNER_COMMAND_JSON", '["malicious-runner"]')
    monkeypatch.setenv(
        "AICOMP_MCP_LEADERBOARD_COMMAND_JSON", '["malicious-leaderboard"]'
    )
    server = JinyinsaiSubmitServer(
        io.StringIO(), io.StringIO(), io.StringIO(), root=tmp_path
    )

    assert server.runner_command() == [
        str(node.resolve()),
        str(runner.resolve()),
        "run",
    ]
    assert server.leaderboard_command() == [
        str(node.resolve()),
        str(cdp.resolve()),
        "leaderboard",
    ]


@pytest.mark.parametrize(
    "helper_name",
    ["aicomp_submit_queue.mjs", "aicomp_cdp.mjs", "aicomp_manifest.mjs"],
)
def test_runner_command_rejects_any_tampered_transitive_helper(
    monkeypatch, tmp_path, helper_name
):
    node = tmp_path / "node.exe"
    tools = tmp_path / "tools"
    runner = tools / "aicomp_submit_queue.mjs"
    cdp = tools / "aicomp_cdp.mjs"
    manifest = tools / "aicomp_manifest.mjs"
    tools.mkdir(parents=True)
    node.write_bytes(b"node")
    runner.write_bytes(b"runner")
    cdp.write_bytes(b"cdp")
    manifest.write_bytes(b"manifest")
    monkeypatch.setattr(submit_module, "AICOMP_NODE_EXECUTABLE", node)
    monkeypatch.setattr(
        submit_module, "AICOMP_RUNNER_HELPER_SHA256", _sha256(runner)
    )
    monkeypatch.setattr(
        submit_module, "AICOMP_CDP_HELPER_SHA256", _sha256(cdp)
    )
    monkeypatch.setattr(
        submit_module, "AICOMP_MANIFEST_HELPER_SHA256", _sha256(manifest)
    )
    (tools / helper_name).write_bytes(b"tampered")
    server = JinyinsaiSubmitServer(
        io.StringIO(), io.StringIO(), io.StringIO(), root=tmp_path
    )

    with pytest.raises(ValueError, match="AICOMP_RUNNER_TOOLCHAIN_UNTRUSTED"):
        server.runner_command()


def test_reserve_runner_lease_rolls_back_ownerless_directory_on_owner_write_failure(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True, exist_ok=True)
    original_write_json = submit_module.write_json

    def fail_owner_write(path, payload):
        if path == server.lock_owner_path(server.runner_lease_path):
            raise OSError("injected owner write failure")
        return original_write_json(path, payload)

    monkeypatch.setattr(submit_module, "write_json", fail_owner_write)

    with pytest.raises(OSError, match="injected owner write failure"):
        server.reserve_runner_lease({"action": "inspect_submission_records"})

    assert not server.runner_lease_path.exists()


def test_control_lock_rolls_back_ownerless_directory_on_owner_write_failure(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    server.submissions.mkdir(parents=True, exist_ok=True)
    original_write_json = submit_module.write_json

    def fail_owner_write(path, payload):
        if path == server.lock_owner_path(server.control_lock_path):
            raise OSError("injected control owner write failure")
        return original_write_json(path, payload)

    monkeypatch.setattr(submit_module, "write_json", fail_owner_write)

    with pytest.raises(OSError, match="injected control owner write failure"):
        with server.control_lock():
            raise AssertionError("control body must not run")

    assert not server.control_lock_path.exists()


def test_submission_records_lease_update_failure_releases_reserved_lease(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)

    def fail_update(*_args, **_kwargs):
        raise OSError("injected lease update failure")

    monkeypatch.setattr(server, "update_runner_lease", fail_update)
    target = {
        "queue_item": {"logicalSubmissionId": "logical-164"},
        "queue_index": 164,
        "sha256": "a" * 64,
        "file": "candidate.zip",
    }

    with pytest.raises(OSError, match="injected lease update failure"):
        with server.submission_records_browser_lease(target):
            raise AssertionError("lease update failure must precede the body")

    assert not server.runner_lease_path.exists()
    assert not server.control_lock_path.exists()


def test_submission_records_command_rejects_unbound_helper(monkeypatch, tmp_path):
    server = _server(tmp_path)
    helper = tmp_path / "malicious-aicomp-cdp.mjs"
    helper.write_text("console.log('malicious')", encoding="utf-8")
    monkeypatch.setattr(submit_module, "SUBMISSION_RECORDS_HELPER", helper)

    with pytest.raises(ValueError, match="SUBMISSION_RECORDS_HELPER_UNTRUSTED"):
        server.submission_records_command()


def test_submission_records_truncation_never_matches_or_creates_finalize_intent(
    monkeypatch,
    tmp_path,
):
    server = _server(tmp_path)
    _write_active(server, tmp_path)
    record = {
        "source": "aicomp_submission_records_table",
        "record_id": "work-164",
        "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip",
        "status": "已打分",
        "score": "78.4567",
        "score_time": "2026-07-09 07:03:00",
        "submit_time": "2026-07-09 06:59:26",
        "team_id": "AIC-2026-58579595",
        "team_name": "swpu_1",
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [record],
                diagnostics={
                    "clicked": {"clicked": True},
                    "collection": {
                        "truncated": True,
                        "endReason": "MAX_DETAILS_REACHED",
                    },
                },
            ),
        ),
    )

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["ok"] is False
    assert result["reason"] == SUBMISSION_RECORDS_COLLECTION_TRUNCATED
    assert result["matched"] is False
    assert result["matchedRecord"] is None
    assert result["scoreAttributionReady"] is False
    assert result["scoreFinalizeReady"] is False
    assert result["scoreFinalizeIntent"] is None
    with pytest.raises(
        ValueError,
        match=SUBMISSION_RECORDS_COLLECTION_TRUNCATED,
    ):
        server.score_finalize_intent_from_evidence(Path(result["rawEvidencePath"]))


def test_submission_record_target_explicit_index_never_borrows_active_identity(tmp_path):
    server = _server(tmp_path)
    active_item, _claim_path = _write_score_missed_case(server, tmp_path)
    legacy_archive = _zip(tmp_path / "submissions" / "legacy-index-22.zip")
    legacy_item = {
        "index": 22,
        "name": legacy_archive.name,
        "path": str(legacy_archive),
        "status": "scored",
        "submittedAt": "2026-06-17T13:06:52.292Z",
        "acceptedAt": "",
        "logicalSubmissionId": "legacy-index-22",
        "artifactSha256": _sha256(legacy_archive),
        "score": "75.3475",
    }
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"].insert(0, legacy_item)
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    target = server.submission_record_target({"queue_index": 22})

    assert target["queue_index"] == 22
    assert target["file"] == legacy_item["name"]
    assert target["path"] == legacy_item["path"]
    assert target["sha256"] == legacy_item["artifactSha256"]
    assert target["submitted_at"] == legacy_item["submittedAt"]
    assert target["accepted_at"] == ""
    assert target["queue_item"]["index"] == 22
    assert target["active"]["queue_index"] == active_item["index"]
    assert target["active"]["path"] != target["path"]


def test_submission_record_target_missing_explicit_index_never_falls_back_to_active(
    tmp_path,
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)

    with pytest.raises(ValueError, match="QUEUE_ITEM_NOT_FOUND"):
        server.submission_record_target({"queue_index": 22})


def test_submission_record_target_rejects_file_mismatch_for_explicit_index(tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)

    with pytest.raises(
        ValueError,
        match="SUBMISSION_RECORD_TARGET_FILE_MISMATCH",
    ):
        server.submission_record_target(
            {"queue_index": item["index"], "file": "different.zip"}
        )


@pytest.mark.parametrize(
    "override",
    [
        {"submitted_at": "2099-01-01T00:00:00Z"},
        {"accepted_at": "2099-01-01T00:00:00Z"},
    ],
)
def test_submission_record_target_rejects_time_override_for_explicit_queue_item(
    tmp_path,
    override,
):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)

    with pytest.raises(ValueError, match="SUBMISSION_RECORD_TARGET_TIME_MISMATCH"):
        server.submission_record_target({"queue_index": item["index"], **override})


def test_submission_record_target_active_index_fallback_is_fully_bound(tmp_path):
    server = _server(tmp_path)
    active = _write_active(server, tmp_path)

    target = server.submission_record_target(
        {
            "queue_index": active["queue_index"],
            "file": active["file"],
            "submitted_at": active["submitted_at"],
            "accepted_at": active["accepted_at"],
        }
    )

    assert target["path"] == active["path"]
    assert target["submitted_at"] == active["submitted_at"]
    assert target["accepted_at"] == active["accepted_at"]
    with pytest.raises(ValueError, match="SUBMISSION_RECORD_TARGET_FILE_MISMATCH"):
        server.submission_record_target(
            {"queue_index": active["queue_index"], "file": "different.zip"}
        )
    with pytest.raises(ValueError, match="SUBMISSION_RECORD_TARGET_TIME_MISMATCH"):
        server.submission_record_target(
            {
                "queue_index": active["queue_index"],
                "accepted_at": "2099-01-01T00:00:00Z",
            }
        )


def test_submission_records_fetch_does_not_borrow_score_from_another_record(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip",
                        "status": "待打分",
                    },
                    {
                        "source": "aicomp_submission_records_table",
                        "attachment_filename": "other.zip",
                        "score": "99.0",
                        "score_time": "2026-07-09 07:03:00",
                    },
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["matched"] is False
    assert result["matchedRecord"] is None
    assert result["scoreAttributionReady"] is False
    assert result["scoreAttributionReason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"


def test_submission_records_fetch_rejects_filename_only_identity(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip",
                        "status": "已打分",
                        "score": "99.0",
                        "score_time": "2026-07-09 07:03:00",
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["matched"] is False
    assert result["matchedRecord"] is None
    assert result["matchReason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"
    assert result["scoreAttributionReady"] is False


def test_submission_records_fetch_rejects_fuzzy_filename_overlap(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip.old",
                        "score": "99.0",
                        "score_time": "2026-07-09 07:03:00",
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["matched"] is False
    assert result["scoreAttributionReady"] is False


def test_submission_records_fetch_fails_closed_on_multiple_identity_matches(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {"source": "aicomp_submission_records_table", "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip", "submit_time": "2026-07-09 06:59:26", "score": "79.0", "score_time": "2026-07-09 07:03:00"},
                    {"source": "aicomp_submission_records_table", "attachment_filename": "pred_results_repro_champion512_tta_balanced.zip", "submit_time": "2026-07-09 06:59:27", "status": "待打分"},
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["matched"] is False
    assert result["matchedRecord"] is None
    assert result["scoreAttributionReady"] is False
    assert result["scoreAttributionReason"] == "AMBIGUOUS_MATCHING_SUBMISSION_RECORDS"


def test_submission_records_fetch_rejects_public_leaderboard_as_per_submission_source(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "public_leaderboard",
                        "team_id": "AIC-2026-58579595",
                        "team_name": "swpu_1",
                        "teamSubmitTime": "2026-07-08 21:52:10",
                        "score": "92.0014",
                    }
                ],
                source="public_leaderboard",
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["ok"] is False
    assert result["reason"] == "PER_SUBMISSION_SOURCE_UNAVAILABLE"
    assert result["sourceReason"] == "PUBLIC_LEADERBOARD_SOURCE_REJECTED"
    assert result["records"] == []
    assert result["matched"] is False


def test_submission_records_fetch_reports_source_gap(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            3,
            stdout=json.dumps(
                {
                    "ok": False,
                    "source": "aicomp_submission_records_cdp",
                    "reason": "PER_SUBMISSION_SOURCE_UNAVAILABLE",
                    "diagnostics": {"error": "encrypted or hidden API"},
                    "records": [],
                }
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["ok"] is False
    assert result["reason"] == "PER_SUBMISSION_SOURCE_UNAVAILABLE"
    assert result["records"] == []
    assert result["diagnostics"]["error"] == "encrypted or hidden API"
    assert Path(result["rawEvidencePath"]).exists()


def test_submission_records_fetch_converts_empty_ok_payload_to_source_gap(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "source": "aicomp_submission_records_cdp",
                    "reason": "OK",
                    "diagnostics": {"after": {"text": "作品打分结果 共 178 条数据"}},
                    "records": [],
                },
                ensure_ascii=False,
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["ok"] is False
    assert result["reason"] == "PER_SUBMISSION_SOURCE_UNAVAILABLE"
    assert result["records"] == []
    assert result["matched"] is False


def test_submission_records_fetch_propagates_fields_insufficient_without_no_match(monkeypatch, tmp_path):
    server = _server(tmp_path)
    _write_active(server, tmp_path)

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            4,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "team_id": "AIC-2026-58579595",
                        "team_name": "swpu_1",
                        "status": "已打分",
                    }
                ],
                ok=False,
                reason="PER_SUBMISSION_FIELDS_INSUFFICIENT",
                sourceAvailable=True,
                fieldsSufficient=False,
                diagnostics={
                    "fieldGap": {
                        "gaps": ["NO_TARGET_COMPARABLE_FIELD", "NO_SCORE_OR_SCORE_TIME_FIELD"]
                    }
                },
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert result["ok"] is False
    assert result["reason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"
    assert result["sourceReason"] == "FIELDS_INSUFFICIENT"
    assert result["matched"] is False
    assert result["matchReason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"
    assert result["records"][0]["status"] == "已打分"
    assert result["reason"] != "NO_MATCHING_SUBMISSION_RECORD"


def test_score_missed_can_finalize_from_exact_strong_submission_record(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, claim_path = _write_score_missed_case(server, tmp_path)
    claim_before = claim_path.read_bytes()

    def fake_run(command, **_kwargs):
        assert command[-1] == "submission-records"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "record_id": "work-164",
                        "attachment_filename": item["name"],
                        "status": "已打分",
                        "score": "78.4567",
                        "score_time": "2026-07-09 07:03:00",
                        "submit_time": "2026-07-09 06:59:26",
                        "team_id": "AIC-2026-58579595",
                        "team_name": "swpu_1",
                    }
                ]
            ),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    fetched = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert fetched["scoreFinalizeReady"] is True, fetched
    intent = fetched["scoreFinalizeIntent"]
    assert intent["action"] == "finalize_submission_record_score"
    assert intent["recordId"] == "work-164"
    assert intent["claimSha256"] == _sha256(claim_path)
    assert intent["evidenceSha256"] == _sha256(Path(fetched["rawEvidencePath"]))

    finalized = server.queue_finalize_submission_record_score(
        {"confirm_score_attribution": True, "intent": intent}
    )

    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    receipt = json.loads(Path(finalized["finalizationReceipt"]).read_text(encoding="utf-8"))
    receipt_sidecar = Path(finalized["finalizationReceipt"]).with_suffix(".sha256")
    result_rows = list(csv.DictReader(server.results_path.open("r", encoding="utf-8")))
    events = [json.loads(line) for line in server.events_path.read_text(encoding="utf-8").splitlines()]
    assert finalized["status"] == "scored"
    assert finalized["captureClaimPreserved"] is True
    assert queue_item["status"] == "scored"
    assert queue_item["score"] == "78.4567"
    assert queue_item["scoreEvidenceSource"] == "aicomp_submission_records_table"
    assert queue_item["scoreRecordId"] == "work-164"
    assert not server.active_path.exists()
    assert claim_path.read_bytes() == claim_before
    assert receipt["schemaVersion"] == 3
    assert receipt["state"] == "committed"
    assert receipt["phase"] == "committed"
    assert receipt_sidecar.read_text(encoding="ascii") == _sha256(Path(finalized["finalizationReceipt"])) + "\n"
    assert result_rows[-1]["file_index"] == "164"
    assert result_rows[-1]["score"] == "78.4567"
    assert result_rows[-1]["snapshot_path"] == fetched["rawEvidencePath"]
    assert events[-1]["event"] == "score.captured_submission_record"

    receipt_before = Path(finalized["finalizationReceipt"]).read_bytes()
    sidecar_before = receipt_sidecar.read_bytes()
    replay = server.queue_finalize_submission_record_score(
        {"confirm_score_attribution": True, "intent": intent}
    )
    assert replay["alreadyFinalized"] is True
    assert Path(finalized["finalizationReceipt"]).read_bytes() == receipt_before
    assert receipt_sidecar.read_bytes() == sidecar_before
    assert len(list(csv.DictReader(server.results_path.open("r", encoding="utf-8")))) == 1


def test_score_finalization_recovers_receipt_after_sidecar_write_crash(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    intent, _evidence_path = _fetch_strong_score_finalize_intent(
        monkeypatch, server, item
    )
    receipt_path = server.score_finalization_receipt_path(intent["finalizationKey"])
    sidecar_path = receipt_path.with_suffix(".sha256")
    original_writer = submit_module.write_evidence_receipt
    crashed = False

    def crash_after_receipt_replace(path, payload, **_kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            raise submit_module.EvidenceReceiptError(
                "injected crash before sidecar replace"
            )
        return original_writer(path, payload, **_kwargs)

    monkeypatch.setattr(
        submit_module, "write_evidence_receipt", crash_after_receipt_replace
    )
    with pytest.raises(ValueError, match="SCORE_FINALIZATION_RECEIPT_WRITE_FAILED"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": intent}
        )
    assert receipt_path.is_file()
    assert not sidecar_path.exists()
    monkeypatch.setattr(submit_module, "write_evidence_receipt", original_writer)

    replay = server.queue_finalize_submission_record_score(
        {"confirm_score_attribution": True, "intent": intent}
    )

    assert replay["alreadyFinalized"] is False
    assert replay["status"] == "scored"
    assert sidecar_path.read_text(encoding="ascii") == _sha256(receipt_path) + "\n"


def test_score_missed_can_finalize_from_new_public_leaderboard_window(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, original_claim_path = _write_score_missed_case(server, tmp_path)
    original_claim_before = original_claim_path.read_bytes()

    fetched, recovery_intent, calls = _fetch_public_score_finalize_intent(
        monkeypatch, server
    )

    assert fetched["ok"] is True, fetched
    assert fetched["source"] == PUBLIC_LEADERBOARD_SOURCE
    assert fetched["publicLeaderboardUrl"] == PUBLIC_LEADERBOARD_URL
    assert fetched["scoreFinalizeReady"] is True
    assert len(calls) == 1
    recovery_consumption_path = Path(fetched["recoveryConsumptionPath"])
    recovery_consumption_before = recovery_consumption_path.read_bytes()
    recovery_consumption_sidecar_before = recovery_consumption_path.with_suffix(
        ".sha256"
    ).read_bytes()
    intent = fetched["scoreFinalizeIntent"]
    assert intent["action"] == "finalize_public_leaderboard_late_publication_score"
    assert intent["leaderboardPublishTime"] == "07月09日 08时00分"
    assert intent["recordSubmitTime"] == "2026-07-09 06:59:26"
    assert intent["teamRank"] == "2"
    assert intent["scoreText"] == "78.4567"
    assert intent["recoveryKey"] == recovery_intent["recoveryKey"]
    recovery_claim_path = Path(fetched["recoveryClaimPath"])
    recovery_claim_before = recovery_claim_path.read_bytes()

    def no_second_network_call(*_args, **_kwargs):
        raise AssertionError("captured recovery claim must replay without another leaderboard read")

    monkeypatch.setattr(subprocess, "run", no_second_network_call)
    replay_fetch = server.aicomp_public_leaderboard_fetch(
        {
            "confirm_public_leaderboard_read": True,
            "intent": recovery_intent,
        }
    )
    assert replay_fetch["alreadyCaptured"] is True
    assert replay_fetch["scoreFinalizeIntent"] == intent

    finalized = server.queue_finalize_public_leaderboard_score(
        {"confirm_score_attribution": True, "intent": intent}
    )

    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    result_rows = list(csv.DictReader(server.results_path.open("r", encoding="utf-8")))
    events = [json.loads(line) for line in server.events_path.read_text(encoding="utf-8").splitlines()]
    assert finalized["status"] == "scored"
    assert finalized["captureClaimPreserved"] is True
    assert finalized["recoveryClaimPreserved"] is True
    assert finalized["recoveryConsumptionPreserved"] is True
    assert finalized["leaderboardPublishTime"] == "07月09日 08时00分"
    assert queue_item["status"] == "scored"
    assert queue_item["score"] == "78.4567"
    assert queue_item["scoreEvidenceSource"] == PUBLIC_LEADERBOARD_SOURCE
    assert queue_item["scoreLeaderboardUrl"] == PUBLIC_LEADERBOARD_URL
    assert queue_item["leaderboardPublishTime"] == "07月09日 08时00分"
    assert queue_item["teamSubmitTime"] == "2026-07-09 06:59:26"
    assert queue_item["teamRank"] == "2"
    assert queue_item["scoreRecoveryClaimPath"] == str(recovery_claim_path)
    assert not server.active_path.exists()
    assert original_claim_path.read_bytes() == original_claim_before
    assert recovery_claim_path.read_bytes() == recovery_claim_before
    assert recovery_consumption_path.read_bytes() == recovery_consumption_before
    assert (
        recovery_consumption_path.with_suffix(".sha256").read_bytes()
        == recovery_consumption_sidecar_before
    )
    assert result_rows[-1]["file_index"] == str(item["index"])
    assert result_rows[-1]["leaderboard_publish_time"] == "07月09日 08时00分"
    assert result_rows[-1]["team_rank"] == "2"
    assert result_rows[-1]["team_submit_time"] == "2026-07-09 06:59:26"
    assert events[-1]["event"] == "score.captured_public_leaderboard_recovery"
    assert events[-1]["leaderboard_url"] == PUBLIC_LEADERBOARD_URL

    receipt_path = Path(finalized["finalizationReceipt"])
    receipt_before = receipt_path.read_bytes()
    sidecar_before = receipt_path.with_suffix(".sha256").read_bytes()
    replay = server.queue_finalize_public_leaderboard_score(
        {"confirm_score_attribution": True, "intent": intent}
    )
    assert replay["alreadyFinalized"] is True
    assert receipt_path.read_bytes() == receipt_before
    assert receipt_path.with_suffix(".sha256").read_bytes() == sidecar_before
    assert len(list(csv.DictReader(server.results_path.open("r", encoding="utf-8")))) == 1


def test_public_leaderboard_minute_label_allows_score_within_same_minute(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)

    fetched, _recovery_intent, calls = _fetch_public_score_finalize_intent(
        monkeypatch,
        server,
        publish_time="07月09日 08时00分",
        score_time="2026-07-09 08:00:42",
    )

    assert len(calls) == 1
    assert fetched["ok"] is True, fetched
    assert fetched["scoreFinalizeReady"] is True


def test_public_leaderboard_timeout_is_bounded_below_outer_mcp_deadline(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    intent = server.queue_status()["publicLeaderboardRecoveryIntent"]

    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == 240
        assert kwargs["env"]["AICOMP_ROOT"] == str(server.root)
        assert kwargs["env"]["AICOMP_DEBUG_URL"] == "http://127.0.0.1:9222"
        assert "AICOMP_MCP_LEADERBOARD_COMMAND_JSON" not in kwargs["env"]
        assert "NODE_OPTIONS" not in kwargs["env"]
        assert "PATH" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout=_public_leaderboard_stdout())

    monkeypatch.setenv("AICOMP_MCP_LEADERBOARD_COMMAND_JSON", '["malicious"]')
    monkeypatch.setenv("NODE_OPTIONS", "--require=malicious.js")
    monkeypatch.setenv("PATH", "D:/malicious-path")
    monkeypatch.setattr(subprocess, "run", fake_run)
    fetched = server.aicomp_public_leaderboard_fetch(
        {
            "confirm_public_leaderboard_read": True,
            "intent": intent,
            "timeout_seconds": 9999,
        }
    )

    assert fetched["ok"] is True
    tools = {tool["name"]: tool for tool in server.tools()}
    assert (
        tools["aicomp_submission_records_fetch"]["inputSchema"]["properties"]
        ["timeout_seconds"]["maximum"]
        == 180
    )
    assert (
        tools["aicomp_public_leaderboard_fetch"]["inputSchema"]["properties"]
        ["timeout_seconds"]["maximum"]
        == 240
    )


def test_public_leaderboard_recovery_rejects_tampered_original_claim_time(tmp_path):
    server = _server(tmp_path)
    _item, claim_path = _write_score_missed_case(server, tmp_path)
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["expectedPublishAt"] = "2026-07-08T22:00:00.000Z"
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "SCORE_CAPTURE_CLAIM_EXPECTED_PUBLISH_AT_MISMATCH"
    )


def test_public_leaderboard_recovery_claim_deletion_never_reopens_network(
    monkeypatch, tmp_path
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    fetched, recovery_intent, calls = _fetch_public_score_finalize_intent(
        monkeypatch, server
    )
    assert fetched["scoreFinalizeReady"] is True
    assert len(calls) == 1
    recovery_claim_path = Path(fetched["recoveryClaimPath"])
    consumption_path = Path(fetched["recoveryConsumptionPath"])
    consumption_before = consumption_path.read_bytes()
    consumption_sidecar_before = consumption_path.with_suffix(".sha256").read_bytes()
    recovery_claim_path.unlink()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("consumed recovery must never perform a second network read")
        ),
    )
    with pytest.raises(
        ValueError,
        match="PUBLIC_LEADERBOARD_RECOVERY_CLAIM_MISSING_AFTER_CONSUMPTION",
    ):
        server.aicomp_public_leaderboard_fetch(
            {
                "confirm_public_leaderboard_read": True,
                "intent": recovery_intent,
            }
        )

    assert consumption_path.read_bytes() == consumption_before
    assert consumption_path.with_suffix(".sha256").read_bytes() == consumption_sidecar_before


@pytest.mark.parametrize(
    ("expected_publish_at", "publish_time", "score_time"),
    [
        ("2026-07-09T01:00:00.000Z", "07月09日 08时00分", "2026-07-09 07:03:00"),
        ("2026-07-08T23:00:00.000Z", "07月09日 07时00分", "2026-07-09 06:59:59"),
    ],
)
def test_public_leaderboard_recovery_rejects_old_or_same_publication_window(
    monkeypatch, tmp_path, expected_publish_at, publish_time, score_time
):
    server = _server(tmp_path)
    _write_score_missed_case(
        server, tmp_path, expected_publish_at=expected_publish_at
    )

    fetched, recovery_intent, calls = _fetch_public_score_finalize_intent(
        monkeypatch,
        server,
        publish_time=publish_time,
        score_time=score_time,
    )

    assert len(calls) == 1
    assert fetched["ok"] is False
    assert fetched["reason"] == "PUBLIC_LEADERBOARD_NOT_A_NEW_PUBLICATION_WINDOW"
    assert fetched["scoreFinalizeReady"] is False
    assert Path(fetched["recoveryClaimPath"]).is_file()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failed recovery claim must not be retried")
        ),
    )
    replay = server.aicomp_public_leaderboard_fetch(
        {
            "confirm_public_leaderboard_read": True,
            "intent": recovery_intent,
        }
    )
    assert replay["reason"] == "PUBLIC_LEADERBOARD_RECOVERY_ALREADY_CONSUMED"
    assert replay["claimState"] == "failed"


@pytest.mark.parametrize(
    ("stdout_overrides", "expected_reason"),
    [
        (
            {"url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842"},
            "PUBLIC_LEADERBOARD_URL_MISMATCH",
        ),
        (
            {"team_submit_time": "2026-07-09 06:57:00"},
            "PUBLIC_LEADERBOARD_TEAM_SUBMIT_TIME_MISMATCH",
        ),
    ],
)
def test_public_leaderboard_recovery_rejects_wrong_page_or_submission_identity(
    monkeypatch, tmp_path, stdout_overrides, expected_reason
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)

    fetched, _recovery_intent, calls = _fetch_public_score_finalize_intent(
        monkeypatch, server, **stdout_overrides
    )

    assert len(calls) == 1
    assert fetched["ok"] is False
    assert fetched["reason"] == expected_reason
    assert fetched["scoreFinalizeIntent"] is None


def test_public_leaderboard_recovery_is_blocked_by_later_accepted_submission(tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"].append(
        {
            "index": item["index"] + 1,
            "name": "later.zip",
            "path": str(tmp_path / "submissions" / "later.zip"),
            "status": "scored",
            "submittedAt": "2026-07-09T00:10:00.000Z",
            "acceptedAt": "2026-07-09T00:10:01.000Z",
            "logicalSubmissionId": "later-submission",
            "artifactSha256": "b" * 64,
        }
    )
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
    )


def test_public_leaderboard_recovery_is_blocked_by_equal_time_different_identity(
    tmp_path,
):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"].append(
        {
            "index": item["index"] + 1,
            "name": "ambiguous-same-time.zip",
            "path": str(tmp_path / "submissions" / "ambiguous-same-time.zip"),
            "status": "scored",
            "submittedAt": "2026-07-09T00:00:00.000Z",
            "acceptedAt": item["acceptedAt"],
            "logicalSubmissionId": "different-submission-at-same-time",
            "artifactSha256": "b" * 64,
            "score": "1.0",
        }
    )
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
    )


def test_public_leaderboard_recovery_accepts_bound_legacy_scored_history(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    _append_legacy_scored_history(server, tmp_path)

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is not None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "EXACT_PUBLIC_LEADERBOARD_RECOVERY_INTENT_READY"
    )
    intent = status["publicLeaderboardRecoveryIntent"]
    assert intent["acceptedHistoryCount"] == 3
    assert intent["laterAcceptedSubmissionCount"] == 0


def test_public_leaderboard_recovery_rejects_legacy_query_before_publish(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    item = _append_legacy_scored_history(server, tmp_path)
    row = next(csv.DictReader(server.results_path.open(encoding="utf-8")))
    row["query_time"] = "2026-07-07T23:59:00.000Z"
    _write_results_rows(server, [row])
    _write_legacy_scored_history_compatibility(server, [(item, row)])

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


def test_public_leaderboard_recovery_rejects_legacy_publish_before_queue_submit(
    tmp_path,
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    item = _append_legacy_scored_history(server, tmp_path)
    item["submittedAt"] = "2026-07-08T00:01:00.000Z"
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"][0]["submittedAt"] = item["submittedAt"]
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")
    row = next(csv.DictReader(server.results_path.open(encoding="utf-8")))
    row["submitted_at"] = item["submittedAt"]
    _write_results_rows(server, [row])
    _write_legacy_scored_history_compatibility(server, [(item, row)])

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


def test_public_leaderboard_recovery_rejects_missing_legacy_source_log(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    _append_legacy_scored_history(server, tmp_path)
    server.snapshot_log_path.unlink()

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


@pytest.mark.parametrize("mutation", ["url", "team", "query_time", "score"])
def test_public_leaderboard_recovery_rejects_legacy_snapshot_content_mismatch(
    tmp_path, mutation
):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    item = _append_legacy_scored_history(server, tmp_path)
    row = next(csv.DictReader(server.results_path.open(encoding="utf-8")))
    snapshot = _legacy_public_snapshot(row)
    if mutation == "url":
        snapshot["url"] = "https://reg.aicomp.cn/team/work"
    elif mutation == "team":
        snapshot["text"] = snapshot["text"].replace(TEAM_ID, "AIC-2026-00000000")
    elif mutation == "query_time":
        snapshot["time"] = "2026-07-08T00:06:00.000Z"
    elif mutation == "score":
        snapshot["text"] = snapshot["text"].replace("75.0000", "75.0001")
    _write_legacy_scored_history_compatibility(
        server, [(item, row)], snapshots=[snapshot]
    )

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


def test_legacy_scored_history_proof_sorts_parsed_utc_not_iso_text(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    item = _append_legacy_scored_history(server, tmp_path)
    row = next(csv.DictReader(server.results_path.open(encoding="utf-8")))
    later_row = dict(row)
    later_row["query_time"] = "2026-07-08T00:05:00.500000Z"
    _write_results_rows(server, [row, later_row])
    _write_legacy_scored_history_compatibility(
        server, [(item, row), (item, later_row)]
    )

    proof = server.legacy_scored_history_proof(
        item,
        [row, later_row],
        dt.datetime(2026, 7, 9, tzinfo=dt.UTC),
        server.read_legacy_scored_history_compatibility(),
    )

    assert proof is not None
    assert proof["observedBeforeAt"] == "2026-07-08T00:05:00.500000Z"


def test_public_leaderboard_recovery_rejects_unproved_legacy_scored_history(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    _append_legacy_scored_history(
        server, tmp_path, include_score_evidence=False
    )

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


def test_public_leaderboard_recovery_rejects_blank_later_queue_history(tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
    queue_doc["queue"].append(
        {
            "index": item["index"] + 1,
            "name": "later-blank.zip",
            "path": str(tmp_path / "submissions" / "later-blank.zip"),
            "status": "scored",
            "submittedAt": "2026-07-09T00:10:00.000Z",
            "acceptedAt": "",
            "logicalSubmissionId": "later-blank",
            "artifactSha256": "b" * 64,
            "score": "1.0",
        }
    )
    server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT_LATER_SUBMISSION"
    )


def test_public_leaderboard_recovery_rejects_legacy_score_identity_mismatch(tmp_path):
    server = _server(tmp_path)
    _write_score_missed_case(server, tmp_path)
    _append_legacy_scored_history(server, tmp_path)
    rows = list(csv.DictReader(server.results_path.open(encoding="utf-8")))
    rows[0]["file_name"] = "different.zip"
    with server.results_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT"
    )


@pytest.mark.parametrize("source", ["queue", "results"])
def test_public_leaderboard_recovery_fails_closed_on_corrupt_later_history(
    tmp_path, source
):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    if source == "queue":
        queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
        queue_doc["queue"].append(
            {
                "index": item["index"] + 1,
                "name": "later-corrupt.zip",
                "path": str(tmp_path / "submissions" / "later-corrupt.zip"),
                "status": "scored",
                "submittedAt": "2026-07-09T00:10:00.000Z",
                "acceptedAt": "corrupt",
                "logicalSubmissionId": "later-corrupt",
                "artifactSha256": "b" * 64,
                "score": "1.0",
            }
        )
        server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")
    else:
        row = {field: "" for field in RESULT_FIELDS}
        row.update(
            {
                "query_time": "2026-07-09T00:11:00Z",
                "file_index": str(item["index"] + 1),
                "file_name": "later-corrupt.zip",
                "file_path": str(tmp_path / "submissions" / "later-corrupt.zip"),
                "submitted_at": "2026-07-09T00:10:00.000Z",
                "accepted_at": "",
                "score": "1.0",
                "score_time": "2026-07-09 08:11:00",
                "snapshot_path": str(tmp_path / "later-corrupt.json"),
            }
        )
        server.results_path.parent.mkdir(parents=True, exist_ok=True)
        with server.results_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerow(row)

    status = server.queue_status()

    assert status["publicLeaderboardRecoveryIntent"] is None
    assert (
        status["publicLeaderboardRecoveryReason"]
        == "PUBLIC_LEADERBOARD_ACCEPTED_HISTORY_CORRUPT_LATER_SUBMISSION"
    )


@pytest.mark.parametrize(
    "mutation",
    ["evidence", "recovery_claim", "candidate", "queue", "results"],
)
def test_public_leaderboard_finalize_rejects_any_bound_state_change_before_receipt(
    monkeypatch, tmp_path, mutation
):
    server = _server(tmp_path)
    item, _original_claim_path = _write_score_missed_case(server, tmp_path)
    fetched, _recovery_intent, _calls = _fetch_public_score_finalize_intent(
        monkeypatch, server
    )
    intent = fetched["scoreFinalizeIntent"]
    evidence_path = Path(fetched["rawEvidencePath"])
    recovery_claim_path = Path(fetched["recoveryClaimPath"])

    if mutation == "evidence":
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["tampered"] = True
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    elif mutation == "recovery_claim":
        claim = json.loads(recovery_claim_path.read_text(encoding="utf-8"))
        claim["tampered"] = True
        recovery_claim_path.write_text(json.dumps(claim), encoding="utf-8")
    elif mutation == "candidate":
        _zip(Path(item["path"]), body="a.jpg,0001\n")
    elif mutation == "queue":
        queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
        queue_doc["queue"][0]["acceptedAt"] = "2026-07-09T00:00:00.000Z"
        server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")
    elif mutation == "results":
        unrelated = {field: "" for field in RESULT_FIELDS}
        unrelated.update(
            {
                "query_time": "2026-07-08T00:00:00Z",
                "file_index": "12",
                "file_name": "older.zip",
                "file_path": str(tmp_path / "submissions" / "older.zip"),
                "submitted_at": "2026-07-08T00:00:00Z",
                "accepted_at": "2026-07-08T00:00:01Z",
                "score": "1.0",
                "score_time": "2026-07-08 08:01:00",
                "snapshot_path": str(tmp_path / "older.json"),
            }
        )
        server.results_path.parent.mkdir(parents=True, exist_ok=True)
        with server.results_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerow(unrelated)

    queue_before = server.queue_path.read_bytes()
    active_before = server.active_path.read_bytes()
    with pytest.raises(ValueError):
        server.queue_finalize_public_leaderboard_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == queue_before
    assert server.active_path.read_bytes() == active_before
    assert not server.score_finalizations_dir.exists()


@pytest.mark.parametrize(
    ("blocked_transition", "expected_phase", "expected_queue_status", "expected_active"),
    [
        ("result_applied", "prepared", "score_missed", True),
        ("queue_applied", "result_applied", "scored", True),
        ("active_cleared", "active_clear_prepared", "scored", False),
        ("event_applied", "active_cleared", "scored", False),
    ],
)
def test_public_leaderboard_finalize_wal_recovers_each_external_write_boundary(
    monkeypatch,
    tmp_path,
    blocked_transition,
    expected_phase,
    expected_queue_status,
    expected_active,
):
    server = _server(tmp_path)
    _item, original_claim_path = _write_score_missed_case(server, tmp_path)
    original_claim_before = original_claim_path.read_bytes()
    fetched, _recovery_intent, _calls = _fetch_public_score_finalize_intent(
        monkeypatch, server
    )
    intent = fetched["scoreFinalizeIntent"]
    recovery_claim_path = Path(fetched["recoveryClaimPath"])
    recovery_claim_before = recovery_claim_path.read_bytes()
    original_advance = server.advance_score_finalization_receipt
    tripped = False

    def fail_once(receipt_path, receipt, phase):
        nonlocal tripped
        if phase == blocked_transition and not tripped:
            tripped = True
            raise RuntimeError(f"injected before {phase}")
        return original_advance(receipt_path, receipt, phase)

    monkeypatch.setattr(server, "advance_score_finalization_receipt", fail_once)
    with pytest.raises(RuntimeError, match="injected"):
        server.queue_finalize_public_leaderboard_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    receipt_path = next(server.score_finalizations_dir.glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    assert receipt["phase"] == expected_phase
    assert queue_item["status"] == expected_queue_status
    assert server.active_path.exists() is expected_active
    assert original_claim_path.read_bytes() == original_claim_before
    assert recovery_claim_path.read_bytes() == recovery_claim_before

    monkeypatch.setattr(server, "advance_score_finalization_receipt", original_advance)
    resumed = server.queue_finalize_public_leaderboard_score(
        {"confirm_score_attribution": True, "intent": intent}
    )

    assert resumed["status"] == "scored"
    assert resumed["captureClaimPreserved"] is True
    assert resumed["recoveryClaimPreserved"] is True
    assert not server.active_path.exists()
    assert original_claim_path.read_bytes() == original_claim_before
    assert recovery_claim_path.read_bytes() == recovery_claim_before
    committed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert committed["phase"] == "committed"


@pytest.mark.parametrize("source", ["queue", "results"])
def test_public_leaderboard_finalize_wal_rejects_later_history_after_prepared(
    monkeypatch, tmp_path, source
):
    server = _server(tmp_path)
    item, _original_claim_path = _write_score_missed_case(server, tmp_path)
    fetched, _recovery_intent, _calls = _fetch_public_score_finalize_intent(
        monkeypatch, server
    )
    intent = fetched["scoreFinalizeIntent"]
    original_advance = server.advance_score_finalization_receipt
    tripped = False

    def fail_before_result_applied(receipt_path, receipt, phase):
        nonlocal tripped
        if phase == "result_applied" and not tripped:
            tripped = True
            raise RuntimeError("injected after results write")
        return original_advance(receipt_path, receipt, phase)

    monkeypatch.setattr(
        server, "advance_score_finalization_receipt", fail_before_result_applied
    )
    with pytest.raises(RuntimeError, match="injected after results write"):
        server.queue_finalize_public_leaderboard_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    receipt_path = next(server.score_finalizations_dir.glob("*.json"))
    receipt_before = receipt_path.read_bytes()
    assert json.loads(receipt_before)["phase"] == "prepared"
    if source == "queue":
        queue_doc = json.loads(server.queue_path.read_text(encoding="utf-8"))
        queue_doc["queue"].append(
            {
                "index": item["index"] + 1,
                "name": "later-after-prepared.zip",
                "path": str(
                    tmp_path / "submissions" / "later-after-prepared.zip"
                ),
                "status": "scored",
                "submittedAt": "2026-07-09T00:10:00.000Z",
                "acceptedAt": "2026-07-09T00:10:01.000Z",
                "logicalSubmissionId": "later-after-prepared",
                "artifactSha256": "c" * 64,
                "score": "2.0",
            }
        )
        server.queue_path.write_text(json.dumps(queue_doc), encoding="utf-8")
    else:
        rows = list(
            csv.DictReader(server.results_path.open("r", encoding="utf-8"))
        )
        later = {field: "" for field in RESULT_FIELDS}
        later.update(
            {
                "query_time": "2026-07-09T00:11:00Z",
                "file_index": str(item["index"] + 1),
                "file_name": "later-after-prepared.zip",
                "file_path": str(
                    tmp_path / "submissions" / "later-after-prepared.zip"
                ),
                "submitted_at": "2026-07-09T00:10:00.000Z",
                "accepted_at": "2026-07-09T00:10:01.000Z",
                "score": "2.0",
                "score_time": "2026-07-09 08:11:00",
                "snapshot_path": str(tmp_path / "later-after-prepared.json"),
            }
        )
        with server.results_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows([*rows, later])

    monkeypatch.setattr(server, "advance_score_finalization_receipt", original_advance)
    with pytest.raises(
        ValueError,
        match=(
            "SCORE_FINALIZATION_(QUEUE|RESULTS)_REVISION_CONFLICT|"
            "PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS"
        ),
    ):
        server.queue_finalize_public_leaderboard_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    assert receipt_path.read_bytes() == receipt_before
    assert json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0][
        "status"
    ] == "score_missed"
    assert server.active_path.exists()


def test_score_finalize_intent_rejects_time_and_filename_without_stable_record_id(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "attachment_filename": item["name"],
                        "score": "78.4567",
                        "score_time": "2026-07-09 07:03:00",
                        "submit_time": "2026-07-09 06:59:26",
                        "team_id": "AIC-2026-58579595",
                    }
                ]
            ),
        ),
    )

    fetched = server.aicomp_submission_records_fetch({"queue_index": 164})

    assert fetched["scoreAttributionReady"] is False
    assert fetched["scoreAttributionReason"] == "PER_SUBMISSION_FIELDS_INSUFFICIENT"
    assert fetched["scoreFinalizeReady"] is False
    assert fetched["scoreFinalizeReason"] == "PER_SUBMISSION_IDENTITY_NOT_STRONG_ENOUGH"


def test_score_finalize_rejects_evidence_changed_after_intent(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=_submission_records_stdout(
                [
                    {
                        "source": "aicomp_submission_records_table",
                        "record_id": "work-164",
                        "attachment_filename": item["name"],
                        "score": "78.4567",
                        "score_time": "2026-07-09 07:03:00",
                        "submit_time": "2026-07-09 06:59:26",
                        "team_id": "AIC-2026-58579595",
                    }
                ]
            ),
        ),
    )
    fetched = server.aicomp_submission_records_fetch({"queue_index": 164})
    evidence_path = Path(fetched["rawEvidencePath"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["tampered"] = True
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    queue_before = server.queue_path.read_bytes()
    active_before = server.active_path.read_bytes()

    with pytest.raises(ValueError, match="STALE_SCORE_FINALIZE_CONFIRMATION"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": fetched["scoreFinalizeIntent"]}
        )

    assert server.queue_path.read_bytes() == queue_before
    assert server.active_path.read_bytes() == active_before
    assert not server.score_finalizations_dir.exists()


def test_score_finalize_preflights_results_conflict_before_any_transaction_write(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    intent, _evidence_path = _fetch_strong_score_finalize_intent(monkeypatch, server, item)
    conflicting = {field: "" for field in RESULT_FIELDS}
    conflicting.update(
        {
            "query_time": "2026-07-09T00:00:00Z",
            "file_index": str(item["index"]),
            "file_name": item["name"],
            "file_path": item["path"],
            "score": "12.3456",
            "score_time": "2026-07-09 01:00:00",
            "snapshot_path": str(tmp_path / "wrong-evidence.json"),
        }
    )
    server.results_path.parent.mkdir(parents=True, exist_ok=True)
    with server.results_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RESULT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(conflicting)
    queue_before = server.queue_path.read_bytes()
    active_before = server.active_path.read_bytes()
    results_before = server.results_path.read_bytes()

    with pytest.raises(ValueError, match="STALE_SCORE_FINALIZE_CONFIRMATION|RESULT_IDENTITY_CONFLICT"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == queue_before
    assert server.active_path.read_bytes() == active_before
    assert server.results_path.read_bytes() == results_before
    assert not server.score_finalizations_dir.exists()


def test_score_finalize_rehashes_candidate_bytes_before_prepared_receipt(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    intent, _evidence_path = _fetch_strong_score_finalize_intent(monkeypatch, server, item)
    _zip(Path(item["path"]), body="a.jpg,0001\n")
    queue_before = server.queue_path.read_bytes()
    active_before = server.active_path.read_bytes()

    with pytest.raises(ValueError, match="CANDIDATE_ARTIFACT_IDENTITY_CHANGED|SCORE_FINALIZE_CANDIDATE_BYTES_CHANGED"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == queue_before
    assert server.active_path.read_bytes() == active_before
    assert not server.score_finalizations_dir.exists()


@pytest.mark.parametrize(
    ("blocked_transition", "expected_phase", "expected_queue_status", "expected_active"),
    [
        ("result_applied", "prepared", "score_missed", True),
        ("queue_applied", "result_applied", "scored", True),
        ("active_cleared", "active_clear_prepared", "scored", False),
        ("event_applied", "active_cleared", "scored", False),
    ],
)
def test_score_finalize_wal_recovers_each_external_write_boundary(
    monkeypatch,
    tmp_path,
    blocked_transition,
    expected_phase,
    expected_queue_status,
    expected_active,
):
    server = _server(tmp_path)
    item, claim_path = _write_score_missed_case(server, tmp_path)
    claim_before = claim_path.read_bytes()
    intent, _evidence_path = _fetch_strong_score_finalize_intent(monkeypatch, server, item)
    original_advance = server.advance_score_finalization_receipt
    tripped = False

    def fail_once(receipt_path, receipt, phase):
        nonlocal tripped
        if phase == blocked_transition and not tripped:
            tripped = True
            raise RuntimeError(f"injected before {phase}")
        return original_advance(receipt_path, receipt, phase)

    monkeypatch.setattr(server, "advance_score_finalization_receipt", fail_once)
    with pytest.raises(RuntimeError, match="injected"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    receipt_path = next(server.score_finalizations_dir.glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["phase"] == expected_phase
    assert receipt_path.with_suffix(".sha256").read_text(encoding="ascii") == _sha256(receipt_path) + "\n"
    queue_item = json.loads(server.queue_path.read_text(encoding="utf-8"))["queue"][0]
    assert queue_item["status"] == expected_queue_status
    assert server.active_path.exists() is expected_active
    assert claim_path.read_bytes() == claim_before

    monkeypatch.setattr(server, "advance_score_finalization_receipt", original_advance)
    resumed = server.queue_finalize_submission_record_score(
        {"confirm_score_attribution": True, "intent": intent}
    )

    assert resumed["status"] == "scored"
    assert resumed["captureClaimPreserved"] is True
    assert not server.active_path.exists()
    assert claim_path.read_bytes() == claim_before
    committed = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert committed["phase"] == "committed"
    assert receipt_path.with_suffix(".sha256").read_text(encoding="ascii") == _sha256(receipt_path) + "\n"


def test_score_finalize_replay_rejects_tampered_receipt_sidecar(monkeypatch, tmp_path):
    server = _server(tmp_path)
    item, _claim_path = _write_score_missed_case(server, tmp_path)
    intent, _evidence_path = _fetch_strong_score_finalize_intent(monkeypatch, server, item)
    finalized = server.queue_finalize_submission_record_score(
        {"confirm_score_attribution": True, "intent": intent}
    )
    receipt_path = Path(finalized["finalizationReceipt"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["updatedAt"] = "2099-01-01T00:00:00Z"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    queue_before = server.queue_path.read_bytes()
    results_before = server.results_path.read_bytes()

    with pytest.raises(ValueError, match="SCORE_FINALIZATION_RECEIPT_INTEGRITY_INVALID"):
        server.queue_finalize_submission_record_score(
            {"confirm_score_attribution": True, "intent": intent}
        )

    assert server.queue_path.read_bytes() == queue_before
    assert server.results_path.read_bytes() == results_before


def test_leaderboard_snapshot_marks_stale_without_active_and_writes_json(monkeypatch, tmp_path):
    server = _server(tmp_path)

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=_leaderboard_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is False
    assert result["freshness"]["reason"] == "NO_ACTIVE_SUBMISSION"
    snapshot_path = Path(result["snapshotPath"])
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["freshness"]["fresh"] is False


def test_leaderboard_snapshot_marks_fresh_when_team_submit_matches_active(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True)
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "file": "candidate.zip",
                "path": str(tmp_path / "submissions" / "candidate.zip"),
                "queue_index": 1,
                "accepted_at": "2026-07-08T09:02:25.665Z",
                "submitted_at": "2026-07-08T09:02:23.104Z",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=_leaderboard_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is True
    assert result["freshness"]["reason"] == "TEAM_SUBMIT_TIME_MATCHES_ACTIVE"


def test_leaderboard_snapshot_requires_awaiting_score_status(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True)
    server.active_path.write_text(
        json.dumps(
            {
                "status": "accepted",
                "file": "candidate.zip",
                "path": str(tmp_path / "submissions" / "candidate.zip"),
                "queue_index": 1,
                "accepted_at": "2026-07-08T09:02:25.665Z",
                "submitted_at": "2026-07-08T09:02:23.104Z",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=_leaderboard_stdout())

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is False
    assert result["freshness"]["reason"] == "ACTIVE_STATUS_NOT_AWAITING_SCORE:accepted"


def test_leaderboard_snapshot_rejects_previous_submission_before_active(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True)
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "file": "candidate.zip",
                "path": str(tmp_path / "submissions" / "candidate.zip"),
                "queue_index": 164,
                "accepted_at": "2026-07-08T22:59:26.657Z",
                "submitted_at": "2026-07-08T22:59:25.505Z",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=_leaderboard_stdout("2026-07-08 21:52:10", "92.0014"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is False
    assert result["freshness"]["reason"] == "PUBLIC_LEADERBOARD_PREVIOUS_SUBMISSION_BEFORE_ACTIVE_ACCEPTED_AT"


def test_leaderboard_snapshot_rejects_team_submit_after_active_window(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True)
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "file": "candidate.zip",
                "path": str(tmp_path / "submissions" / "candidate.zip"),
                "queue_index": 1,
                "accepted_at": "2026-07-08T09:02:25.665Z",
                "submitted_at": "2026-07-08T09:02:23.104Z",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=_leaderboard_stdout("2026-07-08 17:10:30"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is False
    assert result["freshness"]["reason"] == "PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW"


def test_leaderboard_snapshot_rejects_score_time_before_team_submit(monkeypatch, tmp_path):
    server = _server(tmp_path)
    server.active_path.parent.mkdir(parents=True)
    server.active_path.write_text(
        json.dumps(
            {
                "status": "awaiting_score",
                "file": "candidate.zip",
                "path": str(tmp_path / "submissions" / "candidate.zip"),
                "queue_index": 1,
                "accepted_at": "2026-07-08T09:02:25.665Z",
                "submitted_at": "2026-07-08T09:02:23.104Z",
            }
        ),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=_leaderboard_stdout(score_time="2026-07-08 17:01:00"),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = server.leaderboard_snapshot()

    assert result["ok"]
    assert result["fresh"] is False
    assert result["freshness"]["reason"] == "PUBLIC_LEADERBOARD_SCORE_TIME_BEFORE_TEAM_SUBMIT_TIME"
