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
    seed_lms()
    print("Demo data created. Create an admin with: flask --app schoolerp create-admin")


def seed_lms():
    """DEV ONLY: a sample course with lessons, quiz, assignment + a student portal login."""
    from .security import hash_password, temp_password
    teacher = query("SELECT id FROM users WHERE role='teacher' ORDER BY id LIMIT 1", one=True)["id"]
    cid = execute("INSERT INTO lms_courses (title, summary, level, status, owner_id) VALUES (?,?,?,?,?)",
                  ("Tajweed for Beginners", "Learn the basic rules of correct Quran recitation, step by step.", "beginner", "published", teacher))
    m1 = execute("INSERT INTO lms_modules (course_id, title, position) VALUES (?,?,1)", (cid, "Week 1 – Letters & Sounds"))
    m2 = execute("INSERT INTO lms_modules (course_id, title, position) VALUES (?,?,2)", (cid, "Week 2 – Noon Sakinah"))
    lessons = [(m1, "Introduction to Tajweed", "text", "Tajweed means to make better.\n\nIn this course we learn to recite the Quran as it was revealed.\n\nبسم الله الرحمن الرحيم", "", 8),
               (m1, "Makharij – where sounds come from", "video", "Watch the video, then repeat each letter aloud.", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", 15),
               (m2, "Rules of Noon Sakinah", "text", "Idhhar, Idghaam, Iqlab and Ikhfa.\n\nWe will study each rule with examples.", "", 12)]
    for i, (m, t, k, b, u, d) in enumerate(lessons, 1):
        execute("INSERT INTO lms_lessons (module_id, title, kind, body, url, duration_min, position) VALUES (?,?,?,?,?,?,?)", (m, t, k, b, u, d, i))
    z = execute("INSERT INTO lms_quizzes (course_id, title, pass_percent, max_attempts, is_published) VALUES (?,?,70,3,1)", (cid, "Week 1 Quiz"))
    for n, (q, good, bads) in enumerate((("What does Tajweed mean?", "To make better / to improve", ["To write", "To memorise"]),
                                         ("Where does the letter ب come from?", "The lips", ["The throat", "The tongue"])), 1):
        qid = execute("INSERT INTO lms_questions (quiz_id, text, position) VALUES (?,?,?)", (z, q, n))
        execute("INSERT INTO lms_options (question_id, text, is_correct, position) VALUES (?,?,1,1)", (qid, good))
        for j, b in enumerate(bads, 2):
            execute("INSERT INTO lms_options (question_id, text, is_correct, position) VALUES (?,?,0,?)", (qid, b, j))
    execute("INSERT INTO lms_assignments (course_id, title, instructions, due_date, max_points) VALUES (?,?,?,?,10)",
            (cid, "Record yourself reciting Surah Al-Fatiha", "Write which letters were hardest and why.", (school_today() + timedelta(days=5)).isoformat()))
    execute("INSERT INTO lms_announcements (course_id, author_id, title, body) VALUES (?,?,?,?)",
            (cid, teacher, "Welcome!", "Assalamu alaikum! Please finish Week 1 by Friday."))
    pw = temp_password()
    for sid in (1, 2):
        s = query("SELECT * FROM students WHERE id=?", (sid,), one=True)
        uid = execute("INSERT INTO users (username, full_name, role, password_hash, must_change_password) VALUES (?,?,'student',?,0)",
                      (s["student_code"].lower(), s["full_name"], hash_password(pw)))
        execute("UPDATE students SET user_id=? WHERE id=?", (uid, sid))
        execute("INSERT INTO lms_enrollments (course_id, student_id, enrolled_on) VALUES (?,?,?)", (cid, sid, school_today().isoformat()))
    print(f"LMS demo created. Student logins: qlc-0001 / qlc-0002  password: {pw}")
