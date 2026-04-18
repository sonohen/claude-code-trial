import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "change-me-in-production"
DATABASE = "board.db"

TIMEZONES = [
    ("UTC",                 "UTC"),
    ("Asia/Tokyo",          "Asia/Tokyo (UTC+9)"),
    ("Asia/Seoul",          "Asia/Seoul (UTC+9)"),
    ("Asia/Shanghai",       "Asia/Shanghai (UTC+8)"),
    ("Asia/Singapore",      "Asia/Singapore (UTC+8)"),
    ("Asia/Kolkata",        "Asia/Kolkata (UTC+5:30)"),
    ("Asia/Dubai",          "Asia/Dubai (UTC+4)"),
    ("Europe/London",       "Europe/London"),
    ("Europe/Paris",        "Europe/Paris (UTC+1/2)"),
    ("Europe/Berlin",       "Europe/Berlin (UTC+1/2)"),
    ("America/New_York",    "America/New_York (UTC-5/-4)"),
    ("America/Chicago",     "America/Chicago (UTC-6/-5)"),
    ("America/Denver",      "America/Denver (UTC-7/-6)"),
    ("America/Los_Angeles", "America/Los_Angeles (UTC-8/-7)"),
    ("Australia/Sydney",    "Australia/Sydney"),
]

DEFAULT_TZ = "Asia/Tokyo"
DEFAULT_PER_PAGE = 20
PER_PAGE_OPTIONS = [10, 20, 50, 100]


@app.template_filter("localtime")
def localtime_filter(dt_str, tz_name):
    """UTC文字列をユーザーのタイムゾーンへ変換して返す。"""
    if not dt_str or not tz_name:
        return dt_str
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str




def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id TEXT,
            password_hash TEXT
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # マイグレーション
    msg_cols = [r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()]
    if "user_id" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")
    if "password_hash" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN password_hash TEXT")
    if "parent_id" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN parent_id INTEGER")
    usr_cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "display_name" not in usr_cols:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        db.execute("UPDATE users SET display_name = user_id WHERE display_name IS NULL")
    if "timezone" not in usr_cols:
        db.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
        db.execute("UPDATE users SET timezone = ? WHERE timezone IS NULL", (DEFAULT_TZ,))
    if "per_page" not in usr_cols:
        db.execute("ALTER TABLE users ADD COLUMN per_page INTEGER")
        db.execute("UPDATE users SET per_page = ? WHERE per_page IS NULL", (DEFAULT_PER_PAGE,))
    db.commit()
    db.close()


def current_user():
    return session.get("user_id")


