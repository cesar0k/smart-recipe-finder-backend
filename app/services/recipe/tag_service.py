"""LLM-based recipe tag classification and query intent parsing.

Two entry points:

classify_recipe_tags(recipe_id)
    Background task — called after recipe create/update.
    Opens its own DB session, calls Gemini via fal.ai, upserts RecipeTags.
    Graceful degradation: logs warning on failure, never raises.

parse_query_intent(query)
    Called inline during vector search.
    Returns a tag filter dict (e.g. {"vegetarian": True}) or {} / None on error.
    Hard 3-second timeout to avoid blocking search.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

_FAL_APP = "openrouter/router"


def _get_llm_model() -> str:
    """Read LLM model from settings (allows override via .env)."""
    from app.core.config import settings

    return settings.LLM_MODEL


# Tag classification JSON schema description (for LLM system prompt)
_TAG_SYSTEM_PROMPT = (
    "You are a culinary data annotator. Classify the recipe and return ONLY a JSON object.\n"
    "\nFields:\n"
    "- vegetarian: bool (true if no meat/fish/seafood)\n"
    "- vegan: bool (true if no animal products at all)\n"
    "- gluten_free: bool (true if no wheat/barley/rye)\n"
    "- dairy_free: bool (true if no milk/cheese/butter/cream)\n"
    "- meal_type: one of breakfast|lunch|dinner|dessert|snack|soup|salad|side|drink|other\n"
    "- main_protein: one of chicken|beef|pork|fish|seafood|lamb|tofu|legumes|eggs|none\n"
    "- allergens: array, any of: nuts|peanuts|eggs|dairy|shellfish|fish|soy|gluten|sesame\n"
    "- cooking_method: one of baked|fried|grilled|steamed|boiled|raw|roasted|stewed"
    "|slow_cooked|pressure_cooked|no_cook|other\n"
    "- spice_level: one of none|mild|medium|hot|very_hot\n"
    "- occasion: one of everyday|party|holiday|romantic|kids_friendly"
    "|meal_prep|picnic|barbecue|brunch\n"
    "- cost_tier: one of budget|moderate|premium\n"
    "- technique_difficulty: one of basic|intermediate|advanced\n"
    '- cultural_sub_region: string or null (e.g. "Tuscany", "Sichuan", "Kerala", "Belarus")\n'
    '- source: always "llm"'
)

_QUERY_INTENT_SYSTEM_PROMPT = """You are a search query analyzer for a recipe search engine.
Analyze the query and extract dietary/tag constraints the user wants.
Return ONLY a JSON object with the constraints found, or an empty object {} if none.

Possible constraint fields (only include if clearly implied):
- vegetarian: true (e.g. "без мяса", "vegetarian", "вегетарианское")
- vegan: true (e.g. "веганское", "vegan", "без животных продуктов")
- gluten_free: true (e.g. "без глютена", "gluten free")
- dairy_free: true (e.g. "без молока", "без молочки", "dairy free")
- spice_level: ["hot","very_hot"] (e.g. "острый", "spicy") or ["none","mild"] ("не острый")
- meal_type: string (e.g. "суп"→"soup", "завтрак"→"breakfast", "десерт"→"dessert")
- main_protein: string (e.g. "курица"→"chicken", "без мяса"→"none")

Return {} if the query is a simple food name with no dietary restrictions implied.
Examples:
  "рецепты без мяса" → {"vegetarian": true, "main_protein": "none"}
  "острый суп" → {"spice_level": ["hot","very_hot"], "meal_type": "soup"}
  "веганский десерт" → {"vegan": true, "meal_type": "dessert"}
  "борщ" → {}
  "chocolate cake" → {}
