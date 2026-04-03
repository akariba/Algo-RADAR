from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask import current_app


def generate_confirmation_token(email: str) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(email, salt="email-confirm")


def verify_confirmation_token(token: str) -> str | None:
    """Returns email on success, None on failure."""
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    expiry = current_app.config.get("EMAIL_TOKEN_EXPIRY", 86400)
    try:
        return s.loads(token, salt="email-confirm", max_age=expiry)
    except (SignatureExpired, BadSignature):
        return None


def generate_password_reset_token(email: str) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(email, salt="password-reset")


def verify_password_reset_token(token: str, expiry: int = 3600) -> str | None:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        return s.loads(token, salt="password-reset", max_age=expiry)
    except (SignatureExpired, BadSignature):
        return None
