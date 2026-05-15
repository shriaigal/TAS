"""User model wrapping MongoDB documents for Flask-Login."""
from datetime import datetime
from bson import ObjectId
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .. import mongo


class User(UserMixin):
    def __init__(self, doc):
        self.doc = doc or {}

    # Flask-Login required
    def get_id(self):
        return str(self.doc.get("_id"))

    @property
    def is_active(self):
        return bool(self.doc.get("is_active", True))

    # Convenience attrs
    @property
    def id(self):
        return str(self.doc.get("_id"))

    @property
    def name(self):
        return self.doc.get("name", "")

    @property
    def email(self):
        return self.doc.get("email", "")

    @property
    def phone(self):
        return self.doc.get("phone")

    @property
    def role(self):
        return self.doc.get("role", "user")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_verified(self):
        return bool(self.doc.get("is_verified", False))

    # ---- DAO ----
    @staticmethod
    def get_by_id(uid):
        try:
            doc = mongo.db.users.find_one({"_id": ObjectId(uid)})
        except Exception:
            return None
        return User(doc) if doc else None

    @staticmethod
    def get_by_email(email):
        if not email:
            return None
        doc = mongo.db.users.find_one({"email": email.strip().lower()})
        return User(doc) if doc else None

    @staticmethod
    def email_exists(email):
        return mongo.db.users.find_one({"email": email.strip().lower()}) is not None

    @staticmethod
    def phone_exists(phone):
        if not phone:
            return False
        return mongo.db.users.find_one({"phone": phone.strip()}) is not None

    @staticmethod
    def create(name, email, phone, password, role="user", verified=True):
        doc = {
            "name": name.strip(),
            "email": email.strip().lower(),
            "phone": phone.strip() if phone else None,
            "password": generate_password_hash(password),
            "role": role,
            "is_verified": verified,
            "is_active": True,
            "created_at": datetime.utcnow(),
            "last_login": None,
        }
        res = mongo.db.users.insert_one(doc)
        doc["_id"] = res.inserted_id
        return User(doc)

    def check_password(self, password):
        return check_password_hash(self.doc.get("password", ""), password)

    @staticmethod
    def update_password(email, new_password):
        mongo.db.users.update_one(
            {"email": email.strip().lower()},
            {"$set": {"password": generate_password_hash(new_password)}},
        )

    def update(self, **fields):
        if not fields:
            return
        mongo.db.users.update_one({"_id": self.doc["_id"]}, {"$set": fields})
        self.doc.update(fields)

    @staticmethod
    def list_all(search=None, page=1, per_page=20):
        q = {}
        if search:
            q = {"$or": [
                {"name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}},
            ]}
        cursor = mongo.db.users.find(q).sort("created_at", -1)
        total = mongo.db.users.count_documents(q)
        items = list(cursor.skip((page - 1) * per_page).limit(per_page))
        return items, total

    @staticmethod
    def delete_by_id(uid):
        try:
            mongo.db.users.delete_one({"_id": ObjectId(uid)})
            return True
        except Exception:
            return False

    @staticmethod
    def set_active(uid, active):
        try:
            mongo.db.users.update_one(
                {"_id": ObjectId(uid)},
                {"$set": {"is_active": bool(active)}},
            )
            return True
        except Exception:
            return False
