import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = "D:\\02_Projects\\ML\\jinyinsai";
const SUBMISSIONS = path.join(ROOT, "submissions");
const QUEUE_PATH = path.join(SUBMISSIONS, "aicomp_submit_queue.json");
const RESULTS_PATH = path.join(SUBMISSIONS, "aicomp_results.csv");
const MANIFEST_PATH = path.join(SUBMISSIONS, "aicomp_artifact_manifest.json");
const MANIFEST_CSV_PATH = path.join(SUBMISSIONS, "aicomp_artifact_manifest.csv");

function nowIso() {
  return new Date().toISOString();
}

function readJson(filePath, fallback) {
  if (!fs.existsSync(filePath)) return fallback;
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeAtomic(filePath, text) {
  const tmp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, text, "utf8");
  fs.renameSync(tmp, filePath);
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        field += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') quoted = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch !== "\r") {
      field += ch;
    }
  }

  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  if (!rows.length) return [];

  const header = rows[0];
  return rows.slice(1).filter((r) => r.some((x) => x !== "")).map((r) => {
    const obj = {};
    header.forEach((key, i) => {
      obj[key] = r[i] ?? "";
    });
    return obj;
  });
}

function sha256File(filePath) {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

function slugName(name) {
  return path
    .basename(name, path.extname(name))
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function artifactIdFor(name, sha256) {
  return `${slugName(name)}:${sha256.slice(0, 16)}`;
}

function priorityForName(name) {
  const n = name.toLowerCase();
  let score = 0;
  if (n.includes("5sig_full_v2")) score += 1000;
  if (n.includes("5sig_full")) score += 800;
  if (n.includes("5sig")) score += 650;
  if (n.includes("soup")) score += 520;
  if (n.includes("swa")) score += 500;
  if (n.includes("c448_dr_rank32_keep90")) score += 450;
  if (n.includes("gce")) score += 400;
  if (n.includes("balanced")) score += 80;
  if (n.includes("tta")) score += 40;
  return score;
}

function tagsForName(name) {
  const n = name.toLowerCase();
  return [
    n.includes("5sig_full_v2") && "5sig_full_v2",
    n.includes("5sig_full") && "5sig_full",
    n.includes("5sig") && "5sig",
    n.includes("soup") && "soup",
    n.includes("swa") && "swa",
    n.includes("c448") && "c448",
    n.includes("gce") && "gce",
    n.includes("drecall") && "drecall",
    n.includes("lora") && "lora",
    n.includes("balanced") && "balanced",
    n.includes("tta") && "tta",
  ].filter(Boolean);
}

function topLevelZipFiles() {
  return fs
    .readdirSync(SUBMISSIONS, { withFileTypes: true })
    .filter((d) => d.isFile() && d.name.toLowerCase().endsWith(".zip"))
    .map((d) => {
      const full = path.join(SUBMISSIONS, d.name);
      const st = fs.statSync(full);
      const sha256 = sha256File(full);
      const csvPath = path.join(SUBMISSIONS, `${path.basename(d.name, ".zip")}.csv`);
      const csv = fs.existsSync(csvPath) ? fs.statSync(csvPath) : null;
      return {
        artifactId: artifactIdFor(d.name, sha256),
        name: d.name,
        path: full,
        present: true,
        size: st.size,
        sha256,
        mtime: st.mtime.toISOString(),
        priority: priorityForName(d.name),
        tags: tagsForName(d.name),
        csv: csv
          ? {
              name: path.basename(csvPath),
              path: csvPath,
              size: csv.size,
              mtime: csv.mtime.toISOString(),
            }
          : null,
      };
    });
}

function latestResultsByPath() {
  if (!fs.existsSync(RESULTS_PATH)) return new Map();
  const rows = parseCsv(fs.readFileSync(RESULTS_PATH, "utf8"));
  const latest = new Map();

  for (const row of rows) {
    const keys = [
      row.file_path && path.resolve(row.file_path).toLowerCase(),
      row.file_name && row.file_name.toLowerCase(),
    ].filter(Boolean);
    for (const key of keys) {
      const prev = latest.get(key);
      if (!prev || String(row.query_time) > String(prev.query_time)) {
        latest.set(key, row);
      }
    }
  }
  return latest;
}

function resultForArtifact(artifact, resultsByPath) {
  const row =
    resultsByPath.get(path.resolve(artifact.path).toLowerCase()) ||
    resultsByPath.get(artifact.name.toLowerCase());
  if (!row) return null;
  return {
    queryTime: row.query_time || "",
    leaderboardPublishTime: row.leaderboard_publish_time || "",
    teamRank: row.team_rank || "",
    teamId: row.team_id || "",
    teamName: row.team_name || "",
    teamSubmitTime: row.team_submit_time || "",
    score: row.score || "",
    scoreTime: row.score_time || "",
    snapshotPath: row.snapshot_path || "",
  };
}

function loadQueueState() {
  const doc = readJson(QUEUE_PATH, { queue: [] });
  const queue = Array.isArray(doc.queue) ? doc.queue : [];
  const byPath = new Map();
  const byName = new Map();
  for (const item of queue) {
    if (item.path) byPath.set(path.resolve(item.path).toLowerCase(), item);
    if (item.name) byName.set(String(item.name).toLowerCase(), item);
  }
  return {
    updatedAt: doc.updatedAt || "",
    refreshDelayMinutes: doc.refreshDelayMinutes ?? "",
    queue,
    byPath,
    byName,
  };
}

function queueItemForArtifact(artifact, queueState) {
  return (
    queueState.byPath.get(path.resolve(artifact.path).toLowerCase()) ||
    queueState.byName.get(artifact.name.toLowerCase()) ||
    null
  );
}

function stateFor(queueItem, result, present) {
  if (!present) return result?.score ? "scored_missing_file" : "missing_file";
  if (!queueItem) return result?.score ? "scored_unqueued" : "candidate";

  if (queueItem.status === "scored") return "scored";
  if (queueItem.status === "done") return result?.score ? "scored" : "score_missed";
  if (queueItem.status === "awaiting_refresh" || queueItem.status === "submitted_current") return "awaiting_score";
  if (queueItem.status === "pending") return "queued";
  if (queueItem.status === "submitting") return "uploading";
  if (queueItem.status === "accepted") return "accepted";
  if (queueItem.status === "awaiting_score") return "awaiting_score";
  if (queueItem.status === "score_missed" || queueItem.status === "capture_missed") return "score_missed";
  if (queueItem.status === "skipped") return "skipped";
  if (String(queueItem.status || "").startsWith("paused")) return "paused";
  if (queueItem.status === "failed") return "failed";
  return "queued";
}

function warningFor(artifact, queueItem) {
  const warnings = [];
  if (queueItem && Number(queueItem.size || 0) && Number(queueItem.size) !== Number(artifact.size)) {
    warnings.push(`queue_size=${queueItem.size};current_size=${artifact.size}`);
  }
  return warnings;
}

function buildManifest() {
  const now = nowIso();
  const previous = readJson(MANIFEST_PATH, { artifacts: [] });
  const previousById = new Map((previous.artifacts || []).map((a) => [a.artifactId, a]));
  const currentIds = new Set();
  const queueState = loadQueueState();
  const resultsByPath = latestResultsByPath();

  const artifacts = topLevelZipFiles().map((artifact) => {
    currentIds.add(artifact.artifactId);
    const prev = previousById.get(artifact.artifactId) || {};
    const queueItem = queueItemForArtifact(artifact, queueState);
    const result = resultForArtifact(artifact, resultsByPath);
    return {
      ...artifact,
      firstSeenAt: prev.firstSeenAt || now,
      lastSeenAt: now,
      state: stateFor(queueItem, result, true),
      queue: queueItem
        ? {
            index: queueItem.index ?? "",
            status: queueItem.status ?? "",
            submittedAt: queueItem.submittedAt ?? "",
            exitCode: queueItem.exitCode ?? "",
          }
        : null,
      result,
      warnings: warningFor(artifact, queueItem),
      notes: prev.notes || [],
    };
  });

  for (const prev of previous.artifacts || []) {
    if (currentIds.has(prev.artifactId)) continue;
    artifacts.push({
      ...prev,
      present: false,
      lastSeenAt: prev.lastSeenAt || now,
      state: stateFor(prev.queue, prev.result, false),
    });
  }

  artifacts.sort((a, b) => {
    const ai = Number(a.queue?.index || 999999);
    const bi = Number(b.queue?.index || 999999);
    if (ai !== bi) return ai - bi;
    const p = Number(b.priority || 0) - Number(a.priority || 0);
    if (p !== 0) return p;
    return String(b.mtime || "").localeCompare(String(a.mtime || ""));
  });

  const counts = {};
  for (const artifact of artifacts) {
    counts[artifact.state] = (counts[artifact.state] || 0) + 1;
  }

  const activeQueueItems = queueState.queue.map((item) => {
    const match = artifacts.find(
      (a) =>
        a.present &&
        (path.resolve(a.path).toLowerCase() === path.resolve(item.path || "").toLowerCase() ||
          a.name.toLowerCase() === String(item.name || "").toLowerCase()),
    );
    return {
      index: item.index ?? "",
      name: item.name ?? "",
      path: item.path ?? "",
      artifactId: match?.artifactId || "",
      status: item.status ?? "",
      submittedAt: item.submittedAt ?? "",
    };
  });

  return {
    schema: "aicomp-artifact-manifest.v1",
    updatedAt: now,
    root: ROOT,
    submissionsDir: SUBMISSIONS,
    activeQueue: {
      path: QUEUE_PATH,
      updatedAt: queueState.updatedAt,
      refreshDelayMinutes: queueState.refreshDelayMinutes,
      frozen: true,
      items: activeQueueItems,
    },
    counts,
    artifacts,
  };
}

function writeManifestCsv(manifest) {
  const header = [
    "state",
    "queue_index",
    "name",
    "score",
    "rank",
    "leaderboard_publish_time",
    "submitted_at",
    "mtime",
    "priority",
    "artifact_id",
    "warnings",
    "path",
  ];
  const rows = manifest.artifacts.map((a) =>
    [
      a.state,
      a.queue?.index || "",
      a.name,
      a.result?.score || "",
      a.result?.teamRank || "",
      a.result?.leaderboardPublishTime || "",
      a.queue?.submittedAt || "",
      a.mtime || "",
      a.priority || "",
      a.artifactId,
      (a.warnings || []).join(";"),
      a.path,
    ].map(csvCell).join(","),
  );
  writeAtomic(MANIFEST_CSV_PATH, `${header.join(",")}\n${rows.join("\n")}\n`);
}

export function syncManifest() {
  const manifest = buildManifest();
  writeAtomic(MANIFEST_PATH, JSON.stringify(manifest, null, 2));
  writeManifestCsv(manifest);
  return manifest;
}

function printStatus(manifest) {
  const counts = Object.entries(manifest.counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([state, count]) => `${state}=${count}`)
    .join(" ");
  console.log(`manifest: ${MANIFEST_PATH}`);
  console.log(`summary: ${counts}`);

  const candidates = manifest.artifacts.filter((a) => a.state === "candidate");
  if (candidates.length) {
    console.log("new candidates not in active queue:");
    for (const a of candidates.slice(0, 20)) {
      console.log(`  ${a.name} priority=${a.priority} mtime=${a.mtime}`);
    }
    if (candidates.length > 20) console.log(`  ... ${candidates.length - 20} more`);
  }

  const warnings = manifest.artifacts.filter((a) => a.warnings?.length);
  if (warnings.length) {
    console.log("warnings:");
    for (const a of warnings) console.log(`  ${a.name}: ${a.warnings.join("; ")}`);
  }
}

const entryPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
const modulePath = fileURLToPath(import.meta.url);
if (entryPath === modulePath) {
  const cmd = process.argv[2] || "sync";
  if (cmd === "sync" || cmd === "status") {
    printStatus(syncManifest());
  } else if (cmd === "path") {
    console.log(MANIFEST_PATH);
  } else {
    console.error(`Unknown command: ${cmd}`);
    process.exitCode = 1;
  }
}
