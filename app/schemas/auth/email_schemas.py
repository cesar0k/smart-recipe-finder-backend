from pydantic import BaseModel, EmailStr, Field


class SendVerificationEmailRequest(BaseModel):
    pass  # auth is via Bearer token, no body needed


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    captcha_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
