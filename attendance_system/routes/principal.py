import bcrypt
from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from extensions import db
from models import (User, ClassModel, Subject, Faculty,
                     Student, Timetable, ClassLocation, Attendance)
from utils.decorators import role_required
from utils.report_utils import (
    resolve_period, apply_date_range, summarize,
    PERIOD_CHOICES, week_bucket, week_label, month_bucket, month_label,
)

principal_bp = Blueprint("principal", __name__)


@principal_bp.route("/dashboard")
@login_required
@role_required("principal")
def dashboard():
    stats = {
        "classes": ClassModel.query.count(),
        "subjects": Subject.query.count(),
        "faculty": Faculty.query.count(),
        "students": Student.query.count(),
        "attendance_today": Attendance.query.filter_by(date=date.today()).count(),
    }
    return render_template("principal/dashboard.html", stats=stats)


# ---------------- My Account (Principal's own email/password) ----------------
@principal_bp.route("/settings", methods=["GET", "POST"])
@login_required
@role_required("principal")
def settings():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_email = request.form.get("email", "").strip()
        new_password = request.form.get("new_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()

        if not bcrypt.checkpw(current_password.encode(), current_user.password_hash.encode()):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("principal.settings"))

        if new_password and new_password != confirm_password:
            flash("New password and confirm password do not match.", "error")
            return redirect(url_for("principal.settings"))

        if new_email and new_email != current_user.email:
            if User.query.filter(User.email == new_email, User.id != current_user.id).first():
                flash("That email is already in use by another account.", "error")
                return redirect(url_for("principal.settings"))
            current_user.email = new_email

        if new_password:
            current_user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

        db.session.commit()
        flash("Your account was updated successfully.", "success")
        return redirect(url_for("principal.settings"))

    return render_template("principal/settings.html")


# ---------------- Classes ----------------
@principal_bp.route("/classes", methods=["GET", "POST"])
@login_required
@role_required("principal")
def classes():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name and not ClassModel.query.filter_by(name=name).first():
            db.session.add(ClassModel(name=name))
            db.session.commit()
            flash("Class created.", "success")
        else:
            flash("Class name is required and must be unique.", "error")
        return redirect(url_for("principal.classes"))
    all_classes = ClassModel.query.order_by(ClassModel.name).all()
    return render_template("principal/classes.html", classes=all_classes)


@principal_bp.route("/classes/<int:class_id>/edit", methods=["POST"])
@login_required
@role_required("principal")
def edit_class(class_id):
    c = ClassModel.query.get_or_404(class_id)
    c.name = request.form.get("name", c.name).strip()
    db.session.commit()
    flash("Class updated.", "success")
    return redirect(url_for("principal.classes"))


@principal_bp.route("/classes/<int:class_id>/delete", methods=["POST"])
@login_required
@role_required("principal")
def delete_class(class_id):
    c = ClassModel.query.get_or_404(class_id)
    db.session.delete(c)
    db.session.commit()
    flash("Class deleted.", "success")
    return redirect(url_for("principal.classes"))


# ---------------- Subjects ----------------
@principal_bp.route("/subjects", methods=["GET", "POST"])
@login_required
@role_required("principal")
def subjects():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        class_id = request.form.get("class_id")
        if name and class_id:
            db.session.add(Subject(name=name, class_id=int(class_id)))
            db.session.commit()
            flash("Subject created.", "success")
        return redirect(url_for("principal.subjects"))
    all_subjects = Subject.query.all()
    all_classes = ClassModel.query.order_by(ClassModel.name).all()
    return render_template("principal/subjects.html", subjects=all_subjects, classes=all_classes)


@principal_bp.route("/subjects/<int:subject_id>/edit", methods=["POST"])
@login_required
@role_required("principal")
def edit_subject(subject_id):
    s = Subject.query.get_or_404(subject_id)
    s.name = request.form.get("name", s.name).strip()
    class_id = request.form.get("class_id")
    if class_id:
        s.class_id = int(class_id)
    db.session.commit()
    flash("Subject updated.", "success")
    return redirect(url_for("principal.subjects"))


@principal_bp.route("/subjects/<int:subject_id>/delete", methods=["POST"])
@login_required
@role_required("principal")
def delete_subject(subject_id):
    s = Subject.query.get_or_404(subject_id)
    db.session.delete(s)
    db.session.commit()
    flash("Subject deleted.", "success")
    return redirect(url_for("principal.subjects"))