"""


# ── FAL client helper ─────────────────────────────────────────────────────────


def _get_fal_key() -> str | None:
    key = os.environ.get("FAL_KEY", "")
    if not key:
        from pathlib import Path

        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("FAL_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["FAL_KEY"] = key
                    break
    return key or None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1 :] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text.strip()


async def _fal_call(prompt: str, system_prompt: str) -> dict[str, Any]:
    """Single async fal.ai call returning a JSON object. Raises on error."""
    import fal_client

    result = await fal_client.run_async(
        _FAL_APP,
        arguments={
            "model": _get_llm_model(),
            "system_prompt": system_prompt,
            "prompt": prompt,
            "response_format": json.dumps({"type": "json_object"}),
        },
    )
    raw = _strip_fences(result["output"])
    return json.loads(raw)  # type: ignore[no-any-return]


async def _fal_call_text(prompt: str, system_prompt: str) -> str:
    """Single async fal.ai call returning plain text. Raises on error."""
    import fal_client

    result = await fal_client.run_async(
        _FAL_APP,
        arguments={
            "model": _get_llm_model(),
            "system_prompt": system_prompt,
            "prompt": prompt,
        },
    )
    return str(result["output"]).strip()


# ── Recipe tag classification ─────────────────────────────────────────────────


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
async def _classify_with_retry(recipe_prompt: str) -> dict[str, Any]:
    return await _fal_call(recipe_prompt, _TAG_SYSTEM_PROMPT)


async def classify_recipe_tags(recipe_id: int) -> None:
    """Background task: classify tags for a single recipe and upsert to DB.

    Opens its own session — safe to call from BackgroundTasks (after response sent).
    Silently logs and returns on any failure; never raises.
    """
    if not _get_fal_key():
        log.warning(  # noqa: E501
            "tag_service: FAL_KEY not set, skipping tag classification for recipe %d",
            recipe_id,
        )
        return

    try:
        from sqlalchemy.future import select
        from sqlalchemy.orm import selectinload

        from app.db.session import AsyncSessionLocal
        from app.models.recipe.recipe import Recipe
        from app.models.recipe.recipe_tags import RecipeTags

        async with AsyncSessionLocal() as db:
            from app.models.recipe.recipe_ingredient import RecipeIngredient

            result = await db.execute(
                select(Recipe)
                .where(Recipe.id == recipe_id)
                .options(
                    selectinload(Recipe.cuisine_ref),
                    selectinload(Recipe.recipe_ingredients).selectinload(
                        RecipeIngredient.ingredient
                    ),
                )
            )
            recipe = result.scalar_one_or_none()
            if recipe is None:
                log.warning("tag_service: recipe %d not found, skipping", recipe_id)
                return

            ingredients = ", ".join(item.get("name", "") for item in (recipe.ingredients or []))
            prompt = (
                f"Title: {recipe.title}\n"
                f"Description: {recipe.description or ''}\n"
                f"Ingredients: {ingredients}\n"
                f"Cuisine: {recipe.cuisine or ''}\n"
                f"Difficulty: {recipe.difficulty}"
            )

            tags_data = await _classify_with_retry(prompt)
            tags_data["source"] = "llm"
            tags_data["recipe_id"] = recipe_id

            # Upsert: update if exists, insert if not
            existing = await db.execute(select(RecipeTags).where(RecipeTags.recipe_id == recipe_id))
            row = existing.scalar_one_or_none()

            # Sanitize allergens — must be a list
            allergens = tags_data.get("allergens", [])
            if not isinstance(allergens, list):
                allergens = []

            if row is None:
                row = RecipeTags(
                    recipe_id=recipe_id,
                    vegetarian=tags_data.get("vegetarian"),
                    vegan=tags_data.get("vegan"),
                    gluten_free=tags_data.get("gluten_free"),
                    dairy_free=tags_data.get("dairy_free"),
                    meal_type=tags_data.get("meal_type"),
                    main_protein=tags_data.get("main_protein"),
                    cooking_method=tags_data.get("cooking_method"),
                    spice_level=tags_data.get("spice_level"),
                    occasion=tags_data.get("occasion"),
                    cost_tier=tags_data.get("cost_tier"),
                    technique_difficulty=tags_data.get("technique_difficulty"),
                    cultural_sub_region=tags_data.get("cultural_sub_region"),
                    allergens=allergens,
                    source="llm",
                )
                db.add(row)
            else:
                row.vegetarian = tags_data.get("vegetarian")
                row.vegan = tags_data.get("vegan")
                row.gluten_free = tags_data.get("gluten_free")
                row.dairy_free = tags_data.get("dairy_free")
                row.meal_type = tags_data.get("meal_type")
                row.main_protein = tags_data.get("main_protein")
                row.cooking_method = tags_data.get("cooking_method")
                row.spice_level = tags_data.get("spice_level")
                row.occasion = tags_data.get("occasion")
                row.cost_tier = tags_data.get("cost_tier")
                row.technique_difficulty = tags_data.get("technique_difficulty")
                row.cultural_sub_region = tags_data.get("cultural_sub_region")
                row.allergens = allergens
                row.source = "llm"

            await db.commit()
            log.info("tag_service: classified tags for recipe %d (%s)", recipe_id, recipe.title)

            # Re-index the embedding now that tags are available.
            # The document text includes tag keywords (vegetarian, soup, etc.) which
            # significantly improves semantic search quality for tag-related queries.
            try:
                from app.core.vector_store import vector_store
                from app.services.recipe.recipe_service import _create_semantic_document

                # Reload recipe with tags attached so _create_semantic_document sees them
                await db.refresh(recipe)
                recipe.tags = row  # attach in-memory so lazy="noload" doesn't return None
                doc_text, metadata = _create_semantic_document(recipe)
                await vector_store.upsert_recipe(
                    recipe_id=recipe_id,
                    title=recipe.title,
                    full_text=doc_text,
                    metadata=metadata,
                )
                log.info("tag_service: re-indexed embedding for recipe %d with tags", recipe_id)
            except Exception as embed_exc:
                # Non-fatal: embedding without tags still works, just less accurate
                log.warning(
                    "tag_service: failed to re-index recipe %d embedding: %s",
                    recipe_id,
                    embed_exc,
                )

    except Exception as exc:
        log.warning(
            "tag_service: failed to classify recipe %d after retries: %s",
            recipe_id,
            exc,
        )


# ── Query intent parsing ──────────────────────────────────────────────────────


async def parse_query_intent(query: str) -> dict[str, Any] | None:
    """Parse a search query for dietary/tag constraints.

    Returns:
        dict with tag filters (may be empty {}) — no filters found.
        None — LLM unavailable or timeout; caller should skip tag filtering.
    """
    if not _get_fal_key():
        return None

    try:
        result = await asyncio.wait_for(
            _fal_call(f'Query: "{query}"', _QUERY_INTENT_SYSTEM_PROMPT),
            timeout=3.0,
        )
        # Validate it's a dict (not a list or primitive)
        if not isinstance(result, dict):
            return {}
        return result
    except asyncio.TimeoutError:
        log.debug("tag_service: parse_query_intent timed out for %r", query)
        return None
    except Exception as exc:
        log.debug("tag_service: parse_query_intent failed for %r: %s", query, exc)
        return None


# ── Query rewriting ───────────────────────────────────────────────────────────

_REWRITE_SYSTEM_PROMPT = """\
You are a culinary search assistant. Your job is to rewrite a user's search query \
into a clear, concrete culinary description that will work well for semantic similarity \
search over a recipe database.

