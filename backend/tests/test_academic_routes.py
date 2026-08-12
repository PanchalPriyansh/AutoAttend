"""Tests for backend/routes/academic.py (the `academic_bp` blueprint) and
backend/academic/{service,validators,serializers,levels,errors}.py.

Spec contract under test (04-academic-hierarchy-management.md, "APIs" +
"Database changes" + "Rules for implementation" + "Definition of done"):
  - 20 endpoints (5 levels x GET/POST/PUT/DELETE) under `/api`, scoped
    top-down: Institute -> Department -> Semester -> Course -> Class.
  - Writes (POST/PUT/DELETE) are admin-only; reads (GET) are admin or
    faculty. Students get 403 on everything; unauthenticated gets 401.
  - A create verifies its parent exists (404 if missing, 400 if the
    parent id is malformed). A list is scoped by a required parent query
    parameter (400 if missing). Duplicates are scoped to the parent
    (409), never global except for institutes (which have no parent).
  - Semester `end_date` must be strictly after `start_date` (400
    otherwise); dates are stored as BSON dates, never ISO strings.
  - Deletes are blocked (409, never cascaded) while children exist, with
    a message naming the blocking count and correct singular/plural
    ("1 department belongs" vs "2 departments belong").
  - `PUT` never re-parents -- only the level's own fields are updated.
  - `classes.faculty_id` is always null on create and is read-only.
  - Every response serializes `_id` as `id` (string), ObjectId refs as
    strings, and dates as ISO 8601 strings -- no BSON leaks into JSON.
  - CSRF: a mutating request with valid cookies but a missing/incorrect
    `X-CSRF-TOKEN` header is rejected (401), matching 03-authentication.

`routes.auth.get_db` and `routes.academic.get_db` are both monkeypatched
to the *same* in-memory fake db (academic_test_helpers.py) so a real
`/api/auth/login` call can mint genuine JWT + CSRF cookies while the
hierarchy endpoints read/write the fake collections -- no live MongoDB is
required. All emails/passwords/institutional data used are obviously
fake, test-only values; no realistic institutional data is seeded.
"""

from datetime import datetime

import pytest
from academic_test_helpers import (
    build_chain_documents,
    make_class,
    make_class_enrollment,
    make_course,
    make_department,
    make_fake_academic_db,
    make_institute,
)
from auth_test_helpers import make_user
from bson import ObjectId

FAKE_PASSWORD = "a-fake-test-password-1"


def _patch_db(monkeypatch, fake_db):
    """Both blueprints must see the same fake db: login goes through
    routes.auth.get_db, hierarchy requests go through routes.academic.get_db.
    """
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.academic.get_db", lambda: fake_db)
    return fake_db


def _login(client, email, password=FAKE_PASSWORD):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login setup failed: {response.get_json()}"
    return response


def _csrf_headers(client):
    cookie = client.get_cookie("csrf_access_token", path="/")
    assert cookie is not None, "csrf_access_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _login_as(monkeypatch, client, role, *, email=None, **hierarchy):
    """Builds a fake db with one user of `role` plus whatever hierarchy
    collections are passed in, patches both blueprints' get_db, logs in,
    and returns (fake_db, csrf_headers).
    """
    email = email or f"{role}@college.test"
    user = make_user(email=email, password=FAKE_PASSWORD, role=role)
    fake_db = make_fake_academic_db(users=[user], **hierarchy)
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _login_admin_with_chain(monkeypatch, client, email="admin@college.test"):
    """Seeds a full institute->department->semester->course->class chain,
    an admin user, patches the db, logs the admin in, and returns
    (fake_db, ids, docs, csrf_headers).
    """
    docs, ids = build_chain_documents()
    admin = make_user(email=email, password=FAKE_PASSWORD, role="admin")
    fake_db = make_fake_academic_db(
        users=[admin],
        institutes=[docs["institute"]],
        departments=[docs["department"]],
        semesters=[docs["semester"]],
        courses=[docs["course"]],
        classes=[docs["class"]],
    )
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, ids, docs, _csrf_headers(client)


# --- Happy path: institutes CRUD ---------------------------------------


