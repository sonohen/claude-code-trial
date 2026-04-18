import sqlite3
from datetime import datetime
from flask import Flask, g, redirect, render_template, request, url_for

app = Flask(__name__)
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
            created_at TEXT NOT NULL
        )"""
    )
    db.commit()
    db.close()


@app.route("/")
def index():
    db = get_db()
    messages = db.execute(
        "SELECT name, message, created_at FROM messages ORDER BY id DESC LIMIT 100"
    ).fetchall()
    return render_template("index.html", messages=messages)


@app.route("/post", methods=["POST"])
def post():
    name = request.form.get("name", "").strip()
    message = request.form.get("message", "").strip()
    if not name or not message:
        return redirect(url_for("index"))
    if len(name) > 50 or len(message) > 1000:
        return redirect(url_for("index"))
    db = get_db()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "INSERT INTO messages (name, message, created_at) VALUES (?, ?, ?)",
        (name, message, now),
    )
    db.commit()
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
