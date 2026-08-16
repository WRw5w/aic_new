import shutil
import csv
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

SUBMISSION_ARCNAME = "pred_results.csv"
MAX_GENERATED_SUBMISSION_BYTES = 256 * 1024 * 1024


@dataclass
class SubmissionSpec:
    output_filename: str = "submission.csv"
    archive_name: str | None = None
    requires_zip: bool = False
    has_header: bool = True
    header: list[str] | None = None


@dataclass
class SubmissionArtifact:
    csv_path: Path
    zip_path: Path | None
    spec: SubmissionSpec


@dataclass
class SubmissionAttempt:
    file_path: Path
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def submitted(self):
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class GeneratedSubmissionValidation:
    valid: bool
    code: str


class CommandSubmissionBackend:
    def __init__(self, command, cwd=None, timeout_seconds=600):
        self.command = [str(part) for part in command]
        self.cwd = Path(cwd) if cwd is not None else None
        self.timeout_seconds = timeout_seconds

    def submit(self, file_path):
        file_path = Path(file_path)
        command = [
            part.replace("{file}", str(file_path))
            for part in self.command
        ]
        if all("{file}" not in part for part in self.command):
            command.append(str(file_path))
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                cwd=str(self.cwd) if self.cwd is not None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
            return SubmissionAttempt(
                file_path=file_path,
                command=command,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration=time.time() - start,
            )
        except subprocess.TimeoutExpired as exc:
            return SubmissionAttempt(
                file_path=file_path,
                command=command,
                returncode=-1,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + "\nMAK_SUBMIT_TIMEOUT",
                duration=time.time() - start,
                timed_out=True,
            )


def finalize(best_node, best_workdir, run_dir):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "best_solution.py").write_text(best_node.code, encoding="utf-8")
    src = Path(best_workdir) / "submission.csv"
    if not src.exists():
        return None
    dst = run_dir / "submission.csv"
    shutil.copy(src, dst)
    return dst


def validate_generated_submission(
    csv_path,
    data_dir,
    *,
    max_bytes=MAX_GENERATED_SUBMISSION_BYTES,
) -> GeneratedSubmissionValidation:
    """Validate generated CSV structure against trusted task input when present."""
    candidate = Path(csv_path)
    if not candidate.is_file():
        return GeneratedSubmissionValidation(False, "SUBMISSION_MISSING")
    if candidate.is_symlink():
        return GeneratedSubmissionValidation(False, "SUBMISSION_UNSAFE_PATH")
    candidate_rows, error = _read_bounded_csv(candidate, max_bytes)
    if error is not None:
        return GeneratedSubmissionValidation(False, error)
    if not candidate_rows:
        return GeneratedSubmissionValidation(False, "SUBMISSION_EMPTY")

    sample_path = Path(data_dir) / "sample_submission.csv"
    if not sample_path.is_file():
        return GeneratedSubmissionValidation(True, "SUBMISSION_VALID")
    sample_rows, error = _read_bounded_csv(sample_path, max_bytes)
    if error is not None or not sample_rows:
        return GeneratedSubmissionValidation(False, "TRUSTED_SAMPLE_INVALID")

    sample_header, sample_data = sample_rows[0], sample_rows[1:]
    candidate_header, candidate_data = candidate_rows[0], candidate_rows[1:]
    if candidate_header != sample_header:
        return GeneratedSubmissionValidation(False, "SUBMISSION_HEADER_MISMATCH")
    if len(candidate_data) != len(sample_data):
        return GeneratedSubmissionValidation(False, "SUBMISSION_ROW_COUNT_MISMATCH")
    expected_width = len(sample_header)
    if expected_width == 0 or any(
        len(row) != expected_width for row in candidate_data
    ):
        return GeneratedSubmissionValidation(False, "SUBMISSION_ROW_WIDTH_MISMATCH")
    candidate_keys = [row[0] for row in candidate_data]
    if len(candidate_keys) != len(set(candidate_keys)):
        return GeneratedSubmissionValidation(False, "SUBMISSION_KEYS_DUPLICATED")
    sample_keys = [row[0] for row in sample_data]
    if candidate_keys != sample_keys:
        return GeneratedSubmissionValidation(False, "SUBMISSION_KEY_ORDER_MISMATCH")
    return GeneratedSubmissionValidation(True, "SUBMISSION_VALID")


