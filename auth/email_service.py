import boto3
from botocore.exceptions import ClientError
from flask import current_app
import logging

logger = logging.getLogger(__name__)


def _ses_client():
    return boto3.client("ses", region_name=current_app.config.get("AWS_REGION", "eu-central-1"))


def send_confirmation_email(to_email: str, token: str):
    base_url    = current_app.config["APP_BASE_URL"]
    sender      = current_app.config["SES_SENDER_EMAIL"]
    confirm_url = f"{base_url}/auth/confirm/{token}"

    subject   = "Confirm your SYBIL account"
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#0d0d0d;color:#e0e0e0;padding:40px;">
      <h2 style="color:#00ff88;">Welcome to SYBIL</h2>
      <p>Click the link below to confirm your email address. This link expires in 24 hours.</p>
      <p><a href="{confirm_url}" style="color:#00aaff;">{confirm_url}</a></p>
      <p style="color:#888;font-size:12px;">If you did not register, you can safely ignore this email.</p>
    </body></html>
    """
    body_text = f"Confirm your SYBIL account:\n{confirm_url}\n\nExpires in 24 hours."

    try:
        _ses_client().send_email(
            Source=sender,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": body_html, "Charset": "UTF-8"},
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("Confirmation email sent to %s", to_email)
    except ClientError as e:
        logger.error("SES send failed: %s", e.response["Error"]["Message"])
        raise


def send_password_reset_email(to_email: str, token: str):
    base_url  = current_app.config["APP_BASE_URL"]
    sender    = current_app.config["SES_SENDER_EMAIL"]
    reset_url = f"{base_url}/auth/reset-password/{token}"

    subject   = "Reset your SYBIL password"
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#0d0d0d;color:#e0e0e0;padding:40px;">
      <h2 style="color:#00ff88;">Password Reset</h2>
      <p><a href="{reset_url}" style="color:#00aaff;">Click here to reset your password</a></p>
      <p>This link expires in 1 hour.</p>
      <p style="color:#888;font-size:12px;">If you didn't request this, ignore it.</p>
    </body></html>
    """
    body_text = f"Reset your SYBIL password:\n{reset_url}\n\nExpires in 1 hour."

    try:
        _ses_client().send_email(
            Source=sender,
            Destination={"ToAddresses": [to_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Html": {"Data": body_html, "Charset": "UTF-8"},
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                },
            },
        )
        logger.info("Password reset email sent to %s", to_email)
    except ClientError as e:
        logger.error("SES password reset send failed: %s", e.response["Error"]["Message"])
        raise
