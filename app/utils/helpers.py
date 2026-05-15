"""Validation + activity logging helpers."""
import re
from datetime import datetime
from flask import request
from .. import mongo

PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{};:'\",.<>/?\\|`~]).{8,}$"
)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def is_valid_email(s):
    return bool(s and EMAIL_RE.match(s.strip()))


def is_valid_phone(s):
    return bool(s and PHONE_RE.match(s.strip()))


def is_strong_password(s):
    """8+ chars, upper, lower, digit, special."""
    return bool(s and PASSWORD_RE.match(s))


def password_rules_text():
    return "At least 8 characters with uppercase, lowercase, number and special character."


def log_activity(user_id, action, meta=None):
    try:
        mongo.db.activity_logs.insert_one({
            "user_id": str(user_id) if user_id else None,
            "action": action,
            "meta": meta or {},
            "ip": request.remote_addr if request else None,
            "user_agent": request.user_agent.string if request else None,
            "created_at": datetime.utcnow(),
        })
    except Exception:
        pass