class TestInstituteCrudHappyPath:
    def test_create_institute_returns_201_with_serialized_document(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/institutes",
                json={"name": "Acme Institute", "code": "ACME"},
                headers=headers,
            )

        assert response.status_code == 201
        body = response.get_json()
        assert body["name"] == "Acme Institute"
        assert body["code"] == "ACME"
        assert isinstance(body["id"], str) and body["id"]
        assert isinstance(body["created_at"], str)

    def test_list_institutes_returns_created_institute_under_institutes_key(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created_id = client.post(
                "/api/institutes",
                json={"name": "Listed Institute", "code": "LI"},
                headers=headers,
            ).get_json()["id"]

            response = client.get("/api/institutes")

        assert response.status_code == 200
        body = response.get_json()
        assert "institutes" in body
        assert created_id in [item["id"] for item in body["institutes"]]

    def test_update_institute_changes_name_and_code(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/institutes", json={"name": "Old Name", "code": "OLD"}, headers=headers
            ).get_json()

            response = client.put(
                f"/api/institutes/{created['id']}",
                json={"name": "New Name", "code": "NEW"},
                headers=headers,
            )

        assert response.status_code == 200
        body = response.get_json()
        assert body["id"] == created["id"]
        assert body["name"] == "New Name"
        assert body["code"] == "NEW"

    def test_delete_institute_returns_200_and_removes_it_from_list(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/institutes", json={"name": "To Delete", "code": "DEL"}, headers=headers
            ).get_json()

            delete_response = client.delete(f"/api/institutes/{created['id']}", headers=headers)
            list_response = client.get("/api/institutes")

        assert delete_response.status_code == 200
        remaining_ids = [item["id"] for item in list_response.get_json()["institutes"]]
        assert created["id"] not in remaining_ids


# --- Full top-down chain -------------------------------------------------


class TestFullHierarchyChainCreatesTopDown:
    def test_institute_department_semester_course_class_chain_creates_successfully(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            institute = client.post(
                "/api/institutes",
                json={"name": "Chain Institute", "code": "CHAIN"},
                headers=headers,
            )
            assert institute.status_code == 201
            institute_id = institute.get_json()["id"]

            department = client.post(
                "/api/departments",
                json={"institute_id": institute_id, "name": "Computer Science", "code": "CS"},
                headers=headers,
            )
            assert department.status_code == 201
            department_id = department.get_json()["id"]
            assert department.get_json()["institute_id"] == institute_id

            semester = client.post(
                "/api/semesters",
                json={
                    "department_id": department_id,
                    "name": "Fall 2026",
                    "start_date": "2026-08-01",
                    "end_date": "2026-12-15",
                },
                headers=headers,
            )
            assert semester.status_code == 201
            semester_id = semester.get_json()["id"]
            assert semester.get_json()["department_id"] == department_id

            course = client.post(
                "/api/courses",
                json={"semester_id": semester_id, "name": "Data Structures", "code": "CS201"},
                headers=headers,
            )
            assert course.status_code == 201
            course_id = course.get_json()["id"]
            assert course.get_json()["semester_id"] == semester_id

            klass = client.post(
                "/api/classes",
                json={"course_id": course_id, "name": "Section A"},
                headers=headers,
            )

        assert klass.status_code == 201
        klass_body = klass.get_json()
        assert klass_body["course_id"] == course_id
        assert klass_body["faculty_id"] is None

    def test_class_faculty_id_is_always_null_on_create(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/classes",
                json={"course_id": ids["course"], "name": "Another Section"},
                headers=headers,
            )

        assert response.status_code == 201
        assert response.get_json()["faculty_id"] is None


# --- Parent existence on create ------------------------------------------


class TestParentValidationOnCreate:
    def test_create_department_with_nonexistent_institute_id_returns_404(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/departments",
                json={"institute_id": str(ObjectId()), "name": "Ghost Dept", "code": "GD"},
                headers=headers,
            )

        assert response.status_code == 404

    def test_create_department_with_malformed_institute_id_returns_400(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/departments",
                json={"institute_id": "not-an-object-id", "name": "Ghost Dept", "code": "GD"},
                headers=headers,
            )

        assert response.status_code == 400

    def test_create_class_with_nonexistent_course_id_returns_404(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/classes",
                json={"course_id": str(ObjectId()), "name": "Ghost Section"},
                headers=headers,
            )

        assert response.status_code == 404

    def test_create_class_with_malformed_course_id_returns_400(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/classes",
                json={"course_id": "not-an-object-id", "name": "Ghost Section"},
                headers=headers,
            )

        assert response.status_code == 400


# --- Lists are scoped to their parent, never across it -------------------


class TestListScopedToParent:
    def test_departments_list_without_institute_id_returns_400(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.get("/api/departments")

        assert response.status_code == 400

    def test_departments_list_returns_only_the_requested_institutes_departments(
        self, app_instance, monkeypatch
    ):
        institute_a = make_institute(name="Institute A", code="IA")
        institute_b = make_institute(name="Institute B", code="IB")
        department_a = make_department(institute_a["_id"], name="Dept A", code="DA")
        department_b = make_department(institute_b["_id"], name="Dept B", code="DB")

        admin = make_user(email="admin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_academic_db(
            users=[admin],
            institutes=[institute_a, institute_b],
            departments=[department_a, department_b],
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "admin@college.test", FAKE_PASSWORD)

            response = client.get(f"/api/departments?institute_id={institute_a['_id']}")

        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["departments"]]
        assert names == ["Dept A"]

    def test_classes_list_without_course_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.get("/api/classes")

        assert response.status_code == 400

    def test_classes_list_returns_only_the_requested_courses_classes(
        self, app_instance, monkeypatch
    ):
        course_a = make_course(ObjectId(), name="Course A", code="CA")
        course_b = make_course(ObjectId(), name="Course B", code="CB")
        class_a = make_class(course_a["_id"], name="Class A")
        class_b = make_class(course_b["_id"], name="Class B")

        admin = make_user(email="admin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_academic_db(
            users=[admin], courses=[course_a, course_b], classes=[class_a, class_b]
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "admin@college.test", FAKE_PASSWORD)

            response = client.get(f"/api/classes?course_id={course_a['_id']}")

        assert response.status_code == 200
        names = [item["name"] for item in response.get_json()["classes"]]
        assert names == ["Class A"]


# --- Duplicates are scoped to the parent ---------------------------------


class TestDuplicateScopedToParent:
    def test_duplicate_institute_code_returns_409(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            client.post(
                "/api/institutes", json={"name": "First", "code": "DUP"}, headers=headers
            )

            response = client.post(
                "/api/institutes", json={"name": "Second", "code": "DUP"}, headers=headers
            )

        assert response.status_code == 409

    def test_duplicate_department_code_under_same_institute_returns_409(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            client.post(
                "/api/departments",
                json={"institute_id": ids["institute"], "name": "Dup Dept", "code": "DUPD"},
                headers=headers,
            )
            response = client.post(
                "/api/departments",
                json={"institute_id": ids["institute"], "name": "Dup Dept 2", "code": "DUPD"},
                headers=headers,
            )

        assert response.status_code == 409

    def test_same_department_code_under_a_different_institute_succeeds(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            institute_1 = client.post(
                "/api/institutes", json={"name": "Inst 1", "code": "I1"}, headers=headers
            ).get_json()
            institute_2 = client.post(
                "/api/institutes", json={"name": "Inst 2", "code": "I2"}, headers=headers
            ).get_json()

            first = client.post(
                "/api/departments",
                json={"institute_id": institute_1["id"], "name": "Shared Code Dept", "code": "SC"},
                headers=headers,
            )
            second = client.post(
                "/api/departments",
                json={"institute_id": institute_2["id"], "name": "Shared Code Dept", "code": "SC"},
                headers=headers,
            )

        assert first.status_code == 201
        assert second.status_code == 201

    def test_duplicate_class_name_under_same_course_returns_409(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            client.post(
                "/api/classes", json={"course_id": ids["course"], "name": "Dup Section"},
                headers=headers,
            )
            response = client.post(
                "/api/classes", json={"course_id": ids["course"], "name": "Dup Section"},
                headers=headers,
            )

        assert response.status_code == 409

    def test_same_class_name_under_a_different_course_succeeds(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            second_course = client.post(
                "/api/courses",
                json={"semester_id": ids["semester"], "name": "Other Course", "code": "OC1"},
                headers=headers,
            ).get_json()

            first = client.post(
                "/api/classes", json={"course_id": ids["course"], "name": "Same Name"},
                headers=headers,
            )
            second = client.post(
                "/api/classes", json={"course_id": second_course["id"], "name": "Same Name"},
                headers=headers,
            )

        assert first.status_code == 201
        assert second.status_code == 201


# --- Semester date validation ---------------------------------------------


class TestSemesterDateValidation:
    def test_end_date_equal_to_start_date_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/semesters",
                json={
                    "department_id": ids["department"],
                    "name": "Equal Dates",
                    "start_date": "2026-01-01",
                    "end_date": "2026-01-01",
                },
                headers=headers,
            )

        assert response.status_code == 400

    def test_end_date_before_start_date_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/semesters",
                json={
                    "department_id": ids["department"],
                    "name": "Reversed Dates",
                    "start_date": "2026-06-01",
                    "end_date": "2026-01-01",
                },
                headers=headers,
            )

        assert response.status_code == 400

    def test_valid_semester_stores_dates_as_bson_datetimes_not_strings(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            fake_db, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            created = client.post(
                "/api/semesters",
                json={
                    "department_id": ids["department"],
                    "name": "Valid Semester",
                    "start_date": "2026-01-01",
                    "end_date": "2026-06-01",
                },
                headers=headers,
            ).get_json()

        from database.schema import SEMESTERS

        raw = fake_db[SEMESTERS].find_one({"_id": ObjectId(created["id"])})
        assert isinstance(raw["start_date"], datetime)
        assert isinstance(raw["end_date"], datetime)
        assert isinstance(raw["created_at"], datetime)
        assert not isinstance(raw["start_date"], str)

    def test_semester_response_serializes_dates_as_iso_strings(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/semesters",
                json={
                    "department_id": ids["department"],
                    "name": "ISO Semester",
                    "start_date": "2026-01-01",
                    "end_date": "2026-06-01",
                },
                headers=headers,
            )

        body = response.get_json()
        parsed_start = datetime.fromisoformat(body["start_date"])
        parsed_end = datetime.fromisoformat(body["end_date"])
        assert parsed_start.tzinfo is not None
        assert parsed_end.tzinfo is not None


# --- Deletes are blocked, never cascaded ----------------------------------


class TestDeleteBlockedByChildren:
    def test_delete_institute_blocked_by_single_department_uses_singular_message(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.delete(f"/api/institutes/{ids['institute']}", headers=headers)

        assert response.status_code == 409
        message = response.get_json()["error"]
        assert "1 department belongs to this institute" in message

    def test_delete_institute_blocked_by_multiple_departments_uses_plural_message(
        self, app_instance, monkeypatch
    ):
        institute = make_institute()
        department_1 = make_department(institute["_id"])
        department_2 = make_department(institute["_id"], code="TD2")
        admin = make_user(email="admin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_academic_db(
            users=[admin], institutes=[institute], departments=[department_1, department_2]
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "admin@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            response = client.delete(f"/api/institutes/{institute['_id']}", headers=headers)

        assert response.status_code == 409
        message = response.get_json()["error"]
        assert "2 departments belong to this institute" in message

    def test_delete_department_blocked_by_semester_returns_409(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.delete(f"/api/departments/{ids['department']}", headers=headers)

        assert response.status_code == 409
        assert "1 semester belongs to this department" in response.get_json()["error"]

    def test_delete_semester_blocked_by_course_returns_409(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.delete(f"/api/semesters/{ids['semester']}", headers=headers)

        assert response.status_code == 409
        assert "1 course belongs to this semester" in response.get_json()["error"]

    def test_delete_course_blocked_by_class_returns_409(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.delete(f"/api/courses/{ids['course']}", headers=headers)

        assert response.status_code == 409
        assert "1 class belongs to this course" in response.get_json()["error"]

    def test_delete_class_blocked_by_enrollment_returns_409(self, app_instance, monkeypatch):
        docs, ids = build_chain_documents()
        enrollment = make_class_enrollment(docs["class"]["_id"], ObjectId())
        admin = make_user(email="admin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_academic_db(
            users=[admin],
            institutes=[docs["institute"]],
            departments=[docs["department"]],
            semesters=[docs["semester"]],
            courses=[docs["course"]],
            classes=[docs["class"]],
            class_enrollments=[enrollment],
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "admin@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            response = client.delete(f"/api/classes/{ids['class']}", headers=headers)

        assert response.status_code == 409
        assert "1 enrollment belongs to this class" in response.get_json()["error"]

    def test_institute_still_exists_after_a_blocked_delete_attempt(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            client.delete(f"/api/institutes/{ids['institute']}", headers=headers)
            response = client.get("/api/institutes")

        remaining_ids = [item["id"] for item in response.get_json()["institutes"]]
        assert ids["institute"] in remaining_ids

    def test_delete_succeeds_once_the_blocking_child_is_removed(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            # Remove the whole subtree bottom-up so the institute delete is
            # no longer blocked.
            assert client.delete(f"/api/classes/{ids['class']}", headers=headers).status_code == 200
            assert client.delete(f"/api/courses/{ids['course']}", headers=headers).status_code == 200
            assert client.delete(f"/api/semesters/{ids['semester']}", headers=headers).status_code == 200
            assert client.delete(f"/api/departments/{ids['department']}", headers=headers).status_code == 200

            response = client.delete(f"/api/institutes/{ids['institute']}", headers=headers)

        assert response.status_code == 200


# --- PUT never re-parents --------------------------------------------------


class TestNoReparentingOnPut:
    def test_put_department_ignores_institute_id_in_body(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            institute_a = client.post(
                "/api/institutes", json={"name": "Institute A", "code": "IA"}, headers=headers
            ).get_json()
            institute_b = client.post(
                "/api/institutes", json={"name": "Institute B", "code": "IB"}, headers=headers
            ).get_json()
            department = client.post(
                "/api/departments",
                json={"institute_id": institute_a["id"], "name": "Dept", "code": "D1"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/departments/{department['id']}",
                json={
                    "name": "Renamed Dept",
                    "code": "D1",
                    "institute_id": institute_b["id"],
                },
                headers=headers,
            )

            still_under_a = client.get(f"/api/departments?institute_id={institute_a['id']}")
            under_b = client.get(f"/api/departments?institute_id={institute_b['id']}")

        assert response.status_code == 200
        assert response.get_json()["institute_id"] == institute_a["id"]
        assert response.get_json()["name"] == "Renamed Dept"
        assert department["id"] in [d["id"] for d in still_under_a.get_json()["departments"]]
        assert department["id"] not in [d["id"] for d in under_b.get_json()["departments"]]

    def test_put_class_ignores_course_id_in_body(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            other_course = client.post(
                "/api/courses",
                json={"semester_id": ids["semester"], "name": "Other Course", "code": "OC2"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/classes/{ids['class']}",
                json={"name": "Renamed Section", "course_id": other_course["id"]},
                headers=headers,
            )

        assert response.status_code == 200
        assert response.get_json()["course_id"] == ids["course"]
        assert response.get_json()["name"] == "Renamed Section"


# --- classes.faculty_id is null and read-only -------------------------------


class TestClassFacultyIdReadOnly:
    def test_faculty_id_is_null_in_serialized_class(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.get(f"/api/classes?course_id={ids['course']}")

        classes = response.get_json()["classes"]
        assert len(classes) == 1
        assert classes[0]["faculty_id"] is None

    def test_put_class_cannot_set_faculty_id(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.put(
                f"/api/classes/{ids['class']}",
                json={"name": "Section A", "faculty_id": str(ObjectId())},
                headers=headers,
            )

        assert response.status_code == 200
        assert response.get_json()["faculty_id"] is None


# --- Response shape / serialization -----------------------------------------


class TestSerializationShape:
    def test_created_document_has_a_string_id_not_an_objectid(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/institutes", json={"name": "Shape Institute", "code": "SHAPE"},
                headers=headers,
            )

        body = response.get_json()
        assert isinstance(body["id"], str)
        # Must be a valid 24-char hex ObjectId string, not a Python repr.
        assert ObjectId.is_valid(body["id"])
        assert "ObjectId(" not in response.get_data(as_text=True)

    def test_list_response_never_leaks_bson_objectid_repr(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.get(f"/api/departments?institute_id={ids['institute']}")

        assert "ObjectId(" not in response.get_data(as_text=True)

    def test_created_at_is_an_iso_string(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/institutes", json={"name": "Date Institute", "code": "DATE"},
                headers=headers,
            )

        created_at = response.get_json()["created_at"]
        assert isinstance(created_at, str)
        # Must round-trip through fromisoformat without raising.
        datetime.fromisoformat(created_at)


# --- List responses are keyed, not bare arrays ------------------------------


class TestListResponseShapeIsKeyed:
    def test_institutes_list_is_keyed_under_institutes(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.get("/api/institutes")

        assert response.status_code == 200
        assert isinstance(response.get_json(), dict)
        assert "institutes" in response.get_json()

    def test_classes_list_is_keyed_under_classes(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.get(f"/api/classes?course_id={ids['course']}")

        assert response.status_code == 200
        assert isinstance(response.get_json(), dict)
        assert "classes" in response.get_json()


# --- 404s and malformed-id 400s on update/delete ----------------------------


class TestNotFoundAndMalformedIdOnTargets:
    def test_update_nonexistent_institute_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                f"/api/institutes/{ObjectId()}",
                json={"name": "Nope", "code": "NOPE"},
                headers=headers,
            )

        assert response.status_code == 404

    def test_delete_nonexistent_department_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete(f"/api/departments/{ObjectId()}", headers=headers)

        assert response.status_code == 404

    def test_update_with_a_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                "/api/institutes/not-a-valid-id",
                json={"name": "Nope", "code": "NOPE"},
                headers=headers,
            )

        assert response.status_code == 400

    def test_delete_with_a_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete("/api/institutes/not-a-valid-id", headers=headers)

        assert response.status_code == 400


# --- Field validation --------------------------------------------------------


class TestFieldValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"name": "No Code"},
            {"code": "NC"},
            {"name": "", "code": "BLANK"},
            {"name": "Blank Code", "code": ""},
            {"name": "   ", "code": "WS"},
            {"name": 12345, "code": "BAD"},
        ],
        ids=[
            "empty-body",
            "missing-code",
            "missing-name",
            "blank-name",
            "blank-code",
            "whitespace-only-name",
            "non-string-name",
        ],
    )
    def test_create_institute_with_invalid_body_returns_400(
        self, app_instance, monkeypatch, body
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post("/api/institutes", json=body, headers=headers)

        assert response.status_code == 400

    def test_create_semester_with_malformed_date_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/semesters",
                json={
                    "department_id": ids["department"],
                    "name": "Bad Dates",
                    "start_date": "not-a-date",
                    "end_date": "2026-06-01",
                },
                headers=headers,
            )

        assert response.status_code == 400

    def test_create_semester_missing_start_date_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, ids, _docs, headers = _login_admin_with_chain(monkeypatch, client)

            response = client.post(
                "/api/semesters",
                json={"department_id": ids["department"], "name": "No Start", "end_date": "2026-06-01"},
                headers=headers,
            )

        assert response.status_code == 400


# --- Role enforcement: reads --------------------------------------------------


class TestRoleEnforcementReads:
    @pytest.mark.parametrize("role", ["admin", "faculty"])
    def test_admin_and_faculty_can_read_all_five_list_endpoints(
        self, app_instance, monkeypatch, role
    ):
        docs, ids = build_chain_documents()
        user = make_user(email=f"{role}@college.test", password=FAKE_PASSWORD, role=role)
        fake_db = make_fake_academic_db(
            users=[user],
            institutes=[docs["institute"]],
            departments=[docs["department"]],
            semesters=[docs["semester"]],
            courses=[docs["course"]],
            classes=[docs["class"]],
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, f"{role}@college.test", FAKE_PASSWORD)

            responses = [
                client.get("/api/institutes"),
                client.get(f"/api/departments?institute_id={ids['institute']}"),
                client.get(f"/api/semesters?department_id={ids['department']}"),
                client.get(f"/api/courses?semester_id={ids['semester']}"),
                client.get(f"/api/classes?course_id={ids['course']}"),
            ]

        assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]

    def test_student_is_forbidden_on_all_five_list_endpoints(self, app_instance, monkeypatch):
        docs, ids = build_chain_documents()
        student = make_user(email="student@college.test", password=FAKE_PASSWORD, role="student")
        fake_db = make_fake_academic_db(
            users=[student],
            institutes=[docs["institute"]],
            departments=[docs["department"]],
            semesters=[docs["semester"]],
            courses=[docs["course"]],
            classes=[docs["class"]],
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "student@college.test", FAKE_PASSWORD)

            responses = [
                client.get("/api/institutes"),
                client.get(f"/api/departments?institute_id={ids['institute']}"),
                client.get(f"/api/semesters?department_id={ids['department']}"),
                client.get(f"/api/courses?semester_id={ids['semester']}"),
                client.get(f"/api/classes?course_id={ids['course']}"),
            ]

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_request_to_a_list_endpoint_returns_401(
        self, app_instance, monkeypatch
    ):
        fake_db = make_fake_academic_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            response = client.get("/api/institutes")

        assert response.status_code == 401


# --- Role enforcement: writes --------------------------------------------------


def _write_requests(client, ids, headers):
    return [
        client.post("/api/institutes", json={"name": "N", "code": "NEWC"}, headers=headers),
        client.put(f"/api/institutes/{ids['institute']}", json={"name": "N", "code": "NEWC"}, headers=headers),
        client.post(
            "/api/departments",
            json={"institute_id": ids["institute"], "name": "N", "code": "NEWC"},
            headers=headers,
        ),
        client.put(
            f"/api/departments/{ids['department']}", json={"name": "N", "code": "NEWC"}, headers=headers
        ),
        client.post(
            "/api/semesters",
            json={
                "department_id": ids["department"],
                "name": "N",
                "start_date": "2026-01-01",
                "end_date": "2026-06-01",
            },
            headers=headers,
        ),
        client.post(
            "/api/courses",
            json={"semester_id": ids["semester"], "name": "N", "code": "NEWC"},
            headers=headers,
        ),
        client.post(
            "/api/classes", json={"course_id": ids["course"], "name": "N"}, headers=headers
        ),
        client.delete(f"/api/classes/{ids['class']}", headers=headers),
    ]


class TestRoleEnforcementWrites:
    def test_faculty_is_forbidden_on_all_write_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            docs, ids = build_chain_documents()
            faculty = make_user(email="faculty@college.test", password=FAKE_PASSWORD, role="faculty")
            fake_db = make_fake_academic_db(
                users=[faculty],
                institutes=[docs["institute"]],
                departments=[docs["department"]],
                semesters=[docs["semester"]],
                courses=[docs["course"]],
                classes=[docs["class"]],
            )
            _patch_db(monkeypatch, fake_db)
            _login(client, "faculty@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            responses = _write_requests(client, ids, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_student_is_forbidden_on_all_write_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            docs, ids = build_chain_documents()
            student = make_user(email="student@college.test", password=FAKE_PASSWORD, role="student")
            fake_db = make_fake_academic_db(
                users=[student],
                institutes=[docs["institute"]],
                departments=[docs["department"]],
                semesters=[docs["semester"]],
                courses=[docs["course"]],
                classes=[docs["class"]],
            )
            _patch_db(monkeypatch, fake_db)
            _login(client, "student@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            responses = _write_requests(client, ids, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_request_to_a_write_endpoint_returns_401(
        self, app_instance, monkeypatch
    ):
        fake_db = make_fake_academic_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            response = client.post("/api/institutes", json={"name": "N", "code": "N1"})

        assert response.status_code == 401


# --- CSRF enforcement on mutating endpoints -----------------------------------


class TestCsrfEnforcementOnMutatingEndpoints:
    """Mirrors test_auth_routes.py's TestCsrfEnforcementOnMutatingEndpoints,
    using institute creation as the representative mutating endpoint.
    """

    def test_write_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            admin = make_user(email="csrf1@college.test", password=FAKE_PASSWORD, role="admin")
            fake_db = make_fake_academic_db(users=[admin])
            _patch_db(monkeypatch, fake_db)
            _login(client, "csrf1@college.test", FAKE_PASSWORD)

            response = client.post(
                "/api/institutes", json={"name": "No CSRF", "code": "NOCSRF"}
            )  # deliberately no X-CSRF-TOKEN header

        assert response.status_code == 401

    def test_write_with_an_incorrect_csrf_header_is_rejected(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            admin = make_user(email="csrf2@college.test", password=FAKE_PASSWORD, role="admin")
            fake_db = make_fake_academic_db(users=[admin])
            _patch_db(monkeypatch, fake_db)
            _login(client, "csrf2@college.test", FAKE_PASSWORD)

            response = client.post(
                "/api/institutes",
                json={"name": "Wrong CSRF", "code": "WRONGCSRF"},
                headers={"X-CSRF-TOKEN": "not-the-real-token"},
            )

        assert response.status_code == 401

    def test_write_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            admin = make_user(email="csrf3@college.test", password=FAKE_PASSWORD, role="admin")
            fake_db = make_fake_academic_db(users=[admin])
            _patch_db(monkeypatch, fake_db)
            _login(client, "csrf3@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            response = client.post(
                "/api/institutes", json={"name": "Right CSRF", "code": "RIGHTCSRF"}, headers=headers
            )

        assert response.status_code == 201
