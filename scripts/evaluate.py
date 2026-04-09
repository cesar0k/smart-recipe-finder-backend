import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Sequence

import matplotlib.pyplot as plt
import numpy as np
from alembic.config import Config
from sqlalchemy import String, cast, not_, or_
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.future import select
from sqlalchemy_utils import create_database, database_exists, drop_database

from alembic import command

sys.path.append(os.getcwd())


import matplotlib

from app.core import text_utils
from app.core.security import hash_password
from app.core.vector_store import VectorStore
from app.models.recipe import Recipe
from app.models.user import User
from app.schemas import RecipeCreate
from app.services import recipe_service
from tests.testing_config import testing_settings

matplotlib.use("Agg")

TEST_COLLECTION_NAME = "recipes_test_collection"

BASE_PATH = Path(__file__).resolve().parents[1]

LIMIT_TOP_K = 5


def _print_section_header(title_text: str) -> int:
    width = max(len(title_text), 56)
    print("-" * width)
    print(title_text)
    return width


def _print_separator_line(width: int) -> None:
    print("-" * width)


async def setup_test_db() -> None:
    print("Creating isolated database...")
    if database_exists(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL):
        drop_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)
    create_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", testing_settings.ASYNC_TEST_DATABASE_ADMIN_URL
    )
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")


def teardown_test_db() -> None:
    print("Dropping isolated database...")
    if database_exists(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL):
        drop_database(testing_settings.SYNC_TEST_DATABASE_ADMIN_URL)


async def seed_eval_data(session: AsyncSession, recipes_path: Path) -> None:
    # Create an admin user for seeding (recipes need an owner)
    admin_user = User(
        email="eval-admin@test.local",
        username="eval_admin",
        hashed_password=hash_password("eval_password"),
        role="admin",
    )
    session.add(admin_user)
    await session.commit()
    await session.refresh(admin_user)

    with open(recipes_path) as f:
        recipe_samples = json.load(f)

    print("Seeding recipes into test DB...")
    for r_data in recipe_samples:
        r_input = r_data.copy()
        if "id" in r_input:
            del r_input["id"]

        recipe_in = RecipeCreate(**r_input)

        await recipe_service.create_recipe(
            db=session, recipe_in=recipe_in, current_user=admin_user
        )


async def slow_smart_jsonb_filter(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 100,
    include_str: str | None = None,
    exclude_str: str | None = None,
) -> Sequence[Recipe]:
    query = select(Recipe)

    json_as_text = cast(Recipe.ingredients, String)

    if include_str:
        raw_items = [i.strip() for i in include_str.split(",") if i.strip()]
        for item in raw_items:
            terms = text_utils.get_word_forms(item)

            term_conditions = [json_as_text.op("~*")(f"\\y{term}\\y") for term in terms]
            query = query.where(or_(*term_conditions))

    if exclude_str:
        raw_items = [i.strip() for i in exclude_str.split(",") if i.strip()]
        exclude_conditions = []
        for item in raw_items:
            terms = text_utils.get_word_forms(item)
            for term in terms:
                exclude_conditions.append(json_as_text.op("~*")(f"\\y{term}\\y"))

        if exclude_conditions:
            query = query.where(not_(or_(*exclude_conditions)))

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().unique().all()


