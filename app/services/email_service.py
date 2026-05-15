"""Email sending service via SMTP (Brevo / any SMTP relay).

All functions are fire-and-forget safe — they log errors and never raise.
Call them as asyncio.create_task(...) from endpoints or notification_service.
"""

import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.email_notification_preference import EmailNotificationPreference
from app.models.user import User

logger = logging.getLogger(__name__)

# Notification type → localised email strings
# Structure: {type: {lang: (subject, cta_text, greeting)}}
# Supported languages: "ru", "en". Falls back to "ru" for unknown values.

_EMAIL_STRINGS: dict[str, dict[str, tuple[str, str, str]]] = {
    # (subject, cta_button_label, "Hello" greeting)
    "new_comment": {
        "ru": ("Новый комментарий к вашему рецепту", "Перейти к рецепту", "Здравствуйте"),
        "en": ("New comment on your recipe", "View recipe", "Hello"),
    },
    "comment_reply": {
        "ru": ("Ответ на ваш комментарий", "Перейти к рецепту", "Здравствуйте"),
        "en": ("Reply to your comment", "View recipe", "Hello"),
    },
    "comment_reported": {
        "ru": ("Жалоба на комментарий", "Перейти к рецепту", "Здравствуйте"),
        "en": ("Comment reported", "View recipe", "Hello"),
    },
    "new_pending_recipe": {
        "ru": ("Новый рецепт на модерацию", "Открыть модерацию", "Здравствуйте"),
        "en": ("New recipe pending moderation", "Open moderation", "Hello"),
    },
    "recipe_approved": {
        "ru": ("Ваш рецепт одобрен ✓", "Открыть рецепт", "Здравствуйте"),
        "en": ("Your recipe has been approved ✓", "View recipe", "Hello"),
    },
    "recipe_rejected": {
        "ru": ("Ваш рецепт отклонён", "Открыть рецепт", "Здравствуйте"),
        "en": ("Your recipe was rejected", "View recipe", "Hello"),
    },
    "draft_approved": {
        "ru": ("Ваши изменения одобрены ✓", "Открыть рецепт", "Здравствуйте"),
        "en": ("Your changes have been approved ✓", "View recipe", "Hello"),
    },
    "draft_rejected": {
        "ru": ("Ваши изменения отклонены", "Открыть рецепт", "Здравствуйте"),
        "en": ("Your changes were rejected", "View recipe", "Hello"),
    },
    "recipe_deleted": {
        "ru": ("Ваш рецепт удалён", "", "Здравствуйте"),
        "en": ("Your recipe was deleted", "", "Hello"),
    },
    "user_followed": {
        "ru": ("На вас подписался новый пользователь", "", "Здравствуйте"),
        "en": ("Someone new followed you", "", "Hello"),
    },
    "followed_user_published": {
        "ru": ("Новый рецепт от автора, за которым вы следите", "Открыть рецепт", "Здравствуйте"),
        "en": ("New recipe from an author you follow", "View recipe", "Hello"),
    },
}

_FALLBACK_STRINGS: dict[str, tuple[str, str, str]] = {
    "ru": ("Уведомление от Smart Recipe Finder", "Открыть", "Здравствуйте"),
    "en": ("Notification from Smart Recipe Finder", "Open", "Hello"),
}


def _get_strings(notification_type: str, lang: str) -> tuple[str, str, str]:
    """Return (subject, cta_text, greeting) for the given type and language."""
    lang = lang if lang in ("ru", "en") else "ru"
    strings = _EMAIL_STRINGS.get(notification_type)
    if strings:
        return strings.get(lang, strings["ru"])
    return _FALLBACK_STRINGS.get(lang, _FALLBACK_STRINGS["ru"])

