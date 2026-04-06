# claude-pattern_cache

Module: `backend/pattern_cache/`
Role: Fuzzy-matches NL user input against pre-defined query patterns to avoid LLM calls on cache hits. Read-only in Phase 1 — no write API exposed.

## Key Classes

| Class | File | Purpose |
|---|---|---|
| `PatternCache` | `cache/pattern_cache.py` | Top-level facade. Combines TriggerIndex + FuzzyMatcher + stats. Call `lookup()` from request handlers. |
| `TriggerIndex` | `index/trigger_index.py` | Flat in-memory dict: `normalized_trigger → [TriggerEntry]`. Built once at startup, never mutated. |
| `FuzzyMatcher` | `index/fuzzy_matcher.py` | RapidFuzz `token_sort_ratio` scoring. Entity-scoped for precision. |
| `PatternLoader` | `loader/pattern_loader.py` | Discovers + parses `*.patterns.yaml`. Calls `PatternHasher` for skill hash verification. |
| `PatternHasher` | `versioning/hasher.py` | SHA-256 of skill YAML → 16-char hex. Pattern files must declare `# skill_hash: <hash>` on line 1. |
| `PatternPromoter` | `promotion/promoter.py` | **Phase 2 stub** — body is `pass`. Will promote high-confidence LLM patterns to YAML. |

## Data Models (`models/pattern.py`)

| Model | Key Fields |
|---|---|
| `TriggerEntry` | `pattern_id`, `entity`, `normalized_trigger`, raw trigger |
| `MatchResult` | `pattern_id`, `confidence` (0.0–1.0), `matched_trigger`, `entity` |
| `CacheLookupResult` | `tier`, `match` (MatchResult or None), `alternatives` (for did_you_mean) |
| `CacheStats` | `hits`, `misses`, `hit_rate`, `top_patterns` |

## Confidence Tiers (LOCKED — never change thresholds)

| Tier | Confidence | Action |
|---|---|---|
| `direct_execute` | >= 0.95 | Execute QueryPlan immediately |
| `near_miss` | 0.90 – 0.94 | Ask user to confirm pattern match |
| `did_you_mean` | 0.80 – 0.89 | Suggest alternatives to user |
| `cache_miss` | < 0.80 | Fall through to LLM (`QuerySynthesiser`) |

## RapidFuzz Note

RapidFuzz returns scores on **0–100 scale**. Divide by 100 before storing in `MatchResult.confidence`. All internal comparisons use 0.0–1.0.

## Startup Flow

`PatternCache.build_from_pattern_files(pattern_files)` — called from `backend/main.py` after validation:
1. PatternLoader reads YAML, verifies skill hashes
2. TriggerIndex normalizes triggers (lowercase, strip stopwords)
3. Index is sealed — no further writes in Phase 1

## API Endpoints (prefix: `/api/v1`)

| Route | Notes |
|---|---|
| `POST /patterns/match` | Body: `{input, entity?}` → `CacheLookupResult` |
| `GET /patterns/{pattern_id}` | Pattern definition |
| `GET /patterns/entity/{entity}` | All patterns for an entity |
| `GET /patterns/stats` | Hit rate, top misses |

## Critical Rules

- **Read-only in Phase 1**: never expose a write/update endpoint for the pattern cache.
- **Entity-scoped lookup**: always pass `entity` when known — prevents cross-entity false positives.
- **Hash verification**: pattern files with a mismatched `skill_hash` must be rejected (stale patterns for modified skills).
- **Performance gate**: lookup across 5,000 triggers must complete in < 5 ms.
- **Stats logging**: `PatternCache` logs hit rate periodically via structlog — do not remove this.
