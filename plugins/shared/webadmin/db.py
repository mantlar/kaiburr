"""
Database module for the WebAdmin plugin.
Handles SQLite schema creation, user management, session management, and audit logging.
"""

import os
import sqlite3
import secrets
import logging
import json
from datetime import datetime, timedelta

Log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "webadmin.db")


def get_connection():
    """Get a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT    NOT NULL,
                smod_level    INTEGER NOT NULL DEFAULT 1,
                discord_id    TEXT    UNIQUE,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login    TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER REFERENCES users(id),
                action     TEXT    NOT NULL,
                target     TEXT,
                details    TEXT,
                ip_address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
        """)
        conn.commit()
    finally:
        conn.close()


# --- User Management ---

def create_user(username, password_hash, smod_level=1, discord_id=None):
    """Create a new admin user. Returns the user id or None on failure."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, smod_level, discord_id) VALUES (?, ?, ?, ?)",
            (username, password_hash, smod_level, discord_id)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        Log.warning(f"Failed to create user '{username}': {e}")
        return None
    finally:
        conn.close()


def get_user_by_id(user_id):
    """Get a user by ID. Returns a dict or None."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_username(username):
    """Get a user by username (case-insensitive). Returns a dict or None."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_user(user_id, **kwargs):
    """Update user fields. Accepts keyword args matching column names."""
    allowed = {"username", "password_hash", "smod_level", "discord_id", "is_active", "last_login"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]

    conn = get_connection()
    try:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True
    finally:
        conn.close()


def list_users():
    """List all users (without password hashes)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, smod_level, discord_id, is_active, created_at, last_login FROM users"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_count():
    """Get total number of users."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return row[0]
    finally:
        conn.close()


def delete_user(user_id):
    """Delete a user and their sessions."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# --- Session Management ---

def create_session(user_id, ip_address, lifetime_hours=24):
    """Create a new session. Returns the token string."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=lifetime_hours)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, ip_address, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, ip_address, expires_at.isoformat())
        )
        conn.commit()
        return token
    finally:
        conn.close()


def validate_session(token):
    """Validate a session token. Returns user dict or None if invalid/expired."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT s.user_id, s.expires_at, u.id, u.username, u.smod_level, u.is_active
               FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.token = ?""",
            (token,)
        ).fetchone()

        if not row:
            return None

        # Check expiration
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires_at:
            destroy_session(token)
            return None

        # Check if user is still active
        if not row["is_active"]:
            destroy_session(token)
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "smod_level": row["smod_level"]
        }
    finally:
        conn.close()


def destroy_session(token):
    """Delete a session by token."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def destroy_user_sessions(user_id):
    """Delete all sessions for a user."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def cleanup_expired_sessions():
    """Remove all expired sessions."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.utcnow().isoformat(),))
        conn.commit()
    finally:
        conn.close()


# --- Audit Log ---

def log_action(user_id, action, target=None, details=None, ip_address=None):
    """Record an admin action in the audit log."""
    details_json = json.dumps(details) if isinstance(details, dict) else details
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO audit_log (user_id, action, target, details, ip_address) VALUES (?, ?, ?, ?, ?)",
            (user_id, action, target, details_json, ip_address)
        )
        conn.commit()
    except Exception as e:
        Log.error(f"Failed to log audit action: {e}")
    finally:
        conn.close()


def get_audit_log(limit=100, offset=0, user_id=None, action=None):
    """Query the audit log with optional filters."""
    conn = get_connection()
    try:
        query = """
            SELECT a.id, a.user_id, u.username, a.action, a.target, a.details, a.ip_address, a.created_at
            FROM audit_log a
            LEFT JOIN users u ON a.user_id = u.id
        """
        conditions = []
        params = []

        if user_id is not None:
            conditions.append("a.user_id = ?")
            params.append(user_id)
        if action is not None:
            conditions.append("a.action = ?")
            params.append(action)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY a.created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            entry = dict(r)
            # Parse JSON details if present
            if entry.get("details"):
                try:
                    entry["details"] = json.loads(entry["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(entry)
        return results
    finally:
        conn.close()
