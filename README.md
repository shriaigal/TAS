# Traffic Analytics System (TAS)

AI-powered route optimization for Bangalore using **Flask + MongoDB + scikit-learn/XGBoost + Leaflet**.
This is the modernized version of the legacy SQLite-based TAS — original ML / Dijkstra / DFS / OSRM logic is preserved verbatim in `routing/graph.py` and `app/traffic/routes.py`.

## What's new
- **MongoDB** instead of SQLite (Flask-PyMongo).
- **Modular Blueprint architecture**: `auth`, `admin`, `main`, `traffic`, `services`, `utils`, `models`.
- **Full admin panel**: users CRUD, suspend/activate, search, pagination, CSV export, feedback, reports, activity logs, dashboard charts.
- **Modern responsive UI**: TailwindCSS, glassmorphism, gradients, smooth animations, mobile menu.
- **Dark / light theme** toggle persisted in `localStorage`.
- **Strong auth**: Flask-Login, CSRF (Flask-WTF), rate limiting (Flask-Limiter), password rules (8+, upper/lower/digit/special), OTP via email, forgot/reset, remember-me.
- **Activity logging** + **per-user route history**.
- HTML email templates for OTP / welcome / password reset.

## Quick start

### 1. Prerequisites
- Python 3.10+
- MongoDB running locally or a connection string (MongoDB Atlas works too)

### 2. Install
```bash
cd tas_app
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then edit values
```

### 3. Configure `.env`
```
SECRET_KEY=<long-random-string>
MONGO_URI=mongodb://localhost:27017/tas_db
MAIL_USERNAME=you@gmail.com
MAIL_PASSWORD=<gmail-app-password>
ADMIN_EMAIL=admin@tas.local
ADMIN_PASSWORD=Admin@12345
```
On Gmail, generate an **App Password** (2FA must be on).

### 4. Run
```bash
python run.py
# → http://127.0.0.1:5000
```

On first launch the app:
- creates MongoDB indexes
- bootstraps a default admin (using `ADMIN_EMAIL` / `ADMIN_PASSWORD`)

### 5. Default admin login
- Email: value of `ADMIN_EMAIL` (default `admin@tas.local`)
- Password: value of `ADMIN_PASSWORD` (default `Admin@12345`)
- Admin dashboard: `/admin`

## Project structure
```
tas_app/
├── app/
│   ├── __init__.py        # app factory, extensions, bootstrap
│   ├── config.py
│   ├── auth/              # register/login/OTP/forgot/reset/profile
│   ├── admin/             # dashboard, users, feedback, reports, logs
│   ├── main/              # landing, about, faq, contact
│   ├── traffic/           # /app, /get_locations, /get_routes, /history
│   ├── models/            # User (MongoDB)
│   ├── services/          # email, OTP
│   └── utils/             # validation, activity log
├── routing/graph.py       # ⚠️ preserved: Dijkstra + DFS + ML edge weights
├── data/bangalore_routes.csv
├── models/route_model.pkl
├── training/train_model.py
├── templates/             # base, auth/, admin/, main/, emails/, errors/
├── static/                # css/app.css, js/app.js, js/route_planner.js
├── requirements.txt
├── .env.example
└── run.py
```

## MongoDB collections
| Collection | Purpose |
|---|---|
| `users` | accounts (role: `user` or `admin`) |
| `otps` | OTP records (registration, forgot_password) |
| `activity_logs` | login, logout, profile updates, admin actions |
| `feedback` | contact-form submissions |
| `reports` | (reserved) |
| `saved_routes` | per-user route history |

## Preserved logic
The following files / code paths are **unchanged from the original**:
- `routing/graph.py` — `RouteGraph`, `predict_edge_time`, `build_weighted_graph`, `dijkstra`, `all_paths` (DFS), `traffic_level`
- ML model + dataset (`models/route_model.pkl`, `data/bangalore_routes.csv`)
- Path post-processing (`remove_loops`, `remove_repeated`, `smart_simplify_path`)
- OSRM real-road snapping (`get_full_route`)

Only auth, database, admin, UI/UX and structure were modernized.

## Security notes
- Passwords hashed with `werkzeug.security` (PBKDF2-SHA256).
- CSRF protection on all forms (Flask-WTF).
- Rate limits on `/login`, `/register`, `/forgot-password`, `/contact`.
- HTTP-only, SameSite=Lax session cookies; Secure flag enabled in production.
- `.env` is gitignored — never commit credentials.

## License
MCA project — © 2026 A S Shridatta Aigal