async def evaluate_nls_method(
    db: AsyncSession,
    method_name: str,
    search_func: Callable[..., Any],
    queries: list[dict[str, Any]],
    id_to_title: dict[int, str],
) -> dict[str, Any]:
    title = f"Evaluating '{method_name}', top {LIMIT_TOP_K} results are evaluated"
    width = _print_section_header(title)

    passed = 0
    total = len(queries)
    latencies = []
    total_reciprocal_rank = 0.0
    zero_results_count = 0
    total_f1_score = 0.0

    category_stats: dict[str, dict[str, Any]] = {}

    for q in queries:
        query_text = q["query"]

        expected_ids = q.get("expected_ids")
        if expected_ids is None:
            expected_ids = [q["expected_id"]] if "expected_id" in q else []

        expected_titles = {
            id_to_title.get(eid) for eid in expected_ids if id_to_title.get(eid)
        }
        category = q.get("category", "unknown")

        if category not in category_stats:
            category_stats[category] = {"total": 0, "passed": 0, "total_f1": 0.0}

        start_time = time.time()
        results = await search_func(db=db, query_str=query_text)
        end_time = time.time()
        latencies.append((end_time - start_time) * 1000)

        results = results[:LIMIT_TOP_K]

        if not results:
            zero_results_count += 1

        found_titles = [r.title for r in results]
        found_titles_set = set(found_titles)

        intersection = expected_titles.intersection(found_titles_set)
        is_passed = len(intersection) > 0

        category_stats[category]["total"] += 1
        rank = 0
        if is_passed:
            for i, title in enumerate(found_titles):
                if title in expected_titles:
                    rank = i + 1
                    break
        if rank > 0:
            passed += 1
            category_stats[category]["passed"] += 1
            total_reciprocal_rank += 1.0 / rank

        relevant_retrieved = len(intersection)
        total_retrieved = len(found_titles)
        total_relevant_in_db = len(expected_titles)

        precision = (
            (relevant_retrieved / total_retrieved) if total_retrieved > 0 else 0.0
        )
        recall = (
            (relevant_retrieved / total_relevant_in_db)
            if total_relevant_in_db > 0
            else 0.0
        )

        if (precision + recall) > 0:
            f1 = 2 * (precision * recall) / (precision + recall)
        else:
            f1 = 0.0

        total_f1_score += f1
        category_stats[category]["total_f1"] += f1

    accuracy = (passed / total) * 100
    avg_latency = mean(latencies)
    mean_reciprocal_rank = total_reciprocal_rank / total if total > 0 else 0.0
    zero_result_rate = (zero_results_count / total) * 100 if total > 0 else 0.0
    avg_f1_score = total_f1_score / total if total > 0 else 0.0

    print("By category:")
    for cat, stats in category_stats.items():
        cat_acc = (stats["passed"] / stats["total"]) * 100
        cat_f1 = stats["total_f1"] / stats["total"]
        print(
            f" - {cat:32}: {cat_acc:6.2f}% | F1: {cat_f1:.4f} "
            f"({stats['passed']}/{stats['total']})"
        )
    print(f"Overall Accuracy: {accuracy}% ({passed}/{total})")
    print(f"Average latency: {avg_latency:.5f} ms")
    print(f"Mean Reciprocal Rank (MRR): {mean_reciprocal_rank:.5f}")
    print(f"Zero Result Rate (ZRR): {zero_result_rate:.2f}%")
    print(f"Average F1-Score: {avg_f1_score:.4f}")
    _print_separator_line(width)
    print()

    return {
        "method": method_name,
        "accuracy": accuracy,
        "mean_reciprocal_rank": mean_reciprocal_rank,
        "zero_result_rate": zero_result_rate,
        "avg_f1_score": avg_f1_score,
        "avg_latency": avg_latency,
    }


async def evaluate_filters(
    db: AsyncSession,
    method_name: str,
    filter_func: Callable[..., Any],
    filter_queries: list[dict[str, Any]],
    iterations: int = 50,
) -> dict[str, Any]:
    title = f"Evaluating filter with {method_name}"
    width = _print_section_header(title)

    passed = 0
    total = len(filter_queries)
    latencies = []

    if filter_queries:
        warmup_case = filter_queries[0]
        for _ in range(5):
            await filter_func(
                db=db,
                include_str=warmup_case["include_ingredients"],
                exclude_str=warmup_case["exclude_ingredients"],
            )

    for case in filter_queries:
        start_time = time.time()
        for _ in range(iterations):
            results = await filter_func(
                db=db,
                include_str=case["include_ingredients"],
                exclude_str=case["exclude_ingredients"],
            )
        end_time = time.time()

        batch_duration_ms = (end_time - start_time) * 1000

        latency_per_query = batch_duration_ms / iterations
        latencies.append(latency_per_query)

        found_titles = {r.title for r in results}
        expected = set(case.get("should_contain", []))
        unwanted = set(case.get("should_not_contain", []))

        missing = expected - found_titles
        found_unwanted = found_titles.intersection(unwanted)

        if not missing and not found_unwanted:
            passed += 1
        else:
            print(f" Filter testcase {case['id']} failed.")
            if missing:
                print(f"  - missing: {missing}")
            if found_unwanted:
                print(f"  - found unwanted: {found_unwanted}")

    accuracy = (passed / total) * 100 if total > 0 else 0
    avg_latency = mean(latencies) if latencies else 0

    print(f"Filter accuracy: {accuracy:.2f}% ({passed}/{total} queries passed)")
    print(f"Average latency: {avg_latency:.5f} ms")
    _print_separator_line(width)
    print()

    return {"method": method_name, "accuracy": accuracy, "avg_latency": avg_latency}


