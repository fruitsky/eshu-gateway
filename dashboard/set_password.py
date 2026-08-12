#!/usr/bin/env python3
"""
Eshu Gateway Dashboard — Set Password CLI Tool

Usage:
    python set_password.py              # Set a new password (interactive prompt)
    python set_password.py --show       # Show whether a password is configured
"""

import sys
import os
import hashlib
import secrets

# Add the current directory to the path so we can import database
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_password_hash, set_password_hash
from getpass import getpass


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256 with 200,000 iterations."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        200_000,
        dklen=32
    )
    return f"$pbkdf2${salt}${key.hex()}"


def main():
    init_db()

    if '--show' in sys.argv or '--status' in sys.argv:
        pw = get_password_hash()
        if pw:
            print("🔒 Password protection: ENABLED")
        else:
            print("⚠️  No password set yet — set one on first launch to protect the dashboard.")
        return

    # Interactive password set
    print("=" * 50)
    print("  Eshu Gateway Dashboard — Set Password")
    print("=" * 50)
    print()

    current = get_password_hash()
    if current:
        print("A password is already set. You will overwrite it.")

    password = getpass("New password (min 4 chars): ")
    if len(password.strip()) < 4:
        print("❌ Password must be at least 4 characters. Aborted.")
        sys.exit(1)

    confirm = getpass("Confirm password: ")
    if password != confirm:
        print("❌ Passwords do not match. Aborted.")
        sys.exit(1)

    hash_value = hash_password(password.strip())
    set_password_hash(hash_value)

    print()
    print("✅ Dashboard password set successfully!")
    print("   You will be prompted to log in on your next dashboard visit.")


if __name__ == '__main__':
    main()