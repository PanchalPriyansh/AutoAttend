"""Static checks for two Definition-of-Done items in
01-project-foundation.md that are not exercised by hitting an API:

  - "MONGODB_URI and other config values are read from environment
    variables, not hardcoded anywhere in the backend code."
  - "No secrets or credentials are present in any committed file."
  - "No other new dependencies -- ML and notification libraries are out of
    scope until their respective features." (Face-recognition libraries
    were in that list until 06-face-enrollment.md introduced them.)
  - ".env.example (backend and frontend) exists ... and .env remains
    gitignored."

These are source/repo-structure assertions, not application behavior,
but they map directly to stated Definition-of-Done checklist items for
this feature. Only file existence and plain-text source scanning is
used -- no `.env` file contents are ever read, in line with keeping
credentials out of test flows.
"""

import os
import re

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)

# Skip generated/virtualenv/test directories -- we only care about the
# feature's own hand-written backend source.
EXCLUDED_DIR_NAMES = {"venv", "node_modules", "__pycache__", ".git", "tests", "dist"}

SUSPICIOUS_SECRET_ASSIGNMENT = re.compile(
    r'(?i)\b(PASSWORD|SECRET|SECRET_KEY|API_KEY|TOKEN)\s*=\s*["\'][^"\']{4,}["\']'
)
HARDCODED_MONGO_URI_WITH_CREDENTIALS = re.compile(
    r'mongodb(\+srv)?://[^\s"\']+:[^\s"\']+@[^\s"\']+'
)


def _iter_backend_python_files():
    for dirpath, dirnames, filenames in os.walk(BACKEND_DIR):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


class TestNoHardcodedSecretsInBackendSource:
    def test_no_hardcoded_mongodb_connection_string_with_credentials(self):
        offenders = []
        for path in _iter_backend_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read()
            if HARDCODED_MONGO_URI_WITH_CREDENTIALS.search(content):
                offenders.append(path)

        assert not offenders, f"Hardcoded MongoDB credentials found in: {offenders}"

    def test_no_hardcoded_secret_like_literals_outside_environment_lookups(self):
        offenders = []
        for path in _iter_backend_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if "os.environ" in line or "getenv" in line:
                        continue
                    if SUSPICIOUS_SECRET_ASSIGNMENT.search(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, f"Possible hardcoded secret literals found: {offenders}"

    def test_config_module_sources_its_values_from_environment_variables(self):
        config_path = os.path.join(BACKEND_DIR, "config.py")
        with open(config_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        for env_var in ("MONGODB_URI", "PORT", "CORS_ORIGINS", "FLASK_ENV"):
            assert env_var in content, f"config.py does not reference {env_var}"

        assert "os.environ" in content or "os.getenv" in content


class TestOutOfScopeDependenciesAreNotIntroduced:
    def test_requirements_txt_does_not_include_future_feature_dependencies(self):
        requirements_path = os.path.join(BACKEND_DIR, "requirements.txt")
        with open(requirements_path, "r", encoding="utf-8") as handle:
            content = handle.read().lower()

        # `face_recognition`/`numpy` were removed from this list when
        # .claude/specs/06-face-enrollment.md landed and legitimately
        # introduced them, and `opencv` when 07-attendance-capture.md needed
        # video frames.
        #
        # scikit-learn is different: it is not waiting for a spec, it is
        # ruled out. AutoAttend records attendance and does no training or
        # prediction, so nothing in this project can ever earn it (see
        # CLAUDE.md, "Tech constraints"). The low-attendance email needs no
        # dependency either -- `smtplib` and `email` are in the stdlib.
        out_of_scope_packages = [
            "scikit-learn",
            "sklearn",
        ]
        for package in out_of_scope_packages:
            assert package not in content, (
                f"'{package}' is out of scope for this project: AutoAttend "
                "does no model training or prediction"
            )

    def test_requirements_txt_includes_the_documented_foundation_dependencies(self):
        requirements_path = os.path.join(BACKEND_DIR, "requirements.txt")
        with open(requirements_path, "r", encoding="utf-8") as handle:
            content = handle.read().lower()

        for expected_package in ("flask", "pymongo", "python-dotenv", "flask-cors"):
            assert expected_package in content


class TestRecognitionLibraryImportIsolation:
    """06-face-enrollment.md, "Rules for implementation" + Definition of
    done: "No face_recognition or numpy import exists outside
    backend/recognition/encoder.py." Keeping both imports confined there
    (and lazy, inside functions -- see encoder.py itself) is what lets
    the rest of the backend, and the 01-05 test suites, run on a machine
    where the native `dlib` build is unavailable.
    """

    ENCODER_PATH = os.path.join(BACKEND_DIR, "recognition", "encoder.py")
    IMPORT_PATTERN = re.compile(r"^\s*(import|from)\s+(face_recognition|numpy)\b")

    def test_face_recognition_and_numpy_are_only_imported_in_encoder_py(self):
        offenders = []
        for path in _iter_backend_python_files():
            if os.path.normpath(path) == os.path.normpath(self.ENCODER_PATH):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.IMPORT_PATTERN.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, (
            f"face_recognition/numpy imported outside recognition/encoder.py: {offenders}"
        )


class TestVideoLibraryImportIsolation:
    """07-attendance-capture.md, "Rules for implementation" + Definition of
    done: "No cv2 import exists outside backend/recognition/frames.py."
    Keeping the import confined there (and lazy, inside functions -- see
    frames.py itself) is what lets the rest of the backend, and the
    01-06 test suites, run on a machine where OpenCV is not installed.
    """

    FRAMES_PATH = os.path.join(BACKEND_DIR, "recognition", "frames.py")
    IMPORT_PATTERN = re.compile(r"^\s*(import|from)\s+cv2\b")

    def test_cv2_is_only_imported_in_frames_py(self):
        offenders = []
        for path in _iter_backend_python_files():
            if os.path.normpath(path) == os.path.normpath(self.FRAMES_PATH):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.IMPORT_PATTERN.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, f"cv2 imported outside recognition/frames.py: {offenders}"


class TestEnvExampleFilesAndGitignore:
    def test_an_env_example_file_exists_for_the_backend(self):
        assert os.path.exists(os.path.join(REPO_ROOT, ".env.example")) or os.path.exists(
            os.path.join(BACKEND_DIR, ".env.example")
        )

    def test_an_env_example_file_exists_for_the_frontend(self):
        frontend_env_example = os.path.join(REPO_ROOT, "frontend", ".env.example")
        assert os.path.exists(frontend_env_example)

    def test_gitignore_excludes_env_files_while_keeping_the_example(self):
        gitignore_path = os.path.join(REPO_ROOT, ".gitignore")
        with open(gitignore_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        assert ".env" in content
        assert ".env.example" in content
