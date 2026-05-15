"""Admin blueprint."""
import csv
import io
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, Response
from flask_login import login_required, current_user

from .. import mongo
from ..models.user import User
from ..utils.helpers import log_activity

admin_bp = Blueprint("admin", __name__)


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


@admin_bp.route("/")
@login_required
@admin_required
def dashboard():
    db = mongo.db
    total_users = db.users.count_documents({"role": "user"})
    active_users = db.users.count_documents({"role": "user", "is_active": True})
    suspended = db.users.count_documents({"role": "user", "is_active": False})
    total_feedback = db.feedback.count_documents({})
    total_reports = db.reports.count_documents({})

    since = datetime.utcnow() - timedelta(days=7)
    new_week = db.users.count_documents({"created_at": {"$gte": since}})
    logins_week = db.activity_logs.count_documents({"action": "login", "created_at": {"$gte": since}})

    recent_users = list(db.users.find({"role": "user"}).sort("created_at", -1).limit(8))
    recent_logs = list(db.activity_logs.find().sort("created_at", -1).limit(10))

    # 7-day registration chart data
    chart = []
    for i in range(6, -1, -1):
        day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        c = db.users.count_documents({"created_at": {"$gte": day_start, "$lt": day_end}})
        chart.append({"date": day_start.strftime("%b %d"), "count": c})

    return render_template("admin/dashboard.html",
                           stats={
                               "total_users": total_users,
                               "active_users": active_users,
                               "suspended": suspended,
                               "feedback": total_feedback,
                               "reports": total_reports,
                               "new_week": new_week,
                               "logins_week": logins_week,
                           },
                           recent_users=recent_users,
                           recent_logs=recent_logs,
                           chart=chart)


@admin_bp.route("/users")
@login_required
@admin_required
def users():
    search = request.args.get("q", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    per_page = 15
    items, total = User.list_all(search=search, page=page, per_page=per_page)
    pages = (total + per_page - 1) // per_page
    return render_template("admin/users.html",
                           users=items, total=total, page=page, pages=pages, search=search)


@admin_bp.route("/users/<uid>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_user(uid):
    u = User.get_by_id(uid)
    if not u or u.is_admin:
        flash("Cannot modify this user.", "danger")
    else:
        new_state = not u.is_active
        User.set_active(uid, new_state)
        log_activity(current_user.id, "admin_toggle_user", {"target": uid, "active": new_state})
        flash(f"User {'activated' if new_state else 'suspended'}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<uid>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(uid):
    u = User.get_by_id(uid)
    if not u or u.is_admin:
        flash("Cannot delete this user.", "danger")
    else:
        User.delete_by_id(uid)
        log_activity(current_user.id, "admin_delete_user", {"target": uid})
        flash("User deleted.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/export")
@login_required
@admin_required
def export_users():
    items, _ = User.list_all(per_page=10_000)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "name", "email", "phone", "role", "is_active", "is_verified", "created_at"])
    for u in items:
        w.writerow([str(u.get("_id")), u.get("name"), u.get("email"), u.get("phone"),
                    u.get("role"), u.get("is_active"), u.get("is_verified"),
                    u.get("created_at").isoformat() if u.get("created_at") else ""])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=users.csv"},
    )


@admin_bp.route("/feedback")
@login_required
@admin_required
def feedback():
    items = list(mongo.db.feedback.find().sort("created_at", -1).limit(200))
    return render_template("admin/feedback.html", items=items)


@admin_bp.route("/reports")
@login_required
@admin_required
def reports():
    items = list(mongo.db.reports.find().sort("created_at", -1).limit(200))
    return render_template("admin/reports.html", items=items)


@admin_bp.route("/logs")
@login_required
@admin_required
def logs():
    items = list(mongo.db.activity_logs.find().sort("created_at", -1).limit(300))
    return render_template("admin/logs.html", items=items)
