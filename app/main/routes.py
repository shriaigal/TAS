"""Public/main pages: landing, contact, faq, about."""
from datetime import datetime
from pdb import main
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import current_user
from .. import mongo, limiter
from ..utils.helpers import is_valid_email
from ..services.email_service import _send

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    # if current_user.is_authenticated:
    #     return redirect(url_for("traffic.home"))
    return render_template("main/landing.html")


@main_bp.route("/about")
def about():
    return render_template("main/about.html")


@main_bp.route("/faq")
def faq():
    return render_template("main/faq.html")


@main_bp.route("/dev")
def dev():
    return render_template("main/dev.html")

@main_bp.route('/terms-and-conditions')
def terms_and_conditions():
    return render_template('main/terms_and_conditions.html')

@main_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def contact():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()
        if not all([name, email, message]):
            flash("All fields are required.", "danger")
        elif not is_valid_email(email):
            flash("Invalid email.", "danger")
        elif len(message) > 2000:
            flash("Message too long (max 2000 chars).", "danger")
        else:
            mongo.db.feedback.insert_one({
                "type": "contact",
                "name": name, "email": email, "message": message,
                "created_at": datetime.utcnow(),
            })
            try:
                _send(f"[Contact] {name}",
                      [current_app.config.get("ADMIN_EMAIL")],
                      f"<p><b>From:</b> {name} &lt;{email}&gt;</p><p>{message}</p>")
            except Exception:
                pass
            flash("Message sent! We'll get back to you soon.", "success")
            return redirect(url_for("main.index"))
    return render_template("main/contact.html")
