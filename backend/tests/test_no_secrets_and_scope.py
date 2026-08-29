"""Static checks for two Definition-of-Done items in
01-project-foundation.md that are not exercised by hitting an API:

  - "MONGODB_URI and other config values are read from environment
    variables, not hardcoded anywhere in the backend code."
  - "No secrets or credentials are present in any committed file."
  - "No other new dependencies -- ML and notification libraries are out of
    scope until their respective features." (Face-recognition libraries
    were in that list until 06-face-enrollment.md introduced them. The
    notification half resolved differently: 10-low-attendance-notifications.md
    landed the mailer on stdlib `smtplib`/`email` and added no package at
    all, so there is still nothing to allow here.)
  - Extended by 10-low-attendance-notifications.md, "Rules for
    implementation" 18: SMTP credentials are read from Config only, in
    notifications/settings.py only -- never hardcoded. The generic secret
    scan below covers that, and TestSmtpCredentialsAreNotHardcoded adds
    the host/address shapes it would otherwise miss.
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


class TestSmtpCredentialsAreNotHardcoded:
    """10-low-attendance-notifications.md, "Rules for implementation" 18 +
    Definition of done: "A scan of the backend source finds no SMTP host,
    address, or password literal."

    The generic secret scan above already catches a `SMTP_PASSWORD = "..."`
    style assignment. What it cannot catch is a hostname or a from-address
    pasted in as a default, which is neither a "password"-shaped name nor
    a Mongo URI -- so those two shapes are checked here.
    """

    SMTP_HOST_LITERAL = re.compile(
        r'(?i)["\'](?:smtp|mail)\.[a-z0-9.-]+\.[a-z]{2,}["\']'
    )
    EMAIL_LITERAL = re.compile(r'["\'][^"\'\s@]+@[a-z0-9.-]+\.[a-z]{2,}["\']')

    def test_no_smtp_hostname_literal_in_backend_source(self):
        offenders = []
        for path in _iter_backend_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if "os.environ" in line or "getenv" in line:
                        continue
                    if self.SMTP_HOST_LITERAL.search(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, f"Possible hardcoded SMTP host found: {offenders}"

    def test_no_email_address_literal_in_backend_source(self):
        offenders = []
        for path in _iter_backend_python_files():
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    stripped = line.strip()
                    # Regex *patterns* legitimately contain address-shaped
                    # text -- database/schema.py's `email` validator is one.
                    if "os.environ" in line or "getenv" in line:
                        continue
                    if stripped.startswith("#") or '"pattern"' in line:
                        continue
                    if self.EMAIL_LITERAL.search(line):
                        offenders.append(f"{path}:{line_number}: {stripped}")

        assert not offenders, f"Possible hardcoded email address found: {offenders}"

    def test_smtp_settings_are_read_only_by_the_notifications_settings_module(self):
        """Rule 18: "SMTP credentials are read from Config only, in
        notifications/settings.py only." One reader is what makes "never
        logged, never printed, never stored" a property of a single file
        rather than a habit every caller has to remember.

        config.py is excluded because declaring the settings is its job.
        """
        settings_path = os.path.join(BACKEND_DIR, "notifications", "settings.py")
        config_path = os.path.join(BACKEND_DIR, "config.py")
        allowed = {os.path.normpath(settings_path), os.path.normpath(config_path)}

        offenders = []
        for path in _iter_backend_python_files():
            if os.path.normpath(path) in allowed:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if line.strip().startswith("#"):
                        continue
                    if re.search(r"\bSMTP_(HOST|USERNAME|PASSWORD|PORT|FROM_)", line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, (
            f"SMTP configuration read outside notifications/settings.py: {offenders}"
        )


class TestMailLibraryImportIsolation:
    """10-low-attendance-notifications.md Definition of done: "`smtplib`
    and `email` are imported nowhere outside
    backend/notifications/mailer.py."

    The same containment rule the two CV libraries get, for a different
    reason: everything above the mailer deals in strings and plain dicts,
    which is what lets the whole sweep be tested without a socket, a
    credential, or a mail server.
    """

    MAILER_PATH = os.path.join(BACKEND_DIR, "notifications", "mailer.py")
    IMPORT_PATTERN = re.compile(r"^\s*(import|from)\s+(smtplib|email)\b")

    def test_smtplib_and_email_are_only_imported_in_mailer_py(self):
        offenders = []
        for path in _iter_backend_python_files():
            if os.path.normpath(path) == os.path.normpath(self.MAILER_PATH):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.IMPORT_PATTERN.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, (
            f"smtplib/email imported outside notifications/mailer.py: {offenders}"
        )


class TestNotificationsPackageStaysOffTheCvPath:
    """10-low-attendance-notifications.md, rule 26 + Definition of done:
    nothing under backend/notifications/ may import a CV library or reach
    into recognition/.

    Mailing a student about their attendance has nothing to do with
    decoding a video, and a stray import would make the sweep unrunnable
    on a server where `dlib` was never built -- which is most of them.
    """

    NOTIFICATIONS_DIR = os.path.join(BACKEND_DIR, "notifications")
    FORBIDDEN_IMPORT = re.compile(
        r"^\s*(import|from)\s+(face_recognition|numpy|cv2|recognition)\b"
    )

    def test_no_notifications_module_imports_a_cv_library_or_recognition(self):
        offenders = []
        for path in _iter_backend_python_files():
            if not os.path.normpath(path).startswith(
                os.path.normpath(self.NOTIFICATIONS_DIR)
            ):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.FORBIDDEN_IMPORT.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, (
            f"notifications/ must not depend on CV code: {offenders}"
        )


class TestAttendancePackageDoesNotImportNotifications:
    """11-student-attendance-threshold.md, "Rules for implementation" +
    Definition of done: "No file under backend/attendance/ imports from
    notifications/."

    notifications/ is CLI-only by design -- test_app_factory.py already
    asserts no route contains "notification" so nothing on a request path
    can send mail. attendance/threshold.py reads the same
    Config.LOW_ATTENDANCE_THRESHOLD value independently rather than
    borrowing notifications/settings.py::load_sweep_settings or its
    MIN_RECORDED_LECTURES, precisely so this import never has a reason to
    exist. The reverse direction (notifications/service.py importing
    attendance_percentage from attendance/threshold.py) is fine and
    unaffected by this guard -- see
    TestNotificationsPackageStaysOffTheCvPath above for that package's own
    isolation rules.
    """

    ATTENDANCE_DIR = os.path.join(BACKEND_DIR, "attendance")
    FORBIDDEN_IMPORT = re.compile(r"^\s*(import|from)\s+notifications\b")

    def test_no_attendance_module_imports_from_notifications(self):
        offenders = []
        for path in _iter_backend_python_files():
            if not os.path.normpath(path).startswith(
                os.path.normpath(self.ATTENDANCE_DIR)
            ):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.FORBIDDEN_IMPORT.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, f"attendance/ must not depend on notifications/: {offenders}"


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


class TestPdfLibraryImportIsolation:
    """21-attendance-export.md, "Rules for implementation" 16 +
    Definition of done -> "Backend -- PDF" 27: "reportlab is imported in
    pdf_export.py and nowhere else, lazily, inside the function."

    The third of these, after face_recognition/numpy and cv2, and it
    guards the same property for the same reason: a module-level import
    would put the library on the import path of every request the
    attendance blueprint serves, and would make `import app` fail on a
    machine that does not have it -- taking the whole API down over one
    endpoint. Lazy and confined is what lets the app start, serve every
    other route, and still export CSV where reportlab is missing, with
    only the PDF endpoint reporting 503.

    Unlike dlib, reportlab is pure Python and installs anywhere, so this
    is not about a build that might fail. It is about one feature's
    dependency staying one feature's dependency.
    """

    PDF_EXPORT_PATH = os.path.join(BACKEND_DIR, "attendance", "pdf_export.py")
    IMPORT_PATTERN = re.compile(r"^(\s*)(?:import|from)\s+reportlab\b")

    def test_reportlab_is_only_imported_in_pdf_export_py(self):
        offenders = []
        for path in _iter_backend_python_files():
            if os.path.normpath(path) == os.path.normpath(self.PDF_EXPORT_PATH):
                continue
            with open(path, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if self.IMPORT_PATTERN.match(line):
                        offenders.append(f"{path}:{line_number}: {line.strip()}")

        assert not offenders, (
            f"reportlab imported outside attendance/pdf_export.py: {offenders}"
        )

    def test_every_reportlab_import_in_pdf_export_py_is_indented(self):
        """Indentation is the check because it is what "inside a function"
        looks like to a plain source scan -- the same shape encoder.py and
        frames.py keep their optional imports in.
        """
        unindented = []
        with open(self.PDF_EXPORT_PATH, "r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                match = self.IMPORT_PATTERN.match(line)
                if match and not match.group(1):
                    unindented.append(f"{line_number}: {line.strip()}")

        assert not unindented, (
            "reportlab must be imported inside a function in pdf_export.py, "
            f"not at module scope: {unindented}"
        )

    def test_importing_the_module_does_not_import_the_library(self):
        """The behavioural half of the two source scans above.

        `attendance.pdf_export` is force-reloaded rather than fetched from
        the module cache -- a cached import executes nothing, so the
        obvious version of this test passes even against a module-level
        import. Both it and any `reportlab` entries are removed first and
        restored afterwards, so the rest of the suite is unaffected.
        """
        import importlib
        import sys

        def reportlab_modules():
            return [
                name
                for name in list(sys.modules)
                if name == "reportlab" or name.startswith("reportlab.")
            ]

        saved = {name: sys.modules[name] for name in reportlab_modules()}
        saved_self = sys.modules.pop("attendance.pdf_export", None)
        for name in saved:
            del sys.modules[name]

        try:
            module = importlib.import_module("attendance.pdf_export")

            assert module.is_available is not None
            assert not reportlab_modules(), (
                "importing attendance.pdf_export pulled reportlab in with it; "
                "the import must be inside the function that needs it"
            )
        finally:
            sys.modules.update(saved)
            if saved_self is not None:
                sys.modules["attendance.pdf_export"] = saved_self
