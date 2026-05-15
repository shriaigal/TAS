"""Authentication blueprint: register, login, logout, OTP, forgot/reset."""
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from flask_login import login_user, logout_user, login_required, current_user

from .. import limiter, mongo
from ..models.user import User
from ..services.otp_service import store_otp, verify_otp
from ..services.email_service import send_otp, send_welcome, send_reset
from ..utils.helpers import (
    is_valid_email, is_valid_phone, is_strong_password,
    password_rules_text, log_activity,
)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("traffic.home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not all([name, email, phone, password, confirm]):
            flash("All fields are required.", "danger")
        elif not is_valid_email(email):
            flash("Invalid email address.", "danger")
        elif not is_valid_phone(phone):
            flash("Invalid phone number (7-15 digits).", "danger")
        elif password != confirm:
            flash("Passwords do not match.", "danger")
        elif not is_strong_password(password):
            flash(password_rules_text(), "danger")
        elif User.email_exists(email):
            flash("Email is already registered.", "danger")
        elif User.phone_exists(phone):
            flash("Phone number is already registered.", "danger")
        else:
            session["reg_data"] = {"name": name, "email": email, "phone": phone, "password": password}
            otp = store_otp(email, "registration")
            ok, msg = send_otp(email, name, otp, "Verification")
            if ok:
                flash(f"OTP sent to {email}.", "success")
            else:
                current_app.logger.warning(f"OTP email failed; dev OTP: {otp}")
                flash(f"Email send failed. Dev OTP: {otp}", "warning")
            return redirect(url_for("auth.verify_otp_route", purpose="registration"))

    return render_template("auth/register.html", rules=password_rules_text())


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("traffic.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.get_by_email(email)
        if user and user.check_password(password):
            if not user.is_active:
                flash("Your account has been suspended. Contact support.", "danger")
                return render_template("auth/login.html")
            login_user(user, remember=remember)
            user.update(last_login=datetime.utcnow())
            log_activity(user.id, "login")
            flash(f"Welcome back, {user.name}!", "success")
            if user.is_admin:
                return redirect(url_for("admin.dashboard"))
            return redirect(url_for("traffic.home"))
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/verify-otp/<purpose>", methods=["GET", "POST"])
def verify_otp_route(purpose):
    if purpose == "registration":
        reg = session.get("reg_data")
        if not reg:
            flash("Session expired. Please register again.", "danger")
            return redirect(url_for("auth.register"))
        email = reg["email"]
    elif purpose == "forgot_password":
        email = session.get("reset_email")
        if not email:
            flash("Session expired. Try again.", "danger")
            return redirect(url_for("auth.forgot_password"))
    else:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        entered = request.form.get("otp", "")
        ok, msg = verify_otp(email, purpose, entered)
        if not ok:
            flash(msg, "danger")
            return render_template("auth/verify_otp.html", purpose=purpose, email=email)

        if purpose == "registration":
            reg = session.pop("reg_data")
            user = User.create(reg["name"], reg["email"], reg["phone"], reg["password"], verified=True)
            send_welcome(user.email, user.name)
            log_activity(user.id, "register")
            flash("Registration complete! Please log in.", "success")
            return redirect(url_for("auth.login"))

        if purpose == "forgot_password":
            session["otp_verified"] = True
            return redirect(url_for("auth.reset_password"))

    return render_template("auth/verify_otp.html", purpose=purpose, email=email)


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.get_by_email(email)
        if not user:
            flash("No account with this email.", "danger")
        else:
            session["reset_email"] = email
            otp = store_otp(email, "forgot_password")
            ok, _ = send_reset(email, user.name, otp)
            if ok:
                flash(f"Reset OTP sent to {email}.", "success")
            else:
                flash(f"Email failed. Dev OTP: {otp}", "warning")
            return redirect(url_for("auth.verify_otp_route", purpose="forgot_password"))
    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if not session.get("otp_verified") or not session.get("reset_email"):
        flash("Please verify OTP first.", "warning")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if new != confirm:
            flash("Passwords do not match.", "danger")
        elif not is_strong_password(new):
            flash(password_rules_text(), "danger")
        else:
            email = session.pop("reset_email")
            session.pop("otp_verified", None)
            User.update_password(email, new)
            flash("Password reset successfully. Please log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", rules=password_rules_text())


@auth_bp.route("/logout")
@login_required
def logout():
    log_activity(current_user.id, "logout")
    logout_user()
    flash("Logged out successfully.", "info")
    return redirect(url_for("main.index"))


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if not name:
            flash("Name is required.", "danger")
        elif phone and not is_valid_phone(phone):
            flash("Invalid phone number.", "danger")
        else:
            current_user.update(name=name, phone=phone or None)
            log_activity(current_user.id, "profile_update")
            flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")
