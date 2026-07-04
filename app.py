import os
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "change-me")
API_KEY = os.environ.get("API_KEY", "")
DB_PATH = os.environ.get("DATABASE_PATH", "subscriptions.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                uid TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT DEFAULT ''
            )
            """
        )
        conn.commit()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped


def check_api_key():
    if not API_KEY:
        return True
    return request.headers.get("X-API-Key") == API_KEY


@app.before_request
def ensure_db():
    init_db()


@app.get("/")
def index():
    return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        user = request.form.get("username", "")
        password = request.form.get("password", "")
        if user == ADMIN_USER and password == ADMIN_PASS:
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials", "error")
    return render_template("login.html")


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard", methods=["GET", "POST"])
@admin_required
def admin_dashboard():
    if request.method == "POST":
        uid = request.form.get("uid", "").strip()
        days = int(request.form.get("days", "0") or "0")
        note = request.form.get("note", "").strip()

        if not uid.isdigit():
            flash("UID must be numeric", "error")
        elif days <= 0:
            flash("Days must be greater than 0", "error")
        else:
            now = datetime.utcnow()
            expires = now + timedelta(days=days)
            with get_db() as conn:
                existing = conn.execute(
                    "SELECT expires_at FROM subscriptions WHERE uid = ?",
                    (uid,),
                ).fetchone()
                if existing:
                    current = parse_iso(existing["expires_at"])
                    base = current if current > now else now
                    expires = base + timedelta(days=days)

                conn.execute(
                    """
                    INSERT INTO subscriptions (uid, expires_at, created_at, note)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(uid) DO UPDATE SET
                        expires_at = excluded.expires_at,
                        note = excluded.note
                    """,
                    (
                        uid,
                        expires.isoformat(),
                        now.isoformat(),
                        note,
                    ),
                )
                conn.commit()
            flash(f"Subscription saved for UID {uid}", "ok")

    with get_db() as conn:
        rows = conn.execute(
            "SELECT uid, expires_at, created_at, note FROM subscriptions ORDER BY expires_at DESC"
        ).fetchall()

    now = datetime.utcnow()
    subscriptions = []
    for row in rows:
        expires = parse_iso(row["expires_at"])
        subscriptions.append(
            {
                "uid": row["uid"],
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "note": row["note"] or "",
                "active": expires > now,
                "days_left": max(0, (expires - now).days),
            }
        )

    return render_template("dashboard.html", subscriptions=subscriptions)


@app.post("/admin/delete/<uid>")
@admin_required
def admin_delete(uid):
    with get_db() as conn:
        conn.execute("DELETE FROM subscriptions WHERE uid = ?", (uid,))
        conn.commit()
    flash(f"Removed UID {uid}", "ok")
    return redirect(url_for("admin_dashboard"))


@app.post("/api/verify")
def api_verify():
    if not check_api_key():
        return jsonify({"valid": False, "message": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    uid = str(payload.get("uid", "")).strip()

    if not uid.isdigit():
        return jsonify({"valid": False, "message": "Invalid UID format"}), 400

    with get_db() as conn:
        row = conn.execute(
            "SELECT uid, expires_at, note FROM subscriptions WHERE uid = ?",
            (uid,),
        ).fetchone()

    if not row:
        return jsonify(
            {
                "valid": False,
                "uid": uid,
                "message": "UID not found",
            }
        )

    expires = parse_iso(row["expires_at"])
    now = datetime.utcnow()
    if expires <= now:
        return jsonify(
            {
                "valid": False,
                "uid": uid,
                "expires_at": row["expires_at"],
                "message": "Subscription expired",
            }
        )

    days_left = max(0, (expires - now).days)
    return jsonify(
        {
            "valid": True,
            "uid": uid,
            "expires_at": row["expires_at"],
            "days_left": days_left,
            "message": "Active subscription",
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)