# ---------------- Faculty ----------------
@principal_bp.route("/faculty", methods=["GET", "POST"])
@login_required
@role_required("principal")
def faculty():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not password:
            flash("A password is required when creating a faculty account.", "error")
            return redirect(url_for("principal.faculty"))
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return redirect(url_for("principal.faculty"))

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "error")
            return redirect(url_for("principal.faculty"))

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(username=username, email=email, password_hash=pw_hash, role="faculty")
        db.session.add(user)
        db.session.flush()
        db.session.add(Faculty(user_id=user.id, name=name))
        db.session.commit()
        flash(f"Faculty created. Login: {username} / {password}", "success")
        return redirect(url_for("principal.faculty"))

    all_faculty = Faculty.query.all()

    # Derive each faculty's distinct (subject, class) combos directly from the timetable
    # -- assigning faculty to a subject/class now happens via the Timetable page itself.
    faculty_subjects = {}
    for f in all_faculty:
        seen = {}
        for e in f.timetable_entries:
            key = (e.subject_id, e.class_id)
            if key not in seen:
                seen[key] = f"{e.subject.name} ({e.class_.name})"
        faculty_subjects[f.id] = list(seen.values())

    return render_template("principal/faculty.html", faculty_list=all_faculty, faculty_subjects=faculty_subjects)


@principal_bp.route("/faculty/<int:faculty_id>/edit", methods=["POST"])
@login_required
@role_required("principal")
def edit_faculty(faculty_id):
    f = Faculty.query.get_or_404(faculty_id)
    user = User.query.get(f.user_id)

    f.name = request.form.get("name", f.name).strip()

    new_username = request.form.get("username", "").strip()
    new_email = request.form.get("email", "").strip()
    new_password = request.form.get("password", "").strip()

    if user:
        if new_username and new_username != user.username:
            if User.query.filter(User.username == new_username, User.id != user.id).first():
                flash("That username is already taken.", "error")
                return redirect(url_for("principal.faculty"))
            user.username = new_username

        if new_email and new_email != user.email:
            if User.query.filter(User.email == new_email, User.id != user.id).first():
                flash("That email is already in use.", "error")
                return redirect(url_for("principal.faculty"))
            user.email = new_email

        if new_password:
            if len(new_password) < 6:
                flash("Password must be at least 6 characters.", "error")
                return redirect(url_for("principal.faculty"))
            user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    db.session.commit()
    flash("Faculty updated.", "success")
    return redirect(url_for("principal.faculty"))


@principal_bp.route("/faculty/<int:faculty_id>/delete", methods=["POST"])
@login_required
@role_required("principal")
def delete_faculty(faculty_id):
    f = Faculty.query.get_or_404(faculty_id)
    user = User.query.get(f.user_id)
    db.session.delete(f)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash("Faculty deleted.", "success")
    return redirect(url_for("principal.faculty"))


# ---------------- Students ----------------
@principal_bp.route("/students", methods=["GET", "POST"])
@login_required
@role_required("principal")
def students():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        class_id = request.form.get("class_id")
        password = request.form.get("password", "").strip() or "student123"

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("Username or email already exists.", "error")
            return redirect(url_for("principal.students"))

        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        user = User(username=username, email=email, password_hash=pw_hash, role="student")
        db.session.add(user)
        db.session.flush()
        db.session.add(Student(user_id=user.id, name=name, class_id=int(class_id)))
        db.session.commit()
        flash(f"Student created. Login: {username} / {password}", "success")
        return redirect(url_for("principal.students"))

    all_students = Student.query.all()
    all_classes = ClassModel.query.order_by(ClassModel.name).all()
    return render_template("principal/students.html", students=all_students, classes=all_classes)


@principal_bp.route("/students/<int:student_id>/edit", methods=["POST"])
@login_required
@role_required("principal")
def edit_student(student_id):
    s = Student.query.get_or_404(student_id)
    user = User.query.get(s.user_id)

    s.name = request.form.get("name", s.name).strip()
    class_id = request.form.get("class_id")
    if class_id:
        s.class_id = int(class_id)

    new_username = request.form.get("username", "").strip()
    new_email = request.form.get("email", "").strip()
    new_password = request.form.get("password", "").strip()

    if user:
        if new_username and new_username != user.username:
            if User.query.filter(User.username == new_username, User.id != user.id).first():
                flash("That username is already taken.", "error")
                return redirect(url_for("principal.students"))
            user.username = new_username

        if new_email and new_email != user.email:
            if User.query.filter(User.email == new_email, User.id != user.id).first():
                flash("That email is already in use.", "error")
                return redirect(url_for("principal.students"))
            user.email = new_email

        if new_password:
            user.password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    db.session.commit()
    flash("Student updated.", "success")
    return redirect(url_for("principal.students"))


@principal_bp.route("/students/<int:student_id>/delete", methods=["POST"])
@login_required
@role_required("principal")
def delete_student(student_id):
    s = Student.query.get_or_404(student_id)
    user = User.query.get(s.user_id)
    db.session.delete(s)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash("Student deleted.", "success")
    return redirect(url_for("principal.students"))


@principal_bp.route("/students/<int:student_id>/remove_face", methods=["POST"])
@login_required
@role_required("principal")
def remove_student_face(student_id):
    import shutil, os
    from flask import current_app
    s = Student.query.get_or_404(student_id)
    student_dir = os.path.join(current_app.config["FACES_DIR"], str(s.id))
    if os.path.isdir(student_dir):
        shutil.rmtree(student_dir)
    for fi in list(s.face_images):
        db.session.delete(fi)
    s.face_registered = False
    db.session.commit()
    flash("Face data removed for student.", "success")
    return redirect(url_for("principal.students"))


