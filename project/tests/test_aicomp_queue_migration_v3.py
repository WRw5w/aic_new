import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "aicomp_migrate_queue_v3.py"
SPEC = importlib.util.spec_from_file_location("aicomp_migrate_queue_v3", SCRIPT)
MIGRATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MIGRATION)


def _fixture(root: Path) -> Path:
    submissions = root / "submissions"
    submissions.mkdir(parents=True)
    queue_path = submissions / "aicomp_submit_queue.json"
    same_path = str(submissions / "same.zip")
    queue_path.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "queue": [
                    {
                        "index": 144,
                        "name": "same.zip",
                        "path": same_path,
                        "status": "skipped",
                        "submittedAt": "",
                        "acceptedAt": "",
                        "score": "78.2713",
                        "scoreTime": "2026-07-09 10:46:26",
                        "teamSubmitTime": "2026-07-09 06:59:26",
                        "leaderboardPublishTime": "07月09日 10时00分",
                        "provenanceCandidateSha256": "a" * 64,
                    },
                    {
                        "index": 164,
                        "name": "same.zip",
                        "path": same_path,
                        "status": "scored",
                        "submittedAt": "2026-07-08T22:59:25.505Z",
                        "acceptedAt": "2026-07-08T22:59:26.657Z",
                        "score": "78.2713",
                        "scoreTime": "2026-07-09 10:46:26",
                        "teamSubmitTime": "2026-07-09 06:59:26",
                        "leaderboardPublishTime": "07月09日 10时00分",
                        "provenanceCandidateSha256": "a" * 64,
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    with (submissions / "aicomp_results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "query_time",
                "file_index",
                "file_name",
                "file_path",
                "submitted_at",
                "leaderboard_publish_time",
                "team_rank",
                "team_id",
                "team_name",
                "team_submit_time",
                "score",
                "score_time",
                "snapshot_path",
            ]
        )
        writer.writerow(
            [
                "2026-07-09T02:46:26Z",
                "164",
                "same.zip",
                same_path,
                "2026-07-08T22:59:25.505Z",
                "07月09日 10时00分",
                "3",
                "AIC-2026-58579595",
                "swpu_1",
                "2026-07-09 06:59:26",
                "78.2713",
                "2026-07-09 10:46:26",
                "snap.json",
            ]
        )
    (submissions / "aicomp_events.jsonl").write_text(
        json.dumps(
            {
                "event": "submit.accepted",
                "queue_index": 164,
                "accepted_at": "2026-07-08T22:59:26.657Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return queue_path


def test_migration_clears_cross_index_score_contamination_and_is_idempotent(tmp_path):
    queue_path = _fixture(tmp_path)
    document = json.loads(queue_path.read_text(encoding="utf-8"))
    results = MIGRATION.read_results(tmp_path / "submissions" / "aicomp_results.csv")
    events = MIGRATION.read_accept_events(tmp_path / "submissions" / "aicomp_events.jsonl")

    migrated, changes = MIGRATION.migrate_queue_document(
        document,
        results,
        events,
        migrated_at="2026-07-10T00:00:00Z",
    )
    second, second_changes = MIGRATION.migrate_queue_document(
        migrated,
        results,
        events,
        migrated_at="2026-07-10T00:00:00Z",
    )

    old, real = migrated["queue"]
    assert old["status"] == "skipped"
    assert old["score"] == ""
    assert old["scoreTime"] == ""
    assert old["acceptedAt"] == ""
    assert real["score"] == "78.2713"
    assert real["acceptedAt"] == "2026-07-08T22:59:26.657Z"
    assert old["submissionId"] != real["submissionId"]
    assert migrated["schemaVersion"] == 3
    assert any(change["index"] == 144 for change in changes)
    assert second == migrated
    assert second_changes == []


def test_cli_apply_uses_compare_and_set_and_creates_backup(tmp_path):
    queue_path = _fixture(tmp_path)
    before = queue_path.read_bytes()
    before_sha256 = hashlib.sha256(before).hexdigest()

    dry = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(dry.stdout)["changed"] is True
    assert queue_path.read_bytes() == before

    applied = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--apply",
            "--expected-queue-sha256",
            before_sha256,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(applied.stdout)
    assert payload["applied"] is True
    assert Path(payload["backup"], "aicomp_submit_queue.json").read_bytes() == before

    second = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert json.loads(second.stdout)["changed"] is False


def test_cli_apply_refuses_when_shared_control_lock_is_held(tmp_path):
    queue_path = _fixture(tmp_path)
    before_sha256 = hashlib.sha256(queue_path.read_bytes()).hexdigest()
    control = tmp_path / "submissions" / "aicomp_control.lock"
    control.mkdir()
    (control / "owner.json").write_text(json.dumps({"token": "live", "pid": 1}), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(tmp_path),
            "--apply",
            "--expected-queue-sha256",
            before_sha256,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode != 0
    assert "SUBMISSION_CONTROL_BUSY" in result.stderr
    assert hashlib.sha256(queue_path.read_bytes()).hexdigest() == before_sha256


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific non-destructive PID probe")
def test_windows_pid_probe_never_calls_os_kill(monkeypatch):
    def forbidden_kill(*_args, **_kwargs):
        raise AssertionError("os.kill must not be used for PID liveness on Windows")

    monkeypatch.setattr(MIGRATION.os, "kill", forbidden_kill)

    assert MIGRATION.pid_is_running(os.getpid()) is True
    assert MIGRATION.pid_is_running(0) is False
