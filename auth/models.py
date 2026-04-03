"""
User models — two backends selectable via AUTH_BACKEND env var:
  AUTH_BACKEND=rds      → PostgreSQL via SQLAlchemy (default)
  AUTH_BACKEND=dynamo   → DynamoDB via boto3
"""
import uuid
from datetime import datetime, timezone

import bcrypt

# ── RDS / PostgreSQL ──────────────────────────────────────────────────────────

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_confirmed  = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    confirmed_at  = db.Column(db.DateTime(timezone=True), nullable=True)
    last_login    = db.Column(db.DateTime(timezone=True), nullable=True)

    def set_password(self, password: str):
        self.password_hash = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt(rounds=12)
        ).decode()

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def confirm(self):
        self.is_confirmed = True
        self.confirmed_at = datetime.now(timezone.utc)


# ── DynamoDB ──────────────────────────────────────────────────────────────────

import boto3
from flask import current_app


def _dynamo_table():
    dynamodb = boto3.resource("dynamodb", region_name=current_app.config.get("AWS_REGION", "eu-central-1"))
    return dynamodb.Table("sybil_users")


def create_dynamo_table(region: str = "eu-central-1"):
    """Run once to provision the DynamoDB table."""
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.create_table(
        TableName="sybil_users",
        KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    print("DynamoDB table 'sybil_users' created.")


class DynamoUser:
    @staticmethod
    def create(email: str, password: str) -> dict:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        item = {
            "email":         email,
            "id":            str(uuid.uuid4()),
            "password_hash": password_hash,
            "is_confirmed":  False,
            "created_at":    datetime.now(timezone.utc).isoformat(),
            "confirmed_at":  None,
        }
        _dynamo_table().put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(email)"
        )
        return item

    @staticmethod
    def get(email: str) -> dict | None:
        resp = _dynamo_table().get_item(Key={"email": email})
        return resp.get("Item")

    @staticmethod
    def confirm(email: str):
        _dynamo_table().update_item(
            Key={"email": email},
            UpdateExpression="SET is_confirmed = :t, confirmed_at = :d",
            ExpressionAttributeValues={
                ":t": True,
                ":d": datetime.now(timezone.utc).isoformat(),
            },
        )

    @staticmethod
    def check_password(user: dict, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), user["password_hash"].encode())
