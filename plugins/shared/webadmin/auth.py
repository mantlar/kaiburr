"""
Authentication module for the WebAdmin plugin.
Handles password hashing/verification and session lifecycle.
"""

import bcrypt
import secrets
import string
import logging

from . import db

Log = logging.getLogger(__name__)


def hash_password(plain_password):
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password, hashed_password):
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def login(username, password, ip_address, session_lifetime_hours=24):
    """
    Attempt to log in a user. Returns (session_token, user_dict) on success,
    or (None, error_string) on failure.
    """
    user = db.get_user_by_username(username)
    if not user:
        return None, "Invalid username or password"

    if not user["is_active"]:
        return None, "Account is disabled"

    if not verify_password(password, user["password_hash"]):
        return None, "Invalid username or password"

    # Create session
    token = db.create_session(user["id"], ip_address, session_lifetime_hours)

    # Update last login
    from datetime import datetime
    db.update_user(user["id"], last_login=datetime.utcnow().isoformat())

    user_info = {
        "id": user["id"],
        "username": user["username"],
        "smod_level": user["smod_level"]
    }

    Log.info(f"User '{username}' logged in from {ip_address}")
    return token, user_info


def logout(token):
    """Destroy a session by token."""
    db.destroy_session(token)


def get_session_user(token):
    """Validate a session token and return the user dict, or None."""
    if not token:
        return None
    return db.validate_session(token)


def generate_random_password(length=12):
    """Generate a random password for the default admin account."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ensure_default_admin():
    """
    If no users exist, create a default admin account and print credentials to console.
    Returns True if a default admin was created.
    """
    if db.get_user_count() > 0:
        return False

    username = "admin"
    password = generate_random_password()
    pw_hash = hash_password(password)

    user_id = db.create_user(username, pw_hash, smod_level=3)
    if user_id:
        print("=" * 60)
        print("  WebAdmin: Default admin account created!")
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        print("  [!] Change this password after first login!")
        print("=" * 60)
        Log.info(f"Default admin account created (user_id={user_id})")
        return True
    else:
        Log.error("Failed to create default admin account")
        return False
