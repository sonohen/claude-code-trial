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
            user_id TEXT
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    # 既存DBへのマイグレーション
    cols = [r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()]
    if "user_id" not in cols:
        db.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")
    db.commit()
    db.close()


def current_user():
    return session.get("user_id")


def require_login():
    if not current_user():
        return redirect(url_for("login"))


# ── 掲示板 ──────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()
    messages = db.execute(
        "SELECT id, name, message, created_at, user_id FROM messages ORDER BY id DESC LIMIT 100"
    ).fetchall()
    return render_template("index.html", messages=messages)


@app.route("/post", methods=["POST"])
def post():
    redir = require_login()
    if redir:
        return redir
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not name or not message:
        return redirect(url_for("index"))
    if len(name) > 50 or len(message) > 1000:
        return redirect(url_for("index"))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO messages (name, message, created_at, user_id) VALUES (?, ?, ?, ?)",
        (name, message, now, current_user()),
    )
    db.commit()
    return redirect(url_for("index"))


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
                    "INSERT INTO users (user_id, password_hash, created_at) VALUES (?, ?, ?)",
                    (user_id, generate_password_hash(password), now),
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
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