# Low-level SMTP send
async def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an HTML email via SMTP.

    Returns False (and logs a warning) when emails are globally disabled or
    SMTP is not configured. Never raises — callers should use create_task().
    """
    if not settings.EMAILS_ENABLED:
        logger.debug("Emails disabled globally — skipping send to %s", to_email)
        return False

    if not settings.SMTP_HOST or not settings.SMTP_LOGIN:
        logger.debug("SMTP not configured — skipping send to %s", to_email)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_LOGIN,
            password=settings.SMTP_PASSWORD,
            start_tls=True,
            timeout=15,
        )
        logger.info("Email sent to %s — subject: %s", to_email, subject)
        return True
    except Exception as exc:
        logger.warning("Failed to send email to %s: %s", to_email, exc)
        return False


# HTML helpers
def _base_template(title: str, body_html: str, cta_url: str = "", cta_text: str = "") -> str:
    """Minimal, inline-CSS HTML email template."""
    cta_block = ""
    if cta_url and cta_text:
        cta_block = f"""
        <p style="text-align:center;margin:32px 0;">
          <a href="{cta_url}"
             style="background:#111827;color:#fff;padding:12px 28px;
                    border-radius:24px;text-decoration:none;font-weight:600;
                    font-size:15px;display:inline-block;">
            {cta_text}
          </a>
        </p>"""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,
             'Segoe UI',sans-serif;color:#111827;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:16px;
                    border:1px solid #e5e7eb;overflow:hidden;max-width:600px;">
        <!-- Header -->
        <tr>
          <td style="background:#111827;padding:24px 32px;">
            <span style="color:#fff;font-size:20px;font-weight:700;
                         letter-spacing:-0.5px;">Smart Recipe Finder</span>
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:32px;">
            <h2 style="margin:0 0 16px;font-size:22px;font-weight:700;
                       color:#111827;">{title}</h2>
            {body_html}
            {cta_block}
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;border-top:1px solid #f3f4f6;">
            <p style="margin:0;font-size:12px;color:#9ca3af;">
              Smart Recipe Finder &mdash; вы получили это письмо, потому что
              связаны с этим сервисом. Если это не вы, просто проигнорируйте письмо.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# Transactional emails
_TRANSACTIONAL: dict[str, dict[str, dict[str, str]]] = {
    "verify": {
        "ru": {
            "subject": "Подтвердите email",
            "title": "Подтвердите ваш email",
            "greeting": "Здравствуйте",
            "body": (
                "Подтвердите ваш email-адрес, нажав на кнопку ниже. "
                "Ссылка действительна {hours} ч."
            ),
            "fallback": "Если кнопка не работает, скопируйте ссылку: {url}",
            "cta": "Подтвердить email",
            "footer": (
                "Smart Recipe Finder — вы получили это письмо, потому что "
                "связаны с этим сервисом. Если это не вы, просто проигнорируйте письмо."
            ),
        },
        "en": {
            "subject": "Confirm your email",
            "title": "Confirm your email address",
            "greeting": "Hello",
            "body": (
                "Click the button below to verify your email address. "
                "The link is valid for {hours} hours."
            ),
            "fallback": "If the button doesn't work, copy this link: {url}",
            "cta": "Verify email",
            "footer": (
                "Smart Recipe Finder — you received this email because your account is "
                "linked to this service. If this wasn't you, just ignore this message."
            ),
        },
    },
    "email_change": {
        "ru": {
            "subject": "Подтвердите новый email",
            "title": "Подтвердите новый email",
            "greeting": "Здравствуйте",
            "body": (
                "Вы запросили смену email-адреса. Нажмите кнопку ниже, чтобы подтвердить "
                "новый адрес <strong>{new_email}</strong>. "
                "Ссылка действительна {hours} ч."
            ),
            "ignore": "Если вы не запрашивали смену email — проигнорируйте это письмо.",
            "cta": "Подтвердить новый email",
            "footer": (
                "Smart Recipe Finder — вы получили это письмо, потому что "
                "связаны с этим сервисом."
            ),
        },
        "en": {
            "subject": "Confirm your new email",
            "title": "Confirm your new email address",
            "greeting": "Hello",
            "body": (
                "You requested an email address change. Click below to confirm "
                "<strong>{new_email}</strong> as your new address. "
                "The link is valid for {hours} hours."
            ),
            "ignore": "If you didn't request this change, just ignore this message.",
            "cta": "Confirm new email",
            "footer": (
                "Smart Recipe Finder — you received this email because your account is "
                "linked to this service."
            ),
        },
    },
    "reset": {
        "ru": {
            "subject": "Сброс пароля",
            "title": "Сброс пароля",
            "greeting": "Здравствуйте",
            "body": (
                "Мы получили запрос на сброс пароля для вашего аккаунта. "
                "Ссылка действительна {hours} ч."
            ),
            "ignore": "Если вы не запрашивали сброс пароля — просто проигнорируйте это письмо.",
            "fallback": "Если кнопка не работает, скопируйте ссылку: {url}",
            "cta": "Сбросить пароль",
            "footer": (
                "Smart Recipe Finder — вы получили это письмо, потому что "
                "связаны с этим сервисом."
            ),
        },
        "en": {
            "subject": "Reset your password",
            "title": "Reset your password",
            "greeting": "Hello",
            "body": (
                "We received a request to reset the password for your account. "
                "The link is valid for {hours} hour(s)."
            ),
            "ignore": "If you didn't request a password reset, just ignore this email.",
            "fallback": "If the button doesn't work, copy this link: {url}",
            "cta": "Reset password",
            "footer": (
                "Smart Recipe Finder — you received this email because your account is "
                "linked to this service."
            ),
        },
    },
}


def _t(key: str, lang: str, **kwargs: str) -> str:
    """Get a localised transactional string and format it."""
    lang = lang if lang in ("ru", "en") else "ru"
    tmpl = _TRANSACTIONAL[key][lang]
    return tmpl.format(**kwargs) if kwargs else tmpl  # type: ignore[return-value]


async def send_verification_email(user: User, token: str) -> None:
    """Send email verification link. token is the raw (un-hashed) token."""
    lang = getattr(user, "language", "ru") or "ru"
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    s = _TRANSACTIONAL["verify"][lang if lang in ("ru", "en") else "ru"]
    body = f"""
    <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">
      {s['greeting']}, <strong>{user.display_name or user.username}</strong>!
    </p>
    <p style="font-size:15px;line-height:1.6;color:#374151;margin:0 0 8px;">
      {s['body'].format(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)}
    </p>
    <p style="font-size:13px;color:#6b7280;margin:0;">
      {s['fallback'].format(url=f'<a href="{verify_url}">{verify_url}</a>')}
    </p>"""
    html = _base_template(
        title=s["title"], body_html=body, cta_url=verify_url, cta_text=s["cta"]
    )
    await send_email(user.email, f"{s['subject']} — Smart Recipe Finder", html)


async def send_email_change_confirmation(user: User, token: str, new_email: str) -> None:
    """Send a confirmation link to the *new* email address for an email change."""
    lang = getattr(user, "language", "ru") or "ru"
    confirm_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    s = _TRANSACTIONAL["email_change"][lang if lang in ("ru", "en") else "ru"]
    body = f"""
    <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">
      {s['greeting']}, <strong>{user.display_name or user.username}</strong>!
    </p>
    <p style="font-size:15px;line-height:1.6;color:#374151;margin:0 0 8px;">
      {s['body'].format(new_email=new_email, hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS)}
    </p>
    <p style="font-size:13px;color:#6b7280;margin:0;">
      {s['ignore']}
    </p>"""
    html = _base_template(
        title=s["title"], body_html=body, cta_url=confirm_url, cta_text=s["cta"]
    )
    await send_email(new_email, f"{s['subject']} — Smart Recipe Finder", html)


async def send_password_reset_email(user: User, token: str) -> None:
    """Send password reset link. token is the raw (un-hashed) token."""
    lang = getattr(user, "language", "ru") or "ru"
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    s = _TRANSACTIONAL["reset"][lang if lang in ("ru", "en") else "ru"]
    body = f"""
    <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">
      {s['greeting']}, <strong>{user.display_name or user.username}</strong>!
    </p>
    <p style="font-size:15px;line-height:1.6;color:#374151;margin:0 0 8px;">
      {s['body'].format(hours=settings.PASSWORD_RESET_TOKEN_EXPIRE_HOURS)}
    </p>
    <p style="font-size:13px;color:#6b7280;margin:0 0 16px;">
      {s['ignore']}
    </p>
    <p style="font-size:13px;color:#6b7280;margin:0;">
      {s['fallback'].format(url=f'<a href="{reset_url}">{reset_url}</a>')}
    </p>"""
    html = _base_template(
        title=s["title"], body_html=body, cta_url=reset_url, cta_text=s["cta"]
    )
    await send_email(user.email, f"{s['subject']} — Smart Recipe Finder", html)


# Notification emails
async def send_notification_email(
    db: AsyncSession,
    *,
    user: User,
    notification_type: str,
    message: str,
    recipe_id: int | None = None,
) -> None:
    """Send email for a notification event if user has it enabled.

    Checks email_notification_preferences. Default (no row) = enabled.
    Never raises — logs and returns.
    """
    # Check user preference
    result = await db.execute(
        select(EmailNotificationPreference).where(
            EmailNotificationPreference.user_id == user.id,
            EmailNotificationPreference.type == notification_type,
        )
    )
    pref = result.scalar_one_or_none()
    if pref is not None and not pref.enabled:
        logger.debug(
            "Email suppressed for user %d, type=%s (disabled by preference)",
            user.id,
            notification_type,
        )
        return

    lang = getattr(user, "language", "ru") or "ru"
    subject, cta_label, greeting = _get_strings(notification_type, lang)

    cta_url = ""
    if recipe_id and cta_label:
        cta_url = f"{settings.FRONTEND_URL}/recipe/{recipe_id}"

    body = f"""
    <p style="font-size:16px;line-height:1.6;margin:0 0 16px;">
      {greeting}, <strong>{user.display_name or user.username}</strong>!
    </p>
    <p style="font-size:15px;line-height:1.6;color:#374151;margin:0;">
      {message}
    </p>"""

    html = _base_template(
        title=subject,
        body_html=body,
        cta_url=cta_url,
        cta_text=cta_label,
    )
    await send_email(user.email, f"{subject} — Smart Recipe Finder", html)
