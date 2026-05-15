"""OTP utilities."""
import random
import string
from datetime import datetime, timedelta
from flask import current_app
from .. import mongo


def generate_otp(length=6):
    return "".join(random.choices(string.digits, k=length))


def store_otp(email, purpose):
    otp = generate_otp()
    expires = datetime.utcnow() + timedelta(minutes=current_app.config["OTP_EXPIRY_MINUTES"])
    mongo.db.otps.insert_one({
        "email": email.strip().lower(),
        "otp": otp,
        "purpose": purpose,
        "expires_at": expires,
        "used": False,
        "created_at": datetime.utcnow(),
    })
    return otp


def verify_otp(email, purpose, entered):
    rec = mongo.db.otps.find_one(
        {"email": email.strip().lower(), "purpose": purpose, "used": False},
        sort=[("created_at", -1)],
    )
    if not rec:
        return False, "No OTP found. Please request a new one."
    if datetime.utcnow() > rec["expires_at"]:
        return False, "OTP has expired. Please request a new one."
    if rec["otp"] != entered.strip():
        return False, "Invalid OTP."
    mongo.db.otps.update_one({"_id": rec["_id"]}, {"$set": {"used": True}})
    return True, "OTP verified."
