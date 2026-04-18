import sqlite3
from datetime import datetime
from flask import Flask, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "change-me-in-production"
DATABASE = "board.db"


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
    usr_cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "display_name" not in usr_cols:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
        db.execute("UPDATE users SET display_name = user_id WHERE display_name IS NULL")
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


def is_unlocked(message_id):
    return message_id in session.get("unlocked", [])


# ── 掲示板 ──────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()
    messages = db.execute(
        "SELECT id, name, message, created_at, user_id, password_hash FROM messages ORDER BY id DESC LIMIT 100"
    ).fetchall()
    display_name = get_display_name(db, current_user()) if current_user() else None
    unlocked = session.get("unlocked", [])
    return render_template("index.html", messages=messages, display_name=display_name, unlocked=unlocked)


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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO messages (name, message, created_at, user_id, password_hash) VALUES (?, ?, ?, ?, ?)",
        (name, message, now, current_user(), pw_hash),
    )
    db.commit()
    return redirect(url_for("index"))


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
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db.execute(
                    "INSERT INTO users (user_id, password_hash, display_name, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, generate_password_hash(password), user_id, now),
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
    name_error = name_success = pw_error = pw_success = None

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
        name_error=name_error,
        name_success=name_success,
        pw_error=pw_error,
        pw_success=pw_success,
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
