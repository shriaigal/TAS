"""Email sending service with rendered HTML templates."""
from flask import render_template, current_app
from flask_mail import Message
from .. import mail


def _send(subject, recipients, html, text=None):
    try:
        msg = Message(subject=subject, recipients=recipients, html=html, body=text or "")
        mail.send(msg)
        return True, "ok"
    except Exception as e:
        current_app.logger.error(f"Mail error: {e}")
        return False, str(e)


def send_otp(email, name, otp, purpose="Verification"):
    html = render_template("emails/otp.html", name=name, otp=otp, purpose=purpose,
                           app_name=current_app.config["APP_NAME"])
    return _send(f"{current_app.config['APP_SHORT']} {purpose} Code", [email], html)


def send_welcome(email, name):
    html = render_template("emails/welcome.html", name=name,
                           app_name=current_app.config["APP_NAME"])
    return _send(f"Welcome to {current_app.config['APP_NAME']}", [email], html)


def send_reset(email, name, otp):
    html = render_template("emails/reset.html", name=name, otp=otp,
                           app_name=current_app.config["APP_NAME"])
    return _send(f"{current_app.config['APP_SHORT']} Password Reset", [email], html)
