"""DEV-ONLY sample data. Passwords are random and printed once."""
from datetime import timedelta
from .db import execute, get_db, query
from .security import hash_password, temp_password
from .utils import school_today


def seed():
    today = school_today()
    teachers = []
    for uname, name, gender in (("niveen", "Niveen Abd Allah Ahmed", "female"), ("abeer", "Abeer Sabry", "female"), ("maha", "Maha Elmansy", "female")):
        pw = temp_password()
        tid = execute("INSERT INTO users (username, full_name, role, gender, password_hash) VALUES (?,?,'teacher',?,?)",
                      (uname, name, gender, hash_password(pw)))
        teachers.append(tid)
        print(f"teacher {uname}: {pw}")
    students = []
    for i, (n, g, c) in enumerate((("Aisha Khan", "female", "UK"), ("Yusuf Ahmed", "male", "USA"), ("Maryam Ali", "female", "Canada"),
                                  ("Ibrahim Siddiqui", "male", "India"), ("Fatima Noor", "female", "UAE"))):
        sid = execute("""INSERT INTO students (full_name, gender, country, status, joined_on, monthly_fee_cents, currency, guardian_name)
                         VALUES (?,?,?,'active',?,?,'USD','Parent of ' || ?)""", (n, g, c, (today - timedelta(days=60)).isoformat(), 2500, n))
        execute("UPDATE students SET student_code=? WHERE id=?", (f"QLC-{sid:04d}", sid))
        students.append(sid)
    c1 = query("SELECT id FROM courses WHERE name LIKE '%Hifz%'", one=True)["id"]
    c2 = query("SELECT id FROM courses WHERE name LIKE '%Tajweed%'", one=True)["id"]
    cl1 = execute("INSERT INTO classes (name, course_id, teacher_id, weekdays, start_time, duration_min) VALUES ('Hifz Group A', ?, ?, '0,1,2,3,4', '17:00', 45)", (c1, teachers[0]))
    cl2 = execute("INSERT INTO classes (name, course_id, teacher_id, weekdays, start_time, duration_min) VALUES ('Tajweed Group B', ?, ?, '0,2,4', '19:00', 30)", (c2, teachers[1]))
    for sid in students[:3]:
        execute("INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (?,?,?)", (cl1, sid, (today - timedelta(days=30)).isoformat()))
    for sid in students[3:]:
        execute("INSERT INTO enrollments (class_id, student_id, enrolled_on) VALUES (?,?,?)", (cl2, sid, (today - timedelta(days=30)).isoformat()))
    print("Demo data created. Create an admin with: flask --app schoolerp create-admin")
