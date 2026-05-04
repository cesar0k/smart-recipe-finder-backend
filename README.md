# Smart Recipe Finder API

This is the backend service for the "Smart Recipe Finder" application, built as part of a technical assessment. It provides a RESTful API for managing and searching recipes, all running within a Docker environment.

Frontend – [Smart Recipe Finder Frontend](https://github.com/cesar0k/smart-recipe-finder-frontend)

## Tech Stack

- **Language:** Python 3.12
- **Framework:** FastAPI
- **Database:** PostgreSQL 17
- **Vector Search:** ChromaDB
- **ORM:** SQLAlchemy
- **Data Validation:** Pydantic v2
- **Containerization:** Docker & Docker Compose

## Architecture

The codebase follows a **thin controller / fat service** layering: HTTP endpoints in `app/api/v1/endpoints/` only translate the HTTP request, delegate to a service, and serialize the response. All business rules — caching, authorization, multi-step orchestration — live inside the corresponding service module.

### Layers

- **API layer** (`app/api/v1/`) — request parsing, dependency injection (`current_user`, `cache`, `db`), DTO mapping. No `if`-driven business logic, no direct cache calls, no inline ownership checks.
- **Service layer** (`app/services/`) — orchestrates models, vector store, S3, cache, notifications. Mutating functions accept `cache: Cache | None = None` so they're usable from HTTP endpoints, background workers, and CLI scripts (`seed_db.py`) alike.
- **Domain exceptions** (`app/core/exceptions.py`) — `NotFoundError`, `NotAuthorizedError`, `InvalidStateError`, `ValidationError`, `InvalidCredentialsError`. Services raise these instead of `HTTPException`. Global FastAPI handlers in `app/main.py` map them to 404 / 403 / 400 / 409 / 401. This keeps services framework-agnostic.

### Cache invalidation policy

Each writer in the service layer owns the bumps for caches it can invalidate:

- `recipe_service.create_recipe / update_recipe / delete_recipe / resubmit_recipe / upload_recipe_images / delete_recipe_images` → bump search + similar versions and invalidate per-recipe keys.
- `moderation_service.moderate_recipe / moderate_draft` → same plus `invalidate_on_moderation`.
- `user_service.update_user / delete_user / upload_avatar`, `auth_service.update_user_profile` → `invalidate_on_user_change`.

Read-through cache wrappers (`get_distinct_cuisines_cached`, `get_recipe_for_caller`, `get_pending_count_cached`, `get_public_profile_cached`) live next to the underlying queries — endpoints call them as one-liners.

## Development Environment

This project includes a `.devcontainer` configuration, allowing you to open and run the entire development environment in a Docker container using VS Code with the "Dev Containers" extension. This ensures a consistent and reproducible development setup.

## Prerequisites

Before you begin, ensure you have [Docker Desktop](https.www.docker.com/products/docker-desktop/) installed on your system.

## Getting Started

Follow these steps to get the application running locally.

### 1. Clone the Repository

```bash
git clone https://github.com/cesar0k/smart-recipe-finder-backend.git
cd smart-recipe-finder
```

### 2. Set Up Environment Variables

Create a local environment file by copying the provided example.

**Linux / macOS:**

```bash
cp .env.example .env
```

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
```

Important: the application relies on vector search powered by an LLM backend — this requires a Hugging Face API token. Add the token to .env.example (or your local .env) as shown below:

```dotenv
HUGGINGFACE_API_TOKEN=<your_huggingface_token_here>
```

If you need to change the application or database ports because they are already in use on your machine, you can do so by editing the APP_PORT and DB_EXTERNAL_PORT variables in your local .env file.

### 3. Build and Run the Application

Use Docker Compose to build the images and start the services in detached mode (`-d`).

```bash
docker compose up --build -d
```

The API service will now be running and accessible at `http://localhost:8001`.

## API Documentation

Once the application is running, the interactive API documentation (powered by Swagger UI) is available at:

[**http://localhost:8001/docs**](http://localhost:8001/docs)

You can use this interface to explore, test, and interact with all the available API endpoints.

## Testing

A comprehensive test suite has been newly implemented to ensure the reliability and correctness of the API endpoints. The tests run against a dedicated, isolated test database to ensure that test execution does not interfere with development data.

To run the test suite, you first need to set up your test environment.

### 1. Install Dependencies (Optional)

Dependencies are already installed inside the Docker container. You only need to run this step if you intend to execute tests or scripts directly on your host machine (outside the Docker container):

```bash
uv sync
```

### 2. Run Tests

The project includes a comprehensive test suite covering various functionalities. You can run different sets of tests as follows:

#### Full Tests

To run the entire test suite, execute `pytest` without any specific markers:

```bash
docker compose exec app pytest
```

#### Smoke Tests

Smoke tests are a subset of tests designed to quickly verify that the most important functions of the application are working correctly. To run only the smoke tests execute:

```bash
docker compose exec app pytest -m smoke
```

#### CRUD Tests

CRUD tests cover the full lifecycle of recipe management operations (Create, Read, Update, Delete). Unlike smoke tests, this suite performs a comprehensive check of the API functionality, including edge cases, partial updates, and error handling. To run the full functional test suite execute:

```bash
docker compose exec app pytest -m crud
```

#### Evaluation Tests

Evaluation tests are designed to assess specific aspects of the application, often involving dedicated datasets or complex scenarios. To run only the evaluation tests execute:

```bash
docker compose exec app pytest -m eval
```

> **Note:** the test suite loads the embedding model into the same container as the live backend. On memory-constrained setups the backend may become unresponsive after tests finish. If that happens, restart the app:
>
> ```bash
> docker compose restart app
> ```

## Seeding the Database

To populate your database with sample recipe data, you can use the `seed_db.py` script. This is particularly useful for development and testing purposes.

The script can seed the database with recipes in different languages. Use the `--lang` flag to specify the language. Supported languages are `en` (English) and `ru` (Russian).

### English Seeding (Default)

To seed the database with English recipes, run the following command:

```bash
docker compose exec app python scripts/seed_db.py --lang en
```

If you don't specify a language, it will default to English.

### Russian Seeding

To seed the database with Russian recipes, run:

```bash
docker compose exec app python scripts/seed_db.py --lang ru
```

The script will clear existing recipes and add a predefined set to your database, which you can then query via the API.

> **Note:** the seed script loads the embedding model into the same container as the live backend. On memory-constrained setups the backend may become unresponsive after the script finishes. If that happens, restart the app:
>
> ```bash
> docker compose restart app
> ```

## Admin Management

The `scripts/create_admin.py` CLI manages the admin account. Use it to create the first admin or to transfer the admin role to another user.

### Create an admin

**Linux / macOS:**

```bash
docker compose exec app python scripts/create_admin.py create \
    --email admin@example.com \
    --username admin \
    --password 'secure_password'
```

**Windows (PowerShell):**

```powershell
docker compose exec app python scripts/create_admin.py create `
    --email admin@example.com `
    --username admin `
    --password 'secure_password'
```

### Transfer admin to another user

**Linux / macOS:**

```bash
docker compose exec app python scripts/create_admin.py transfer \
    --to username_or_email
```

**Windows (PowerShell):**

```powershell
docker compose exec app python scripts/create_admin.py transfer `
    --to username_or_email
```

## Search Capabilities

### Vector Search

The application implements vector search using ChromaDB to find semantically similar recipes. This allows for more "natural language" queries (e.g., "healthy chicken dishes for dinner") and finds recipes that are conceptually related, even if they don't share exact keywords.

## Evaluation & Benchmarking

One of the core goals of this project is to quantitatively compare different search and filtering methods.

### Metrics Implemented

- **Accuracy:** Percentage of queries where the target recipe was found.
- **Latency:** Average execution time per query.
- **Mean Reciprocal Rank (MRR):** Measures ranking quality (how high the relevant recipe appears).
- **ZRR (Zero Result Rate):** Percentage of queries returning no results.

### How to Run Benchmarks

The evaluation script can be run for different languages. Use the `--lang` flag to specify which dataset to use.

**Run Evaluation Script (English - Default)**:

```bash
docker compose exec app python scripts/evaluate.py --lang en
```

**Run Evaluation Script (Russian)**:

```bash
docker compose exec app python scripts/evaluate.py --lang ru
```

This will run a series of tests and generate a performance comparison chart.

> **Note:** the evaluation script loads the embedding model into the same container as the live backend. On memory-constrained machines the backend may become unresponsive after the script finishes. If that happens, restart the app:
>
> ```bash
> docker compose restart app
> ```

### Redis Cache Benchmark

Vector search is the most expensive operation in the API: each call runs the embedding model and a ChromaDB similarity query. We cache the vector-search IDs in Redis with version-scoped keys; the script below measures the impact.

```bash
docker compose exec app python scripts/benchmark_search.py --iterations 10
```

The script calls `recipe_service.search_recipes_by_vector()` directly (same approach as `evaluate.py`) to isolate the cache impact from the HTTP stack. Two scenarios are compared:

- **cold** — forces a cache MISS on every call by bumping `search:version`.
- **warm** — pre-warms the cache, then hits it on every subsequent call.

For each scenario the script collects latency percentiles (p50/p95/p99) and reads `VectorStore.search_calls_count` — the total number of times the heavy embedding + ChromaDB pipeline actually ran.

**Representative result** (5 iterations × 10 queries = 50 calls per scenario, local Docker, laptop CPU):

| Scenario | calls | `vector_store.search()` | p50    | p95     | p99      | avg    |
|----------|-------|-------------------------|--------|---------|----------|--------|
| cold     | 50    | **50**                  | 307 ms | 2683 ms | 11463 ms | 975 ms |
| warm     | 50    | **0**                   | 2 ms   | 2 ms    | 3 ms     | 2 ms   |

- **Speedup:** ~500× on average, ~1000× on p95.
- **Backend load:** 100% of expensive vector-search invocations eliminated on repeated queries.
- Chart: `benchmark_results.png` (generated next to the script).

> **Note:** same caveat as `evaluate.py` — the benchmark loads the embedding model inside the app container, which can leave the backend unresponsive on memory-constrained machines. If that happens, restart the app:
>
> ```bash
> docker compose restart app
> ```

## Visual Results

Upon running the evaluation script, a graph file **`evaluation_results.png`** will be generated in the project root.

The cache benchmark produces a separate file **`benchmark_results.png`** with latency histograms and percentile bars for cold vs. warm runs.

## Project Status

Currently implemented features:

- [x] Project setup with Docker and a scalable layered architecture.
- [x] **Create** and **Read** (by ID and list all) operations for recipes.
- [x] **Update** and **Delete** operations for recipes.
- [x] **Smart Filtering Logic** (Refactored).
- [x] Vector Search Implementation.
- [x] Comprehensive test suite with an isolated database.
- [x] Script for evaluating search and filtering methods with **graphical** representation in the form of a file.
- [x] Devcontainer for a consistent development environment.