def check_quality_gates(method_name: str, results: dict[str, Any]) -> bool:
    if method_name not in testing_settings.THRESHOLDS:
        return True

    limits = testing_settings.THRESHOLDS[method_name]
    is_passed = True

    print(f"Quality gate for {method_name}")

    metrics_higher = ["accuracy", "avg_f1_score", "mean_reciprocal_rank"]
    metrics_lower = ["zero_result_rate", "latency", "avg_latency"]

    for metric, target in limits.items():
        if metric not in results:
            continue

        actual = results[metric]

        status = "PASS"

        if metric in metrics_higher:
            if actual < target:
                status = "FAIL"
                is_passed = False
            print(f"  {status} {metric}: {actual:.4f} >= {target}")

        elif metric in metrics_lower:
            if actual > target:
                status = "FAIL"
                is_passed = False
            print(f"  {status} {metric}: {actual:.4f} <= {target}")

    return is_passed


def plot_evaluation_results(
    nls_results: list[dict[str, Any]], filter_results: list[dict[str, Any]]
) -> None:
    print("\nGenerating evaluation charts...")

    plt.style.use("ggplot")

    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(22, 6))
    fig.suptitle("Search & Filter evaluation results", fontsize=16)

    methods = [r["method"] for r in nls_results]
    accuracy = [r["accuracy"] for r in nls_results]
    f1 = [r["avg_f1_score"] * 100 for r in nls_results]
    mrr = [r["mean_reciprocal_rank"] * 100 for r in nls_results]
    zrr = [r["zero_result_rate"] for r in nls_results]

    x = np.arange(len(methods))
    width = 0.2

    ax1.bar(x - 1.5 * width, accuracy, width, label="Accuracy (%)", color="#3498db")
    ax1.bar(x - 0.5 * width, f1, width, label="F1-Score", color="#9b59b6")
    ax1.bar(x + 0.5 * width, mrr, width, label="MRR", color="#2ecc71")
    ax1.bar(x + 1.5 * width, zrr, width, label="Zero Result Rate (%)", color="#e14747")

    ax1.set_ylabel("Score")
    ax1.set_title("Search Quality")
    ax1.set_xticks(x)
    ax1.set_xticklabels(methods, rotation=15)
    ax1.legend()
    ax1.set_ylim(0, 110)

    for i, v in enumerate(accuracy):
        ax1.text(
            x[i] - 1.5 * width,
            v + 1.5,
            f"{v:.1f}%",
            ha="center",
            color="#3498db",
            fontsize=8,
        )
    for i, v in enumerate(f1):
        ax1.text(
            x[i] - 0.5 * width,
            v + 1.5,
            f"{v / 100:.4f}",
            ha="center",
            color="#9b59b6",
            fontsize=8,
        )
    for i, v in enumerate(mrr):
        ax1.text(
            x[i] + 0.5 * width,
            v + 1.5,
            f"{v / 100:.4f}",
            ha="center",
            color="#2ecc71",
            fontsize=8,
        )
    for i, v in enumerate(zrr):
        ax1.text(
            x[i] + 1.5 * width,
            v + 1.5,
            f"{v:.1f}%",
            ha="center",
            color="#e14747",
            fontsize=8,
        )

    f_methods = [r["method"] for r in filter_results]
    f_accuracy = [r["accuracy"] for r in filter_results]

    ax2.bar(f_methods, f_accuracy, color=["#e74c3c", "#27ae60"])
    ax2.set_title("Filter Accuracy")
    ax2.set_ylabel("Accuracy (%)")
    ax2.set_ylim(0, 110)
    for i, v in enumerate(f_accuracy):
        ax2.text(i, v + 1.5, f"{v:.1f}%", ha="center")

    all_methods = methods + f_methods
    all_latencies = [r["avg_latency"] for r in nls_results] + [
        r["avg_latency"] for r in filter_results
    ]

    colors = ["#3498db"] * len(nls_results) + ["#e67e22"] * len(filter_results)

    ax3.bar(all_methods, all_latencies, color=colors)
    ax3.set_title("Average Latency (Log Scale)")
    ax3.set_ylabel("Time (ms)")
    ax3.set_yscale("log")
    ax3.set_xticklabels(all_methods, rotation=45, ha="right")

    output_path = "evaluation_results.png"
    plt.tight_layout()
    plt.savefig(output_path)
    print(f"Charts saved to {output_path}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate search and filter methods.")
    parser.add_argument(
        "--lang",
        type=str,
        default="en",
        choices=["en", "ru"],
        help="Language of the dataset to use for evaluation.",
    )
    args = parser.parse_args()
    lang = args.lang
    print(f"Using '{lang}' language for evaluation.")

    datasets_path = BASE_PATH / "datasets" / lang
    nls_queries_path = datasets_path / "evaluation_nls_queries.json"
    filter_queries_path = datasets_path / "filter_test_data.json"
    recipes_path = datasets_path / "recipe_samples.json"

    await setup_test_db()

    eval_vector_store = VectorStore(
        collection_name=TEST_COLLECTION_NAME, force_new=True
    )
    original_vector_store = recipe_service.vector_store
    recipe_service.vector_store = eval_vector_store

    engine = create_async_engine(testing_settings.ASYNC_TEST_DATABASE_ADMIN_URL)
    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

    nls_results: list[dict[str, Any]] = []
    filter_results: list[dict[str, Any]] = []

    success = True

    try:
        async with SessionLocal() as db:
            await seed_eval_data(db, recipes_path)

            with open(nls_queries_path) as f:
                nls_queries = json.load(f)
            with open(filter_queries_path) as f:
                filter_queries = json.load(f)
            with open(recipes_path) as f:
                recipes = json.load(f)

            id_to_title = {r["id"]: r["title"] for r in recipes}

            vec_res = await evaluate_nls_method(
                db,
                "Vector Search",
                recipe_service.search_recipes_by_vector,
                nls_queries,
                id_to_title,
            )
            nls_results.append(vec_res)
            if not check_quality_gates("Vector Search", vec_res):
                success = False

            jsonb_fast_smart_fil_res = await evaluate_filters(
                db,
                "JSONB GIN Filter",
                recipe_service.get_all_recipes,
                filter_queries,
            )
            filter_results.append(jsonb_fast_smart_fil_res)
            if not check_quality_gates("JSONB GIN Filter", jsonb_fast_smart_fil_res):
                success = False

            jsonb_slow_smart_fil_res = await evaluate_filters(
                db,
                "Slow JSONB Accurate Word Boundary Filter",
                slow_smart_jsonb_filter,
                filter_queries,
            )
            filter_results.append(jsonb_slow_smart_fil_res)

            plot_evaluation_results(nls_results, filter_results)
    finally:
        print("Cleaning up...")
        await engine.dispose()
        recipe_service.vector_store = original_vector_store

        try:
            eval_vector_store.client.delete_collection(TEST_COLLECTION_NAME)
        except Exception:
            pass

        teardown_test_db()
        print("Cleaned up.")

    if not success:
        print("Evaluation tests FAILED.")
        sys.exit(1)

    print("Evaluation tests PASSED.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