def get_display_name(db, user_id):
    row = db.execute("SELECT display_name FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["display_name"] if row and row["display_name"] else user_id


def require_login():
    if not current_user():
        return redirect(url_for("login"))


@app.context_processor
def inject_user_tz():
    tz = DEFAULT_TZ
    if current_user():
        db = get_db()
        row = db.execute("SELECT timezone FROM users WHERE user_id = ?", (current_user(),)).fetchone()
        if row and row["timezone"]:
            tz = row["timezone"]
    return {"user_tz": tz}


def is_unlocked(message_id):
    return message_id in session.get("unlocked", [])


# ── 掲示板 ──────────────────────────────────────────────

def get_per_page(db):
    if current_user():
        row = db.execute("SELECT per_page FROM users WHERE user_id = ?", (current_user(),)).fetchone()
        if row and row["per_page"]:
            return int(row["per_page"])
    return DEFAULT_PER_PAGE


@app.route("/")
def index():
    db = get_db()
    per_page = get_per_page(db)
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    offset = (page - 1) * per_page

    total = db.execute(
        "SELECT COUNT(*) FROM messages WHERE parent_id IS NULL"
    ).fetchone()[0]
    total_pages = max(1, -(-total // per_page))  # ceiling division
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    parents = db.execute(
        "SELECT id, name, message, created_at, user_id, password_hash FROM messages"
        " WHERE parent_id IS NULL ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    ).fetchall()

    parent_ids = [m["id"] for m in parents]
    replies_map = {}
    if parent_ids:
        placeholders = ",".join("?" * len(parent_ids))
        rows = db.execute(
            f"SELECT id, name, message, created_at, user_id, parent_id FROM messages"
            f" WHERE parent_id IN ({placeholders}) ORDER BY id ASC",
            parent_ids,
        ).fetchall()
        for r in rows:
            replies_map.setdefault(r["parent_id"], []).append(r)

    display_name = get_display_name(db, current_user()) if current_user() else None
    unlocked = session.get("unlocked", [])
    return render_template(
        "index.html",
        messages=parents,
        replies_map=replies_map,
        display_name=display_name,
        unlocked=unlocked,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
    )


@app.route("/post", methods=["POST"])
def post():
    redir = require_login()
    if redir:
        return redir
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    post_password = request.form.get("post_password", "").strip()
    if not name or not message:
        return redirect(url_for("index"))
    if len(name) > 50 or len(message) > 1000:
        return redirect(url_for("index"))
    pw_hash = generate_password_hash(post_password) if post_password else None
    db = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO messages (name, message, created_at, user_id, password_hash, parent_id)"
        " VALUES (?, ?, ?, ?, ?, NULL)",
        (name, message, now, current_user(), pw_hash),
    )
    db.commit()
    return redirect(url_for("index"))


@app.route("/reply/<int:parent_id>", methods=["GET", "POST"])
def reply(parent_id):
    redir = require_login()
    if redir:
        return redir
    db = get_db()
    parent = db.execute(
        "SELECT id, name, message, created_at, user_id FROM messages WHERE id = ? AND parent_id IS NULL",
        (parent_id,),
    ).fetchone()
    if parent is None:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not message:
            error = "名前とメッセージを入力してください。"
        elif len(name) > 50 or len(message) > 1000:
            error = "名前は50文字以内、メッセージは1000文字以内で入力してください。"
        else:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO messages (name, message, created_at, user_id, parent_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, message, now, current_user(), parent_id),
            )
            db.commit()
            return redirect(url_for("index"))
    display_name = get_display_name(db, current_user())
    return render_template("reply.html", parent=parent, display_name=display_name, error=error)


@app.route("/api/reply/<int:parent_id>", methods=["POST"])
def api_reply(parent_id):
    if not current_user():
        return jsonify({"error": "ログインが必要です。"}), 401
    db = get_db()
    parent = db.execute(
        "SELECT id FROM messages WHERE id = ? AND parent_id IS NULL", (parent_id,)
    ).fetchone()
    if parent is None:
        return jsonify({"error": "投稿が見つかりません。"}), 404
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not name or not message:
        return jsonify({"error": "名前とメッセージを入力してください。"}), 400
    if len(name) > 50 or len(message) > 1000:
        return jsonify({"error": "名前は50文字以内、メッセージは1000文字以内で入力してください。"}), 400
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur = db.execute(
        "INSERT INTO messages (name, message, created_at, user_id, parent_id) VALUES (?, ?, ?, ?, ?)",
        (name, message, now_utc, current_user(), parent_id),
    )
    db.commit()
    # ユーザーのタイムゾーンで変換した日時を返す
    user_row = db.execute("SELECT timezone FROM users WHERE user_id = ?", (current_user(),)).fetchone()
    tz_name = user_row["timezone"] if user_row and user_row["timezone"] else DEFAULT_TZ
    display_time = localtime_filter(now_utc, tz_name)
    return jsonify({
        "id": cur.lastrowid,
        "name": name,
        "message": message,
        "created_at": display_time,
    }), 201


@app.route("/view/<int:message_id>", methods=["GET", "POST"])
def view(message_id):
    db = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if msg is None:
        return redirect(url_for("index"))
    # 投稿者本人またはすでにアンロック済みなら即表示
    if msg["user_id"] == current_user() or is_unlocked(message_id):
        return render_template("view.html", msg=msg, unlocked=True, error=None)
    error = None
    if request.method == "POST":
        entered = request.form.get("password", "")
        if check_password_hash(msg["password_hash"], entered):
            unlocked = session.get("unlocked", [])
            unlocked.append(message_id)
            session["unlocked"] = unlocked
            return redirect(url_for("view", message_id=message_id))
        error = "パスワードが正しくありません。"
    return render_template("view.html", msg=msg, unlocked=False, error=error)


@app.route("/edit/<int:message_id>", methods=["GET", "POST"])
def edit(message_id):
    redir = require_login()
    if redir:
        return redir
    db = get_db()
    row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None or row["user_id"] != current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        message = request.form.get("message", "").strip()
        if name and message and len(name) <= 50 and len(message) <= 1000:
            db.execute(
                "UPDATE messages SET name = ?, message = ? WHERE id = ?",
                (name, message, message_id),
            )
            db.commit()
        return redirect(url_for("index"))
    return render_template("edit.html", msg=row)


@app.route("/delete/<int:message_id>", methods=["POST"])
def delete(message_id):
    redir = require_login()
    if redir:
        return redir
    db = get_db()
    row = db.execute("SELECT user_id FROM messages WHERE id = ?", (message_id,)).fetchone()
    if row is None or row["user_id"] != current_user():
        return redirect(url_for("index"))
    db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
    db.commit()
    return redirect(url_for("index"))


# ── 認証 ────────────────────────────────────────────────

@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        if not user_id or not password:
            error = "IDとパスワードを入力してください。"
        elif len(user_id) > 50:
            error = "IDは50文字以内で入力してください。"
        elif len(password) < 6:
            error = "パスワードは6文字以上で入力してください。"
        elif password != confirm:
            error = "パスワードが一致しません。"
        else:
            db = get_db()
            exists = db.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if exists:
                error = "そのIDはすでに使われています。"
            else:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "INSERT INTO users (user_id, password_hash, display_name, timezone, per_page, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, generate_password_hash(password), user_id, DEFAULT_TZ, DEFAULT_PER_PAGE, now),
                )
                db.commit()
                session["user_id"] = user_id
                return redirect(url_for("index"))

    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            error = "IDまたはパスワードが正しくありません。"
        else:
            session["user_id"] = user_id
            return redirect(url_for("index"))

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("unlocked", None)
    return redirect(url_for("index"))


# ── 設定 ────────────────────────────────────────────────

@app.route("/settings", methods=["GET", "POST"])
def settings():
    redir = require_login()
    if redir:
        return redir
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE user_id = ?", (current_user(),)).fetchone()
    name_error = name_success = pw_error = pw_success = tz_error = tz_success = pp_error = pp_success = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "name":
            new_name = request.form.get("display_name", "").strip()
            if not new_name:
                name_error = "名前を入力してください。"
            elif len(new_name) > 50:
                name_error = "名前は50文字以内で入力してください。"
            else:
                db.execute(
                    "UPDATE users SET display_name = ? WHERE user_id = ?",
                    (new_name, current_user()),
                )
                db.execute(
                    "UPDATE messages SET name = ? WHERE user_id = ?",
                    (new_name, current_user()),
                )
                db.commit()
                name_success = "名前を変更しました。"
                user = db.execute("SELECT * FROM users WHERE user_id = ?", (current_user(),)).fetchone()

        elif action == "per_page":
            try:
                new_pp = int(request.form.get("per_page", DEFAULT_PER_PAGE))
            except (ValueError, TypeError):
                new_pp = None
            if new_pp not in PER_PAGE_OPTIONS:
                pp_error = "無効な件数です。"
            else:
                db.execute("UPDATE users SET per_page = ? WHERE user_id = ?", (new_pp, current_user()))
                db.commit()
                pp_success = "表示件数を変更しました。"
                user = db.execute("SELECT * FROM users WHERE user_id = ?", (current_user(),)).fetchone()

        elif action == "timezone":
            new_tz = request.form.get("timezone", "").strip()
            valid = [tz for tz, _ in TIMEZONES]
            if new_tz not in valid:
                tz_error = "無効なタイムゾーンです。"
            else:
                db.execute("UPDATE users SET timezone = ? WHERE user_id = ?", (new_tz, current_user()))
                db.commit()
                tz_success = "タイムゾーンを変更しました。"
                user = db.execute("SELECT * FROM users WHERE user_id = ?", (current_user(),)).fetchone()

        elif action == "password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")
            if not check_password_hash(user["password_hash"], current_pw):
                pw_error = "現在のパスワードが正しくありません。"
            elif len(new_pw) < 6:
                pw_error = "新しいパスワードは6文字以上で入力してください。"
            elif new_pw != confirm_pw:
                pw_error = "新しいパスワードが一致しません。"
            else:
                db.execute(
                    "UPDATE users SET password_hash = ? WHERE user_id = ?",
                    (generate_password_hash(new_pw), current_user()),
                )
                db.commit()
                pw_success = "パスワードを変更しました。"

    return render_template(
        "settings.html",
        user=user,
        timezones=TIMEZONES,
        per_page_options=PER_PAGE_OPTIONS,
        name_error=name_error,
        name_success=name_success,
        pw_error=pw_error,
        pw_success=pw_success,
        tz_error=tz_error,
        tz_success=tz_success,
        pp_error=pp_error,
        pp_success=pp_success,
    )


@app.route("/user/<user_id>")
def user_posts(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if user is None:
        return redirect(url_for("index"))
    messages = db.execute(
        "SELECT id, name, message, created_at, password_hash FROM messages WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    unlocked = session.get("unlocked", [])
    return render_template("user_posts.html", user=user, messages=messages, unlocked=unlocked)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
