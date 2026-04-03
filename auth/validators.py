from marshmallow import Schema, fields, validate


class RegistrationSchema(Schema):
    email    = fields.Email(required=True, validate=validate.Length(max=255))
    password = fields.Str(required=True, validate=[
        validate.Length(min=8, max=128),
        validate.Regexp(
            r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])",
            error="Password must contain uppercase, lowercase, digit, and special character."
        )
    ])


registration_schema = RegistrationSchema()
