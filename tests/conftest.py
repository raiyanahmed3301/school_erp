import os, re, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["SCHOOL_ERP_ENV"] = "development"

from schoolerp import create_app
from schoolerp.db import execute, init_db
from schoolerp.security import hash_password

PW = "Str0ng-Passw0rd!"


@pytest.fixture()
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "SECRET_KEY": "test"})
    with app.app_context():
        init_db()
        for u, role in (("admin", "admin"), ("t1", "teacher"), ("t2", "teacher")):
            execute("INSERT INTO users (username, full_name, role, password_hash, must_change_password) VALUES (?,?,?,?,0)",
                    (u, u.upper() + " Name", role, hash_password(PW)))
        execute("INSERT INTO students (id, student_code, full_name, gender, status, guardian_phone) VALUES (1,'QLC-0001','Aisha','female','active','+911234')")
        execute("INSERT INTO students (id, student_code, full_name, gender, status) VALUES (2,'QLC-0002','Yusuf','male','active')")
        execute("INSERT INTO classes (id, name, course_id, teacher_id, weekdays, start_time, duration_min) VALUES (1,'C1',1,2,'0,1,2,3,4,5,6','17:00',30)")  # t1 (id 2)
        execute("INSERT INTO classes (id, name, course_id, teacher_id, weekdays, start_time, duration_min) VALUES (2,'C2',1,3,'0,1,2,3,4,5,6','18:00',30)")  # t2 (id 3)
        execute("INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (1,1,'2020-01-01')")
        execute("INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (2,2,'2020-01-01')")
    return app


def token(client, url="/login"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def login(client, user, pw=PW):
    return client.post("/login", data={"username": user, "password": pw, "_csrf": token(client)})


@pytest.fixture()
def admin(app):
    c = app.test_client(); login(c, "admin"); return c


@pytest.fixture()
def t1(app):
    c = app.test_client(); login(c, "t1"); return c