# ---------------- Timetable ----------------
@principal_bp.route("/timetable", methods=["GET", "POST"])
@login_required
@role_required("principal")
def timetable():
    if request.method == "POST":
        entry = Timetable(
            class_id=int(request.form["class_id"]),
            subject_id=int(request.form["subject_id"]),
            faculty_id=int(request.form["faculty_id"]),
            day=request.form["day"],
            start_time=datetime.strptime(request.form["start_time"], "%H:%M").time(),
            end_time=datetime.strptime(request.form["end_time"], "%H:%M").time(),
        )
        db.session.add(entry)
        db.session.commit()
        flash("Timetable entry added.", "success")
        return redirect(url_for("principal.timetable"))

    entries = Timetable.query.all()
    return render_template(
        "principal/timetable.html",
        entries=entries,
        classes=ClassModel.query.all(),
        subjects=Subject.query.all(),
        faculty_list=Faculty.query.all(),
    )


@principal_bp.route("/timetable/<int:entry_id>/delete", methods=["POST"])
@login_required
@role_required("principal")
def delete_timetable(entry_id):
    e = Timetable.query.get_or_404(entry_id)
    db.session.delete(e)
    db.session.commit()
    flash("Timetable entry deleted.", "success")
    return redirect(url_for("principal.timetable"))


# ---------------- Geofence / Locations ----------------
ALLOWED_RADII = [100, 200, 300, 400, 500]  # meters -- hard cap at 500


@principal_bp.route("/locations", methods=["GET", "POST"])
@login_required
@role_required("principal")
def locations():
    if request.method == "POST":
        class_id = int(request.form["class_id"])
        lat = float(request.form["latitude"])
        lng = float(request.form["longitude"])
        radius = int(request.form.get("radius_meters") or 100)

        if radius not in ALLOWED_RADII:
            flash(f"Radius must be one of {', '.join(map(str, ALLOWED_RADII))} meters.", "error")
            return redirect(url_for("principal.locations"))

        loc = ClassLocation.query.filter_by(class_id=class_id).first()
        if loc:
            loc.latitude, loc.longitude, loc.radius_meters = lat, lng, radius
        else:
            db.session.add(ClassLocation(class_id=class_id, latitude=lat, longitude=lng, radius_meters=radius))
        db.session.commit()
        flash("Geofence location saved.", "success")
        return redirect(url_for("principal.locations"))

    return render_template(
        "principal/locations.html",
        classes=ClassModel.query.all(),
        locations={l.class_id: l for l in ClassLocation.query.all()},
        allowed_radii=ALLOWED_RADII,
    )


# ---------------- Attendance / Reports ----------------
@principal_bp.route("/attendance")
@login_required
@role_required("principal")
def attendance_report():
    class_id = request.args.get("class_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    student_id = request.args.get("student_id", type=int)
    period = request.args.get("period", "month")
    group_by = request.args.get("group_by", "student")
    custom_start = request.args.get("start_date")
    custom_end = request.args.get("end_date")

    start, end = resolve_period(period, custom_start, custom_end)

    query = Attendance.query.join(Student)
    if class_id:
        query = query.filter(Student.class_id == class_id)
    if subject_id:
        query = query.filter(Attendance.subject_id == subject_id)
    if student_id:
        query = query.filter(Attendance.student_id == student_id)
    query = apply_date_range(query, Attendance.date, start, end)

    records = query.order_by(Attendance.date.desc(), Attendance.marked_at.desc()).all()

    group_funcs = {
        "student": (lambda r: r.student_id, lambda k: Student.query.get(k).name if Student.query.get(k) else "Unknown"),
        "subject": (lambda r: r.subject_id, lambda k: Subject.query.get(k).name if Subject.query.get(k) else "Unknown"),
        "class": (lambda r: r.student.class_id, lambda k: ClassModel.query.get(k).name if ClassModel.query.get(k) else "Unknown"),
        "week": (week_bucket_of_record, week_label),
        "month": (month_bucket_of_record, month_label),
    }
    key_func, label_func = group_funcs.get(group_by, group_funcs["student"])
    summary_rows = summarize(records, key_func, label_func)

    # cap the detailed log for page performance; the summary above already covers the full filtered set
    detail_records = records[:500]

    return render_template(
        "principal/attendance_report.html",
        records=detail_records,
        summary_rows=summary_rows,
        classes=ClassModel.query.order_by(ClassModel.name).all(),
        subjects=Subject.query.order_by(Subject.name).all(),
        students=Student.query.order_by(Student.name).all(),
        selected_class=class_id,
        selected_subject=subject_id,
        selected_student=student_id,
        selected_period=period,
        selected_group_by=group_by,
        start_date=custom_start or "",
        end_date=custom_end or "",
        period_choices=PERIOD_CHOICES,
        range_label=f"{start} to {end}" if start and end else "All Time",
    )


def week_bucket_of_record(r):
    return week_bucket(r.date)


def month_bucket_of_record(r):
    return month_bucket(r.date)
