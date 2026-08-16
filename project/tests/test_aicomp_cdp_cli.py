import os
import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "aicomp_cdp.mjs"


class AicompCdpCliTests(unittest.TestCase):
    def run_cli(self, *args, env_updates=None):
        env = os.environ.copy()
        env["AICOMP_DEBUG_URL"] = "http://127.0.0.1:9"
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            ["node", str(SCRIPT_PATH), *args],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            timeout=5,
        )

    def payload_from_stdout(self, stdout):
        return json.loads(stdout[stdout.find("{") : stdout.rfind("}") + 1])

    def run_fixture(self, fixture, env_updates=None):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = Path(temp_dir) / "submission_records_fixture.json"
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            return self.run_cli("submission-records-fixture", str(fixture_path), env_updates=env_updates)

    def test_submit_one_rejects_direct_cli_without_fenced_runner(self):
        result = self.run_cli("submit-one", str(REPO_ROOT / "missing-submit-file.zip"))

        self.assertEqual(result.returncode, 77, result.stdout)
        self.assertIn("SUBMIT_CAPABILITY_ENV_INCOMPLETE", result.stdout)

    def test_debug_and_click_submit_commands_are_disabled(self):
        for command in ("submit-one-debug", "click-current-submit", "click-exact", "remove-file"):
            result = self.run_cli(command)
            self.assertEqual(result.returncode, 77, result.stdout)
            self.assertIn("DIRECT_SUBMIT_COMMAND_DISABLED", result.stdout)

    def test_submit_one_accepts_only_matching_parent_lease_and_queue_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            submissions = root / "submissions"
            lease_dir = submissions / "aicomp_runner.lock"
            lease_dir.mkdir(parents=True)
            candidate = submissions / "candidate.zip"
            candidate.write_bytes(b"PK\x03\x04fenced-test")
            candidate_sha256 = hashlib.sha256(candidate.read_bytes()).hexdigest()
            logical_id = "logical-candidate-r0"
            attempt_id = "attempt-1"
            intent = {
                "action": "submit_candidate",
                "queueIndex": 1,
                "candidateSha256": candidate_sha256,
                "logicalSubmissionId": logical_id,
                "queueRevision": "sha256:test",
                "attemptId": attempt_id,
                "runnerId": "fixture-runner",
            }
            (submissions / "aicomp_submit_queue.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": 3,
                        "queue": [
                            {
                                "index": 1,
                                "name": candidate.name,
                                "path": str(candidate),
                                "status": "uploading",
                                "attemptId": attempt_id,
                                "runnerId": "fixture-runner",
                                "provenanceCandidateSha256": candidate_sha256,
                                "logicalSubmissionId": logical_id,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (lease_dir / "owner.json").write_text(
                json.dumps(
                    {
                        "token": "lease-token",
                        "pid": os.getpid(),
                        "phase": "running",
                        "runner_id": "fixture-runner",
                        "intent": intent,
                    }
                ),
                encoding="utf-8",
            )

            result = self.run_cli(
                "submit-one",
                str(candidate),
                env_updates={
                    "AICOMP_ROOT": str(root),
                    "AICOMP_RUNNER_LEASE_TOKEN": "lease-token",
                    "AICOMP_RUNNER_ID": "fixture-runner",
                    "AICOMP_EXPECTED_QUEUE_INDEX": "1",
                    "AICOMP_EXPECTED_CANDIDATE_SHA256": candidate_sha256,
                    "AICOMP_EXPECTED_LOGICAL_SUBMISSION_ID": logical_id,
                    "AICOMP_SUBMIT_ATTEMPT_ID": attempt_id,
                    "AICOMP_CDP_COMMAND_TIMEOUT_MS": "100",
                },
            )

            self.assertNotEqual(result.returncode, 77, result.stdout)
            self.assertNotIn("SUBMIT_GATE_REJECTED", result.stdout)

    def test_submit_attempt_id_is_preallocated_by_mcp_not_generated_in_node_runner(self):
        source = (REPO_ROOT / "tools" / "aicomp_submit_queue.mjs").read_text(encoding="utf-8")
        start = source.index("async function submitNext")
        submit_block = source[start : source.index("function recoverStaleUploading(queue)", start)]

        self.assertIn("AICOMP_SUBMIT_ATTEMPT_ID", submit_block)
        self.assertNotIn("item.attemptId = randomUUID()", submit_block)

    def test_help_does_not_connect_to_cdp(self):
        result = self.run_cli("submit-one", "--help")

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("submit-one", result.stdout)
        self.assertIn("submission-records", result.stdout)

    def test_acceptance_uses_new_transient_feedback_and_rechecks_fence_before_click(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("/提交成功|作品提交成功/.test(feedbackText)", source)
        self.assertNotIn("feedbackText + \" \" + bodyText", source)
        self.assertIn("last.feedbackText !== baselineFeedbackText", source)
        click_loop = source[
            source.index("for (let i = 0; i < 3; i++)") : source.index(
                'console.log("SUBMIT_NOT_DISPATCHED=NO_SUBMIT_BUTTON_CLICKED")'
            )
        ]
        self.assertLess(click_loop.index("assertFencedSubmitCapability(filePath)"), click_loop.index("clickSubmitButtonExpression"))

    def test_submission_records_reports_source_unavailable_without_cdp(self):
        result = self.run_cli("submission-records")

        self.assertEqual(result.returncode, 3, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "PER_SUBMISSION_SOURCE_UNAVAILABLE")
        self.assertEqual(payload["records"], [])

    def test_submission_records_requires_actual_records_page_before_extracting_rows(self):
        result = self.run_fixture(
            {
                "url": "https://reg.aicomp.cn/app/JSGLPT/639980063d903c241eb85102",
                "title": "AICOMP",
                "headings": ["参赛作品上传"],
                "bodyText": "竞赛报名 我的参赛信息 参赛作品上传 作品打分结果 参赛作品上传 共 1 条数据",
                "tables": [
                    {
                        "tableIndex": 0,
                        "headers": ["参赛编号", "团队名称", "作品提交", "操作"],
                        "rows": [
                            {
                                "rowIndex": 0,
                                "cells": ["AIC-2026-58579595", "swpu_1", "未提交", "提交作品"],
                                "row": {"参赛编号": "AIC-2026-58579595", "团队名称": "swpu_1", "作品提交": "未提交"},
                                "text": "AIC-2026-58579595 swpu_1 未提交 提交作品",
                                "links": [],
                                "actionTexts": ["提交作品"],
                            }
                        ],
                        "text": "参赛作品上传 共 1 条数据 AIC-2026-58579595 swpu_1 未提交 提交作品",
                    }
                ],
            }
        )

        self.assertEqual(result.returncode, 3, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertEqual(payload["reason"], "PER_SUBMISSION_SOURCE_UNAVAILABLE")
        self.assertFalse(payload["sourceAvailable"])
        self.assertEqual(payload["records"], [])

    def test_submission_records_reports_fields_insufficient_when_records_page_lacks_target_and_score_fields(self):
        result = self.run_fixture(
            {
                "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                "title": "AICOMP",
                "headings": ["作品打分结果"],
                "bodyText": "作品打分结果 共 178 条数据",
                "tables": [
                    {
                        "tableIndex": 0,
                        "headers": ["竞赛名称", "参赛编号", "团队名称", "状态", "操作"],
                        "rows": [
                            {
                                "rowIndex": 0,
                                "cells": ["AIC-算法挑战赛（赛马制）", "AIC-2026-58579595", "swpu_1", "已打分", "详情"],
                                "row": {
                                    "竞赛名称": "AIC-算法挑战赛（赛马制）",
                                    "参赛编号": "AIC-2026-58579595",
                                    "团队名称": "swpu_1",
                                    "状态": "已打分",
                                },
                                "text": "AIC-算法挑战赛（赛马制） AIC-2026-58579595 swpu_1 已打分 详情",
                                "links": [],
                                "actionTexts": ["详情"],
                            }
                        ],
                        "text": "作品打分结果 共 178 条数据 AIC-2026-58579595 swpu_1 已打分 详情",
                    }
                ],
            },
            env_updates={
                "AICOMP_RECORDS_TARGET_QUEUE_INDEX": "164",
                "AICOMP_RECORDS_TARGET_FILE": "pred_results_repro_champion512_tta_balanced.zip",
                "AICOMP_RECORDS_TARGET_ACCEPTED_AT": "2026-07-08T22:59:26.657Z",
            },
        )

        self.assertEqual(result.returncode, 4, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "PER_SUBMISSION_FIELDS_INSUFFICIENT")
        self.assertTrue(payload["sourceAvailable"])
        self.assertFalse(payload["fieldsSufficient"])
        self.assertGreater(len(payload["records"]), 0)
        self.assertIn("NO_TARGET_COMPARABLE_FIELD", payload["diagnostics"]["fieldGap"]["gaps"])
        self.assertIn("NO_SCORE_OR_SCORE_TIME_FIELD", payload["diagnostics"]["fieldGap"]["gaps"])

    def test_submission_records_fixture_accepts_records_with_target_and_score_fields(self):
        result = self.run_fixture(
            {
                "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                "title": "AICOMP",
                "headings": ["作品打分结果"],
                "bodyText": "作品打分结果 共 178 条数据",
                "tables": [
                    {
                        "tableIndex": 0,
                        "headers": ["作品文件", "提交时间", "分数", "打分时间", "状态"],
                        "rows": [
                            {
                                "rowIndex": 0,
                                "cells": [
                                    "pred_results_repro_champion512_tta_balanced.zip",
                                    "2026-07-09 06:59:26",
                                    "78.4567",
                                    "2026-07-09 07:03:00",
                                    "已打分",
                                ],
                                "row": {
                                    "作品文件": "pred_results_repro_champion512_tta_balanced.zip",
                                    "提交时间": "2026-07-09 06:59:26",
                                    "分数": "78.4567",
                                    "打分时间": "2026-07-09 07:03:00",
                                    "状态": "已打分",
                                },
                                "text": "pred_results_repro_champion512_tta_balanced.zip 2026-07-09 06:59:26 78.4567 2026-07-09 07:03:00 已打分",
                                "links": [],
                                "actionTexts": [],
                            }
                        ],
                        "text": "作品打分结果 共 178 条数据 pred_results_repro_champion512_tta_balanced.zip 78.4567",
                    }
                ],
            },
            env_updates={
                "AICOMP_RECORDS_TARGET_FILE": "pred_results_repro_champion512_tta_balanced.zip",
                "AICOMP_RECORDS_TARGET_ACCEPTED_AT": "2026-07-08T22:59:26.657Z",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["reason"], "OK")
        self.assertTrue(payload["fieldsSufficient"])
        self.assertTrue(payload["attributionReady"], payload)
        self.assertEqual(payload["diagnostics"]["attribution"]["scoredMatchedRecordCount"], 1)

    def test_submission_records_fixture_waits_through_role_bootstrap_snapshot(self):
        result = self.run_fixture(
            {
                "readinessSnapshots": [
                    {
                        "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                        "title": "",
                        "headings": [],
                        "bodyText": "当前角色为：参赛学生",
                        "tables": [],
                    },
                    {
                        "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                        "title": "AICOMP",
                        "headings": ["作品打分结果"],
                        "bodyText": "作品打分结果 共 178 条数据",
                        "tables": [
                            {
                                "tableIndex": 0,
                                "headers": ["作品文件", "提交时间", "分数", "打分时间"],
                                "rows": [
                                    {
                                        "rowIndex": 0,
                                        "row": {
                                            "作品文件": "target.zip",
                                            "提交时间": "2026-07-11 06:31:40",
                                            "分数": "79.1234",
                                            "打分时间": "2026-07-11 07:03:00",
                                        },
                                        "text": "target.zip 2026-07-11 06:31:40 79.1234 2026-07-11 07:03:00",
                                        "links": [],
                                        "actionTexts": [],
                                    }
                                ],
                                "text": "作品打分结果 target.zip 79.1234",
                            }
                        ],
                    },
                ]
            },
            env_updates={
                "AICOMP_RECORDS_TARGET_FILE": "target.zip",
                "AICOMP_RECORDS_TARGET_ACCEPTED_AT": "2026-07-10T22:31:40Z",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"]["readiness"]["samples"][0]["kind"], "bootstrap")
        self.assertEqual(payload["diagnostics"]["readiness"]["samples"][-1]["kind"], "records")

    def test_submission_records_fixture_waits_for_data_after_records_heading(self):
        result = self.run_fixture(
            {
                "readinessSnapshots": [
                    {
                        "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                        "title": "竞赛报名平台",
                        "headings": ["作品打分结果"],
                        "bodyText": "竞赛报名 作品打分结果 当前角色为：参赛学生",
                        "tables": [],
                    },
                    {
                        "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                        "title": "竞赛报名平台",
                        "headings": ["作品打分结果"],
                        "bodyText": "作品打分结果 共 1 条数据",
                        "tables": [
                            {
                                "tableIndex": 0,
                                "headers": ["作品ID", "作品文件", "提交时间", "分数", "打分时间", "参赛编号"],
                                "rows": [
                                    {
                                        "rowIndex": 0,
                                        "row": {
                                            "作品ID": "work-167",
                                            "作品文件": "target.zip",
                                            "提交时间": "2026-07-13 15:03:54",
                                            "分数": "79.1234",
                                            "打分时间": "2026-07-13 16:02:00",
                                            "参赛编号": "AIC-2026-58579595",
                                        },
                                        "text": "work-167 target.zip 2026-07-13 15:03:54 79.1234 2026-07-13 16:02:00 AIC-2026-58579595",
                                        "links": [],
                                        "actionTexts": [],
                                    }
                                ],
                                "text": "作品打分结果 target.zip 79.1234",
                            }
                        ],
                    },
                ]
            },
            env_updates={
                "AICOMP_RECORDS_TARGET_FILE": "target.zip",
                "AICOMP_RECORDS_TARGET_ACCEPTED_AT": "2026-07-13T07:03:54Z",
            },
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        payload = self.payload_from_stdout(result.stdout)
        self.assertEqual(payload["diagnostics"]["readiness"]["samples"][0]["kind"], "records_loading")
        self.assertEqual(payload["diagnostics"]["readiness"]["samples"][-1]["kind"], "records")
        self.assertTrue(payload["attributionReady"])

    def test_submission_records_fixture_does_not_join_target_and_score_across_records(self):
        result = self.run_fixture(
            {
                "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                "title": "AICOMP",
                "headings": ["作品打分结果"],
                "bodyText": "作品打分结果 共 2 条数据",
                "tables": [
                    {
                        "tableIndex": 0,
                        "headers": ["作品文件", "分数", "打分时间"],
                        "rows": [
                            {"rowIndex": 0, "row": {"作品文件": "target.zip"}, "text": "target.zip", "links": [], "actionTexts": []},
                            {"rowIndex": 1, "row": {"作品文件": "other.zip", "分数": "99.0", "打分时间": "2026-07-11 07:03:00"}, "text": "other.zip 99.0", "links": [], "actionTexts": []},
                        ],
                        "text": "作品打分结果 target.zip other.zip 99.0",
                    }
                ],
            },
            env_updates={"AICOMP_RECORDS_TARGET_FILE": "target.zip"},
        )

        payload = self.payload_from_stdout(result.stdout)
        self.assertFalse(payload["attributionReady"])
        self.assertEqual(payload["diagnostics"]["attribution"]["matchedRecordCount"], 1)
        self.assertEqual(payload["diagnostics"]["attribution"]["scoredMatchedRecordCount"], 0)

    def test_submission_records_fixture_fails_closed_on_multiple_identity_matches(self):
        result = self.run_fixture(
            {
                "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                "title": "AICOMP",
                "headings": ["作品打分结果"],
                "bodyText": "作品打分结果 共 2 条数据",
                "tables": [
                    {
                        "tableIndex": 0,
                        "headers": ["作品文件", "分数", "打分时间", "状态"],
                        "rows": [
                            {"rowIndex": 0, "row": {"作品文件": "target.zip", "分数": "79.0", "打分时间": "2026-07-11 07:03:00"}, "text": "target.zip 79.0", "links": [], "actionTexts": []},
                            {"rowIndex": 1, "row": {"作品文件": "target.zip", "状态": "待打分"}, "text": "target.zip 待打分", "links": [], "actionTexts": []},
                        ],
                        "text": "作品打分结果 target.zip 79.0 target.zip 待打分",
                    }
                ],
            },
            env_updates={"AICOMP_RECORDS_TARGET_FILE": "target.zip"},
        )

        payload = self.payload_from_stdout(result.stdout)
        self.assertFalse(payload["attributionReady"])
        self.assertEqual(payload["diagnostics"]["attribution"]["matchedRecordCount"], 2)
        self.assertEqual(payload["diagnostics"]["attribution"]["reason"], "AMBIGUOUS_MATCHING_SUBMISSION_RECORDS")

    def test_submission_records_fixture_normalizes_platform_timezones(self):
        cases = [
            ("2026-07-11 06:31:40", "2026-07-10T22:31:40Z"),
            ("2026-07-11T06:31:40", "2026-07-10T22:31:40Z"),
            ("2026-07-11T06:31:40.123", "2026-07-10T22:31:40.123Z"),
            ("2026-07-11T06:31:40Z", "2026-07-11T06:31:40Z"),
            ("2026-07-11T06:31:40+08:00", "2026-07-10T22:31:40Z"),
        ]
        for record_time, accepted_at in cases:
            with self.subTest(record_time=record_time, accepted_at=accepted_at):
                result = self.run_fixture(
                    {
                        "url": "https://reg.aicomp.cn/app/JSGLPT/65b75207a58fdc32c79e9842",
                        "title": "AICOMP",
                        "headings": ["作品打分结果"],
                        "bodyText": "作品打分结果 共 1 条数据",
                        "tables": [
                            {
                                "tableIndex": 0,
                                "headers": ["作品文件", "提交时间", "分数", "打分时间"],
                                "rows": [
                                    {
                                        "rowIndex": 0,
                                        "row": {"作品文件": "target.zip", "提交时间": record_time, "分数": "79.0", "打分时间": "2026-07-11 07:03:00"},
                                        "text": f"target.zip {record_time} 79.0",
                                        "links": [],
                                        "actionTexts": [],
                                    }
                                ],
                                "text": "作品打分结果 target.zip 79.0",
                            }
                        ],
                    },
                    env_updates={"AICOMP_RECORDS_TARGET_FILE": "target.zip", "AICOMP_RECORDS_TARGET_ACCEPTED_AT": accepted_at},
                )
                payload = self.payload_from_stdout(result.stdout)
                self.assertTrue(payload["attributionReady"], payload)

    def test_submission_records_network_capture_is_epoch_fenced(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("activeNetworkCapture", source)
        self.assertIn("meta.capture !== activeNetworkCapture", source)
        self.assertNotIn("networkEvidence.length = 0", source)
        self.assertNotIn("bodyPromises.length = 0", source)

    def test_page_probe_never_serializes_live_form_control_values(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        start = source.index("const probeExpression = String.raw`")
        probe = source[start : source.index("async function waitForPage", start)]

        self.assertNotIn("el.innerText || el.value", probe)
        self.assertNotIn('el.type === "file" ? "" : el.value', probe)
        self.assertIn('value: ""', probe)


if __name__ == "__main__":
    unittest.main()
