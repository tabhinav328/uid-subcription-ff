import math
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


VALID_UNITS = {"minutes", "hours", "days"}


def duration_to_timedelta(amount: int, unit: str) -> timedelta:
    if unit == "minutes":
        return timedelta(minutes=amount)
    if unit == "hours":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def format_duration(amount: int, unit: str) -> str:
    label = {"minutes": "minute", "hours": "hour", "days": "day"}[unit]
    if amount == 1:
        return f"1 {label}"
    return f"{amount} {label}s"


def days_remaining(expires: datetime, now: datetime) -> int:
    """Count partial days as 1 day left (1 day = 24h from save/extend time)."""
    if expires <= now:
        return 0
    seconds_left = (expires - now).total_seconds()
    return max(0, math.ceil(seconds_left / 86400))


def time_remaining(expires: datetime, now: datetime) -> str:
    if expires <= now:
        return "0m"
    seconds_left = int((expires - now).total_seconds())
    days = seconds_left // 86400
    hours = (seconds_left % 86400) // 3600
    minutes = (seconds_left % 3600) // 60

    if days > 0:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours > 0:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes > 0:
        return f"{minutes}m"
    return "<1m"


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


def save_subscription(uid: str, amount: int, unit: str, note: str) -> tuple[bool, str]:
    if not uid.isdigit():
        return False, "UID must be numeric"
    if amount <= 0:
        return False, "Duration must be greater than 0"
    if unit not in VALID_UNITS:
        return False, "Invalid duration unit"

    delta = duration_to_timedelta(amount, unit)
    now = datetime.utcnow()
    expires = now + delta

    with get_db() as conn:
        existing = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE uid = ?",
            (uid,),
        ).fetchone()
        if existing:
            current = parse_iso(existing["expires_at"])
            base = current if current > now else now
            expires = base + delta

        conn.execute(
            """
            INSERT INTO subscriptions (uid, expires_at, created_at, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(uid) DO UPDATE SET
                expires_at = excluded.expires_at,
                note = CASE
                    WHEN excluded.note != '' THEN excluded.note
                    ELSE subscriptions.note
                END
            """,
            (
                uid,
                expires.isoformat(),
                now.isoformat(),
                note,
            ),
        )
        conn.commit()

    duration_label = format_duration(amount, unit)
    return True, f"Subscription saved for UID {uid} (+{duration_label})"


@app.post("/admin/dashboard")
@admin_required
def admin_dashboard_save():
    uid = request.form.get("uid", "").strip()
    amount = int(request.form.get("amount", "0") or "0")
    unit = request.form.get("unit", "days").strip().lower()
    note = request.form.get("note", "").strip()

    ok, message = save_subscription(uid, amount, unit, note)
    flash(message, "ok" if ok else "error")

    # Post/Redirect/Get: refresh must not resubmit the form and add days again.
    return redirect(url_for("admin_dashboard"))


@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
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
                "days_left": days_remaining(expires, now),
                "time_left": time_remaining(expires, now),
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

    days_left = days_remaining(expires, now)
    return jsonify(
        {
            "valid": True,
            "uid": uid,
            "expires_at": row["expires_at"],
            "days_left": days_left,
            "time_left": time_remaining(expires, now),
            "message": "Active subscription",
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)