Rules:
- Translate abstract, metaphorical, scientific, or colloquial terms into plain cooking language.
- Expand ingredient synonyms: "казеин" → "творог", "allium" → "лук чеснок", \
"brassica" → "капуста брокколи".
- Describe processes concretely: "карамелизованный" → "обжаренный до золотистой корочки с сахаром".
- If the query already uses plain cooking language, return it unchanged.
- Return ONLY the rewritten query as plain text, no explanation, no quotes.
- Keep the same language as the input (Russian stays Russian, English stays English).
- Maximum 2 sentences.

Examples:
  "казеиновый белок" → "рецепты из творога, сыра или молочных продуктов"
  "блюда для восстановления после тренировки" → "высокобелковое блюдо из курицы яиц или бобовых"
  "прокисшее тесто" → "рецепты на кислом или дрожжевом тесте"
  "борщ" → "борщ"
  "pasta carbonara" → "pasta carbonara"\
"""


async def rewrite_query(query: str) -> str | None:
    """Rewrite an abstract/tricky query into plain culinary language for better vector search.

    Returns the rewritten query string, or None when the LLM is unavailable/timeout.
    If the query is already plain, the LLM returns it unchanged.
    Hard 3-second timeout — callers fall back to the original query on None.
    """
    if not _get_fal_key():
        return None

    try:
        result = await asyncio.wait_for(
            _fal_call_text(f'Query: "{query}"', _REWRITE_SYSTEM_PROMPT),
            timeout=3.0,
        )
        rewritten = result.strip().strip('"').strip("'")
        if not rewritten or rewritten == query:
            return None  # unchanged — no point substituting
        log.debug("tag_service: rewrite %r → %r", query, rewritten)
        return rewritten
    except asyncio.TimeoutError:
        log.debug("tag_service: rewrite_query timed out for %r", query)
        return None
    except Exception as exc:
        log.debug("tag_service: rewrite_query failed for %r: %s", query, exc)
        return None
