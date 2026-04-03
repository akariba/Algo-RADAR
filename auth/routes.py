from datetime import datetime, timezone

import bcrypt
from flask import Blueprint, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from .email_service import send_confirmation_email, send_password_reset_email
from .models import User, db
from .tokens import (generate_confirmation_token, generate_password_reset_token,
                     verify_confirmation_token, verify_password_reset_token)
from .validators import registration_schema

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
limiter = Limiter(key_func=get_remote_address)

# Dummy hash used for constant-time comparison when user is not found
_DUMMY_HASH = "$2b$12$invalidhashfortimingprotectionXXXXXXXXXXXXXXXXXXXXXXX"


# ── Register ──────────────────────────────────────────────────────────────────

@auth_bp.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    try:
        data = registration_schema.load(request.get_json(silent=True) or {})
    except ValidationError as e:
        return jsonify({"error": "Validation failed", "details": e.messages}), 422

    user = User(email=data["email"].lower())
    user.set_password(data["password"])

    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Email already registered."}), 409

    token = generate_confirmation_token(user.email)
    try:
        send_confirmation_email(user.email, token)
    except Exception:
        pass  # logged inside email_service; don't expose SES errors

    return jsonify({"message": "Registration successful. Check your email to confirm."}), 201


# ── Confirm Email ─────────────────────────────────────────────────────────────

@auth_bp.route("/confirm/<token>", methods=["GET"])
@limiter.limit("10 per minute")
def confirm_email(token: str):
    email = verify_confirmation_token(token)
    if not email:
        return jsonify({"error": "Invalid or expired confirmation link."}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found."}), 404
    if user.is_confirmed:
        return jsonify({"message": "Account already confirmed."}), 200

    user.confirm()
    db.session.commit()
    return jsonify({"message": "Email confirmed. You can now log in."}), 200


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password required."}), 400

    user = User.query.filter_by(email=email).first()

    # Always run bcrypt to prevent timing-based user enumeration
    if user:
        valid = user.check_password(password)
    else:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH.encode())
        valid = False

    if not user or not valid:
        return jsonify({"error": "Invalid credentials."}), 401
    if not user.is_confirmed:
        return jsonify({"error": "Please confirm your email before logging in."}), 403

    user.last_login = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"message": "Login successful.", "user_id": str(user.id)}), 200


# ── Forgot Password ───────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("3 per minute")
def forgot_password():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").lower().strip()

    user = User.query.filter_by(email=email).first()
    if user and user.is_confirmed:
        token = generate_password_reset_token(user.email)
        try:
            send_password_reset_email(user.email, token)
        except Exception:
            pass

    # Always 200 — never reveal whether email exists
    return jsonify({"message": "If that email exists, a reset link has been sent."}), 200


# ── Reset Password ────────────────────────────────────────────────────────────

@auth_bp.route("/reset-password/<token>", methods=["POST"])
@limiter.limit("5 per minute")
def reset_password(token: str):
    email = verify_password_reset_token(token, expiry=3600)
    if not email:
        return jsonify({"error": "Invalid or expired reset link."}), 400

    data         = request.get_json(silent=True) or {}
    new_password = data.get("password") or ""

    try:
        registration_schema.fields["password"].validate(new_password)
    except Exception:
        return jsonify({"error": "Password does not meet complexity requirements."}), 422

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "User not found."}), 404

    user.set_password(new_password)
    db.session.commit()
    return jsonify({"message": "Password updated successfully."}), 200


# ── Health ────────────────────────────────────────────────────────────────────

@auth_bp.route("/health", methods=["GET"])
def auth_health():
    return jsonify({"status": "ok", "service": "auth"}), 200
