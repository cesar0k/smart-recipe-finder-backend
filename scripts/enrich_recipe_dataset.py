"""One-off dataset enrichment: add ingredient amounts + detailed instructions.

For every recipe in datasets/{lang}/recipe_samples.json this script asks the
project's LLM (fal.ai openrouter/router, same client as tag_service) to rewrite:

  * ``ingredients`` — keep the SAME items in the SAME order and language, but
    prefix each with a concrete amount, e.g. "fresh spinach" -> "200 g fresh
    spinach", "яйца" -> "3 яйца".
  * ``instructions`` — a more detailed step-by-step that states how much of
    each ingredient goes in and when.

Everything else (id, title, description, time, difficulty, cuisine,
image_urls) is preserved verbatim. Output is written back to the same file
(or to --out). Recipes whose LLM response fails validation keep their original
content, and are reported at the end so they can be retried.

Usage (inside the app container or any env with FAL_KEY + deps):
    python scripts/enrich_recipe_dataset.py --lang en
    python scripts/enrich_recipe_dataset.py --lang ru --limit 3 --out /tmp/ru_sample.json
    python scripts/enrich_recipe_dataset.py --lang en --concurrency 4
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.recipe.tag_service import (  # noqa: E402
    _fal_call_text,
    _get_fal_key,
    _strip_fences,
)

DATASETS_PATH = Path(__file__).resolve().parents[1] / "datasets"

# Mirror the backend validation limits (RecipeCreate) so we never emit a
# recipe the API would later reject.
MAX_INGREDIENT_LEN = 255
MAX_INGREDIENTS = 100
MAX_INSTRUCTIONS_LEN = 50_000

_SYSTEM_PROMPT = (
    "You are a precise culinary editor. You are given a single recipe as JSON. "
    "Rewrite ONLY two fields and return STRICT JSON with exactly these keys:\n"
    '  "ingredients": array of strings\n'
    '  "instructions": string\n'
    "\n"
    "Rules:\n"
    "1. Keep the SAME ingredients, in the SAME order, and in the SAME language "
    "as the input. Do NOT add or remove ingredients. For each one, prefix a "
    "realistic, concrete amount appropriate to the dish and its serving size "
    "(metric units: g, kg, ml, l, or natural counts like '3 eggs', "
    "'2 cloves garlic'; for things like salt/pepper you may use 'to taste' / "
    "'по вкусу'). Example: 'fresh spinach' -> '200 g fresh spinach'; "
    "'яйца' -> '3 яйца'.\n"
    "2. Rewrite 'instructions' as a clear, numbered, step-by-step that states "
    "HOW MUCH of each ingredient to add and WHEN, consistent with the amounts "
    "you assigned. Keep it in the SAME language as the input. Be practical and "
    "concise — no fluff, no commentary. Separate every step with a single "
    "newline character ('\\n'), and start each step with its number followed "
    "by '. ' (e.g. '1. …\\n2. …').\n"
    "3. Each ingredient string must be at most 255 characters. The whole "
    "instructions string must be at most 50000 characters.\n"
    "Return only the JSON object, nothing else."
)


async def _fal_json(prompt: str, system_prompt: str) -> dict[str, Any]:
    """Call the LLM and robustly parse a JSON object from the reply.

    tag_service._fal_call uses a plain json.loads, which throws "Extra data"
    when the model appends anything after the JSON (a stray newline, a note,
    a second fenced block). Here we strip code fences, then slice from the
    first '{' to its matching '}' and parse only that — tolerating trailing
    junk that broke ~6 RU recipes on the first full run.
    """
    raw = _strip_fences(await _fal_call_text(prompt, system_prompt))

    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM reply")

    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start : i + 1])  # type: ignore[no-any-return]
    raise ValueError("unbalanced JSON object in LLM reply")


def _build_prompt(recipe: dict[str, Any]) -> str:
    payload = {
        "title": recipe.get("title"),
        "cuisine": recipe.get("cuisine"),
        "difficulty": recipe.get("difficulty"),
        "cooking_time_in_minutes": recipe.get("cooking_time_in_minutes"),
        "ingredients": recipe.get("ingredients", []),
        "instructions": recipe.get("instructions", ""),
    }
    return json.dumps(payload, ensure_ascii=False)


def _validate(result: dict[str, Any], original: dict[str, Any]) -> tuple[bool, str]:
    """Validate an LLM result against schema + sanity rules.

    Returns (ok, reason). On failure the caller keeps the original recipe.
    """
    ingredients = result.get("ingredients")
    instructions = result.get("instructions")

    if not isinstance(ingredients, list) or not ingredients:
        return False, "ingredients missing or not a non-empty list"
    if not all(isinstance(i, str) and i.strip() for i in ingredients):
        return False, "ingredient items must be non-empty strings"
    if len(ingredients) > MAX_INGREDIENTS:
        return False, f"too many ingredients ({len(ingredients)})"
    if any(len(i) > MAX_INGREDIENT_LEN for i in ingredients):
        return False, "an ingredient exceeds 255 chars"

    # The LLM must not drop or add ingredients — count must match the original.
    orig_count = len(original.get("ingredients", []))
    if len(ingredients) != orig_count:
        return False, f"ingredient count changed ({orig_count} -> {len(ingredients)})"

    if not isinstance(instructions, str) or not instructions.strip():
        return False, "instructions missing or empty"
    if len(instructions) > MAX_INSTRUCTIONS_LEN:
        return False, "instructions exceed 50000 chars"

    return True, ""


async def _enrich_one(
    recipe: dict[str, Any], sem: asyncio.Semaphore
) -> tuple[dict[str, Any], str | None]:
    """Return (recipe, error). On any failure the ORIGINAL recipe is returned."""
    rid = recipe.get("id")
    title = recipe.get("title", "?")
    async with sem:
        try:
            result = await _fal_json(_build_prompt(recipe), _SYSTEM_PROMPT)
        except Exception as ex:  # noqa: BLE001 — one-off script, log & keep original
            return recipe, f"[{rid}] {title}: LLM call failed: {ex}"

    ok, reason = _validate(result, recipe)
    if not ok:
        return recipe, f"[{rid}] {title}: invalid result ({reason})"

    enriched = dict(recipe)
    enriched["ingredients"] = [i.strip() for i in result["ingredients"]]
    enriched["instructions"] = result["instructions"].strip()
    return enriched, None


async def enrich(
    lang: str,
    limit: int | None,
    out: Path,
    concurrency: int,
    ids: set[int] | None,
) -> None:
    src = DATASETS_PATH / lang / "recipe_samples.json"
    if not src.exists():
        print(f"Error: dataset not found at {src}")
        return

    if not _get_fal_key():
        print("Error: FAL_KEY not set (env or .env). Cannot call the LLM.")
        return

    with open(src, encoding="utf-8") as f:
        recipes: list[dict[str, Any]] = json.load(f)

    if ids is not None:
        # Targeted retry: only re-process the given recipe ids. Used to fix
        # the handful that failed on a full run WITHOUT re-enriching (and
        # double-amounting) the ones that already succeeded.
        target = [r for r in recipes if r.get("id") in ids]
    elif limit is not None:
        target = recipes[:limit]
    else:
        target = recipes
    print(f"Enriching {len(target)}/{len(recipes)} '{lang}' recipes (concurrency={concurrency})…")

    sem = asyncio.Semaphore(concurrency)
    results = await asyncio.gather(*[_enrich_one(r, sem) for r in target])

    enriched_by_id: dict[Any, dict[str, Any]] = {}
    errors: list[str] = []
    for enriched, err in results:
        enriched_by_id[enriched.get("id")] = enriched
        if err:
            errors.append(err)

    # Rebuild the full list, swapping in enriched versions where we have them.
    final = [enriched_by_id.get(r.get("id"), r) for r in recipes]

    with open(out, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
        f.write("\n")

    ok_count = len(target) - len(errors)
    print(f"Done. {ok_count}/{len(target)} enriched, {len(errors)} kept original.")
    print(f"Written to {out}")
    if errors:
        print("\nRecipes kept as original (retry candidates):")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", choices=["en", "ru"], required=True)
    parser.add_argument(
        "--limit", type=int, default=None, help="Only process the first N recipes (dry-run)."
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Output path. Defaults to overwriting the source dataset.",
    )
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated recipe ids to re-process only (targeted retry).",
    )
    args = parser.parse_args()

    ids_set = {int(x) for x in args.ids.split(",") if x.strip()} if args.ids else None

    out_path = Path(args.out) if args.out else DATASETS_PATH / args.lang / "recipe_samples.json"
    asyncio.run(
        enrich(
            lang=args.lang,
            limit=args.limit,
            out=out_path,
            concurrency=args.concurrency,
            ids=ids_set,
        )
    )