def _read_bounded_csv(
    path: Path,
    max_bytes: int,
) -> tuple[list[list[str]], str | None]:
    try:
        if path.stat().st_size > max_bytes:
            return [], "SUBMISSION_TOO_LARGE"
        with path.open(newline="", encoding="utf-8-sig") as fp:
            return list(csv.reader(fp)), None
    except (OSError, UnicodeError, csv.Error):
        return [], "SUBMISSION_UNREADABLE"


def save_image_classification_predictions(output_csv, filenames, pred_indices, idx_to_class):
    if len(filenames) != len(pred_indices):
        raise ValueError("filenames and pred_indices must have the same length")
    output_csv = Path(output_csv)
    rows = [[name, idx_to_class[pred]] for name, pred in zip(filenames, pred_indices)]
    return write_submission_rows(
        output_csv,
        rows,
        SubmissionSpec(
            output_filename=output_csv.name,
            archive_name=SUBMISSION_ARCNAME,
            requires_zip=True,
            has_header=False,
        ),
    )


def write_submission_rows(output_csv, rows, spec: SubmissionSpec):
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        if spec.has_header and spec.header:
            writer.writerow(spec.header)
        writer.writerows(rows)
    return output_csv


def package_file_submission(csv_path, spec: SubmissionSpec):
    csv_path = Path(csv_path)
    zip_path = None
    if spec.requires_zip:
        if not spec.archive_name:
            raise ValueError("archive_name is required when requires_zip=True")
        zip_path = zip_submission(csv_path, arcname=spec.archive_name)
    return SubmissionArtifact(csv_path=csv_path, zip_path=zip_path, spec=spec)


def zip_submission(csv_path, arcname=SUBMISSION_ARCNAME):
    csv_path = Path(csv_path)
    zip_path = csv_path.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(csv_path, arcname=arcname)
    return zip_path


def validate_image_classification_submission(csv_path, zip_path, test_dir, num_classes):
    csv_path = Path(csv_path)
    test_dir = Path(test_dir)
    ok = True

    if zip_path is not None:
        zip_path = Path(zip_path)
        if not zip_path.exists():
            ok = False
        else:
            with zipfile.ZipFile(zip_path) as zf:
                if zf.namelist() != [SUBMISSION_ARCNAME]:
                    ok = False

    rows = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as fp:
            for row in csv.reader(fp):
                if len(row) != 2:
                    ok = False
                    continue
                rows.append((row[0], row[1].strip()))
    except OSError:
        return False

    valid_classes = {f"{i:04d}" for i in range(num_classes)}
    if any(class_id not in valid_classes for _, class_id in rows):
        ok = False

    test_files = {p.name for p in test_dir.iterdir() if p.is_file()}
    pred_files = [name for name, _ in rows]
    pred_set = set(pred_files)
    if len(pred_files) != len(pred_set):
        ok = False
    if test_files != pred_set:
        ok = False

    return ok


def validate_submission_rows(
    csv_path,
    spec: SubmissionSpec,
    expected_keys=None,
    valid_values=None,
    key_column_index=0,
    value_column_index=1,
):
    rows = []
    try:
        with Path(csv_path).open(newline="", encoding="utf-8") as fp:
            reader = csv.reader(fp)
            if spec.has_header:
                header = next(reader, None)
                if spec.header is not None and header != spec.header:
                    return False
            for row in reader:
                rows.append(row)
    except (OSError, StopIteration):
        return False

    max_index = max(key_column_index, value_column_index)
    if any(len(row) <= max_index for row in rows):
        return False

    keys = [row[key_column_index] for row in rows]
    if len(keys) != len(set(keys)):
        return False

    if expected_keys is not None and set(keys) != set(expected_keys):
        return False

    if valid_values is not None:
        allowed = set(valid_values)
        values = [row[value_column_index] for row in rows]
        if any(value not in allowed for value in values):
            return False

    return True
