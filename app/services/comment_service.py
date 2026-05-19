"""Comment service: create, soft-delete, report, and list comments with replies."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import Cache
from app.core.exceptions import (
    InvalidStateError,
    NotAuthorizedError,
    NotFoundError,
    ValidationError,
)
from app.models import Recipe
from app.models.recipe_comment import RecipeComment
from app.models.recipe_comment_report import RecipeCommentReport
from app.models.user import User
from app.schemas.comment.comment import CommentResponse
from app.services import cache_keys, search_cache, similar_cache
from app.services.rating_service import recompute_engagement_score

log = logging.getLogger(__name__)

DELETED_CONTENT = ""


async def _bump_caches(cache: Cache | None, *, recipe_id: int) -> None:
    if cache is None:
        return
    await search_cache.bump_search_version(cache)
    await similar_cache.bump_similar_version(cache)
    await cache_keys.invalidate_on_recipe_change(cache, recipe_id=recipe_id)


async def _recompute_comments_count(db: AsyncSession, *, recipe_id: int) -> None:
    count_q = select(func.count(RecipeComment.id)).where(
        RecipeComment.recipe_id == recipe_id,
        RecipeComment.is_deleted.is_(False),
    )
    new_count = (await db.execute(count_q)).scalar_one()
    await db.execute(
        update(Recipe).where(Recipe.id == recipe_id).values(comments_count=new_count)
    )
    await recompute_engagement_score(db, recipe_id=recipe_id)


def _user_role(user: object) -> str | None:
    """Return 'admin' | 'moderator' | None based on user.role."""
    role = getattr(user, "role", None)
    if role in ("admin", "moderator"):
        return role
    return None


def _build_response(comment: RecipeComment, replies: list[RecipeComment]) -> CommentResponse:
    """Assemble a CommentResponse from ORM objects (author already loaded)."""
    author_username: str | None = None
    author_avatar_url: str | None = None
    author_role: str | None = None
    try:
        if comment.user:
            author_username = comment.user.username
            author_avatar_url = comment.user.avatar_url
            author_role = _user_role(comment.user)
    except Exception:
        pass

    reply_responses = []
    for r in replies:
        r_username: str | None = None
        r_avatar: str | None = None
        r_role: str | None = None
        try:
            if r.user:
                r_username = r.user.username
                r_avatar = r.user.avatar_url
                r_role = _user_role(r.user)
        except Exception:
            pass
        reply_responses.append(
            CommentResponse(
                id=r.id,
                recipe_id=r.recipe_id,
                user_id=r.user_id,
                author_username=r_username,
                author_avatar_url=r_avatar,
                author_role=r_role,
                parent_comment_id=r.parent_comment_id,
                content=r.content,
                is_deleted=r.is_deleted,
                created_at=r.created_at,
                updated_at=r.updated_at,
                replies=[],
            )
        )

    return CommentResponse(
        id=comment.id,
        recipe_id=comment.recipe_id,
        user_id=comment.user_id,
        author_username=author_username,
        author_avatar_url=author_avatar_url,
        author_role=author_role,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        is_deleted=comment.is_deleted,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        replies=reply_responses,
    )


async def get_comments(
    db: AsyncSession,
    *,
    recipe_id: int,
    skip: int = 0,
    limit: int = 50,
) -> list[CommentResponse]:
    """Return paginated top-level comments with their replies nested inside."""
    # Query 1: top-level comments (paginated)
    top_q = (
        select(RecipeComment)
        .where(
            RecipeComment.recipe_id == recipe_id,
            RecipeComment.parent_comment_id.is_(None),
        )
        .options(selectinload(RecipeComment.user))
        .order_by(RecipeComment.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    top_result = await db.execute(top_q)
    top_comments: Sequence[RecipeComment] = top_result.scalars().all()

    if not top_comments:
        return []

    top_ids = [c.id for c in top_comments]

    # Query 2: all replies for those top-level comments in one round-trip
    replies_q = (
        select(RecipeComment)
        .where(RecipeComment.parent_comment_id.in_(top_ids))
        .options(selectinload(RecipeComment.user))
        .order_by(RecipeComment.created_at.asc())
    )
    replies_result = await db.execute(replies_q)
    all_replies: Sequence[RecipeComment] = replies_result.scalars().all()

    # Group replies by parent_comment_id
    replies_map: dict[int, list[RecipeComment]] = {}
    for r in all_replies:
        pid = r.parent_comment_id
        if pid is not None:
            replies_map.setdefault(pid, []).append(r)

    return [_build_response(c, replies_map.get(c.id, [])) for c in top_comments]


async def create_comment(
    db: AsyncSession,
    *,
    user: User,
    recipe_id: int,
    content: str,
    parent_comment_id: int | None = None,
    cache: Cache | None = None,
) -> CommentResponse:
    """Create a top-level comment or a reply.

    Replies must target a top-level comment (no reply-to-reply).
    Raises NotFoundError when the recipe or parent comment doesn't exist.
    Raises InvalidStateError when the recipe is not approved or parent is a reply.
    """
    # Validate recipe
    recipe_result = await db.execute(select(Recipe).where(Recipe.id == recipe_id))
    recipe = recipe_result.scalar_one_or_none()
    if recipe is None or recipe.status != "approved":
        raise NotFoundError("Recipe not found")

    # Validate parent comment
    if parent_comment_id is not None:
        parent_result = await db.execute(
            select(RecipeComment).where(RecipeComment.id == parent_comment_id)
        )
        parent = parent_result.scalar_one_or_none()
        if parent is None:
            raise NotFoundError("Parent comment not found")
        if parent.recipe_id != recipe_id:
            raise InvalidStateError("Parent comment belongs to a different recipe")
        if parent.parent_comment_id is not None:
            raise InvalidStateError("Cannot reply to a reply (max 2 levels)")

    comment = RecipeComment(
        recipe_id=recipe_id,
        user_id=user.id,
        parent_comment_id=parent_comment_id,
        content=content,
    )
    db.add(comment)
    await db.flush()  # assign id before notifications

    await _recompute_comments_count(db, recipe_id=recipe_id)

    # Notifications
    from app.services import notification_service

    if parent_comment_id is None:
        # Notify recipe owner about new top-level comment (skip if self-comment)
        if recipe.owner_id is not None and recipe.owner_id != user.id:
            await notification_service.notify_and_broadcast(
                db,
                user_id=recipe.owner_id,
                type="new_comment",
                title=recipe.title,
                message=content[:200],
                recipe_id=recipe_id,
                comment_id=comment.id,
            )
    else:
        # Notify parent comment author about reply
        parent_author_result = await db.execute(
            select(RecipeComment.user_id).where(RecipeComment.id == parent_comment_id)
        )
        parent_author_id = parent_author_result.scalar_one_or_none()
        if parent_author_id is not None and parent_author_id != user.id:
            await notification_service.notify_and_broadcast(
                db,
                user_id=parent_author_id,
                type="comment_reply",
                title=recipe.title,
                message=content[:200],
                recipe_id=recipe_id,
                comment_id=comment.id,
            )

    await db.commit()
    await db.refresh(comment)
    await _bump_caches(cache, recipe_id=recipe_id)

    # Load user for response
    user_result = await db.execute(
        select(RecipeComment)
        .where(RecipeComment.id == comment.id)
        .options(selectinload(RecipeComment.user))
    )
    loaded = user_result.scalar_one()
    return _build_response(loaded, [])


async def soft_delete_comment(
    db: AsyncSession,
    *,
    user: User,
    comment_id: int,
    cache: Cache | None = None,
) -> None:
    """Soft-delete a comment (owner or moderator/admin only).

    Clears content to "" and sets is_deleted=True. Replies remain visible.
    """
    result = await db.execute(
        select(RecipeComment)
        .where(RecipeComment.id == comment_id)
        .options(selectinload(RecipeComment.user))
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise NotFoundError("Comment not found")

    is_owner = comment.user_id == user.id
    is_mod = user.role in ("moderator", "admin")
    if not (is_owner or is_mod):
        raise NotAuthorizedError("Not authorized to delete this comment")

    comment.is_deleted = True
    comment.content = DELETED_CONTENT
    db.add(comment)

    await _recompute_comments_count(db, recipe_id=comment.recipe_id)
    await db.commit()
    await _bump_caches(cache, recipe_id=comment.recipe_id)


async def report_comment(
    db: AsyncSession,
    *,
    reporter: User,
    comment_id: int,
    reason: str,
) -> None:
    """File a report against a comment.

    Raises NotFoundError if the comment doesn't exist.
    Raises ValidationError if the reporter already filed a report on this comment.
    Notifies all moderators/admins via WebSocket.
    """
    comment_result = await db.execute(
        select(RecipeComment)
        .where(RecipeComment.id == comment_id)
        .options(selectinload(RecipeComment.user))
    )
    comment = comment_result.scalar_one_or_none()
    if comment is None:
        raise NotFoundError("Comment not found")

    # Check duplicate report
    existing = await db.execute(
        select(RecipeCommentReport).where(
            RecipeCommentReport.comment_id == comment_id,
            RecipeCommentReport.reporter_id == reporter.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValidationError("already_reported")

    report = RecipeCommentReport(
        comment_id=comment_id,
        reporter_id=reporter.id,
        reason=reason,
    )
    db.add(report)
    await db.flush()

    # Notify all moderators and admins
    from sqlalchemy.future import select as fut_select

    from app.models.user import User as _User
    from app.services import notification_service

    mod_result = await db.execute(
        fut_select(_User.id).where(
            _User.role.in_(["moderator", "admin"]),
            _User.is_active.is_(True),
        )
    )
    mod_ids = [row[0] for row in mod_result.all()]

    recipe_result = await db.execute(select(Recipe).where(Recipe.id == comment.recipe_id))
    recipe = recipe_result.scalar_one_or_none()
    recipe_title = recipe.title if recipe else ""

    try:
        author_name = comment.user.username if comment.user else f"user#{comment.user_id}"
    except Exception:
        author_name = f"user#{comment.user_id}"

    message = (
        f"Reporter: {reporter.username} | Author: {author_name} | "
        f'Comment: "{comment.content[:100]}" | Reason: {reason}'
    )

    if mod_ids:
        await notification_service.notify_bulk_and_broadcast(
            db,
            user_ids=mod_ids,
            type="comment_reported",
            title=recipe_title,
            message=message,
            recipe_id=comment.recipe_id,
        )

    await db.commit()


# Moderation helpers
async def get_reported_comments(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return comments that have at least one report, grouped with aggregated info."""
    from app.models.recipe_comment_report import RecipeCommentReport

    stmt = (
        select(RecipeComment)
        .join(RecipeCommentReport, RecipeCommentReport.comment_id == RecipeComment.id)
        .where(RecipeComment.is_deleted.is_(False))
        .group_by(RecipeComment.id)
        .order_by(func.count(RecipeCommentReport.id).desc(), RecipeComment.created_at.desc())
        .options(selectinload(RecipeComment.user))
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    comments: list[RecipeComment] = list(result.scalars().unique().all())

    if not comments:
        return []

    comment_ids = [c.id for c in comments]

    reports_stmt = (
        select(RecipeCommentReport)
        .where(RecipeCommentReport.comment_id.in_(comment_ids))
        .options(selectinload(RecipeCommentReport.reporter))
        .order_by(RecipeCommentReport.created_at.asc())
    )
    reports_result = await db.execute(reports_stmt)
    all_reports = reports_result.scalars().all()

    reports_map: dict[int, list[RecipeCommentReport]] = {}
    for r in all_reports:
        reports_map.setdefault(r.comment_id, []).append(r)

    recipe_ids = list({c.recipe_id for c in comments})
    recipes_result = await db.execute(
        select(Recipe.id, Recipe.title).where(Recipe.id.in_(recipe_ids))
    )
    recipe_map = {row[0]: row[1] for row in recipes_result.all()}

    parent_ids = [c.parent_comment_id for c in comments if c.parent_comment_id is not None]
    parent_map: dict[int, RecipeComment] = {}
    if parent_ids:
        parents_result = await db.execute(
            select(RecipeComment)
            .where(RecipeComment.id.in_(parent_ids))
            .options(selectinload(RecipeComment.user))
        )
        for p in parents_result.scalars().all():
            parent_map[p.id] = p

    result_list = []
    for comment in comments:
        comment_reports = reports_map.get(comment.id, [])
        parent = parent_map.get(comment.parent_comment_id) if comment.parent_comment_id else None

        try:
            author_username = comment.user.username if comment.user else None
        except Exception:
            author_username = None

        reports_data = []
        for rep in comment_reports:
            try:
                reporter_username = rep.reporter.username if rep.reporter else None
            except Exception:
                reporter_username = None
            reports_data.append({
                "reporter_id": rep.reporter_id,
                "reporter_username": reporter_username,
                "reason": rep.reason,
                "created_at": rep.created_at,
            })

        result_list.append({
            "comment_id": comment.id,
            "recipe_id": comment.recipe_id,
            "recipe_title": recipe_map.get(comment.recipe_id, ""),
            "content": comment.content,
            "is_deleted": comment.is_deleted,
            "author_id": comment.user_id,
            "author_username": author_username,
            "created_at": comment.created_at,
            "parent_comment_id": comment.parent_comment_id,
            "parent_content": parent.content if parent else None,
            "parent_author_username": (
                parent.user.username
                if parent and not parent.is_deleted
                else None
            ),
            "report_count": len(comment_reports),
            "reports": reports_data,
        })

    return result_list


async def dismiss_comment_reports(
    db: AsyncSession,
    *,
    comment_id: int,
) -> None:
    """Delete all reports for a comment without deleting the comment itself."""
    from app.models.recipe_comment_report import RecipeCommentReport

    result = await db.execute(
        select(RecipeComment).where(RecipeComment.id == comment_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Comment not found")

    await db.execute(
        sa_delete(RecipeCommentReport).where(RecipeCommentReport.comment_id == comment_id)
    )
    await db.commit()
