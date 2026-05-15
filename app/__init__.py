"""Flask application factory."""
from flask import Flask, render_template
from flask_pymongo import PyMongo
from flask_mail import Mail
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from .config import Config

mongo = PyMongo()
mail = Mail()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per minute"])


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_object(config_class)

    # Extensions
    mongo.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "warning"

    from .models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.get_by_id(user_id)

    # Blueprints
    from .auth.routes import auth_bp
    from .admin.routes import admin_bp
    from .main.routes import main_bp
    from .traffic.routes import traffic_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(main_bp)
    app.register_blueprint(traffic_bp)

    # Bootstrap default admin + indexes
    with app.app_context():
        _bootstrap(app)

    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500

    # Theme context
    @app.context_processor
    def inject_globals():
        return {"APP_NAME": app.config["APP_NAME"], "APP_SHORT": app.config["APP_SHORT"]}

    return app


def _bootstrap(app):
    """Create indexes and default admin if missing."""
    from werkzeug.security import generate_password_hash
    from datetime import datetime

    db = mongo.db
    if db is None:
        return
    try:
        db.users.create_index("email", unique=True)
        db.users.create_index("phone", unique=True, sparse=True)
        db.otps.create_index("email")
        db.otps.create_index("expires_at")
        db.activity_logs.create_index([("user_id", 1), ("created_at", -1)])
        db.feedback.create_index("created_at")
        db.reports.create_index("created_at")
    except Exception as e:
        app.logger.warning(f"Index creation skipped: {e}")

    if not db.users.find_one({"role": "admin"}):
        try:
            db.users.insert_one({
                "name": app.config["ADMIN_NAME"],
                "email": app.config["ADMIN_EMAIL"].lower(),
                "phone": None,
                "password": generate_password_hash(app.config["ADMIN_PASSWORD"]),
                "role": "admin",
                "is_verified": True,
                "is_active": True,
                "created_at": datetime.utcnow(),
                "last_login": None,
            })
            app.logger.info(f"Default admin created: {app.config['ADMIN_EMAIL']}")
        except Exception as e:
            app.logger.warning(f"Admin bootstrap skipped: {e}")
