# Changelog

All notable changes to this project are documented here.

## 0.2.0 - 2026-10-17

Fork of [sepehr071/snapp-mcp](https://github.com/sepehr071/snapp-mcp), maintained at [A512-dev/snapp-mcp](https://github.com/A512-dev/snapp-mcp).

### Added

- `food_menu_descriptions`: a restaurant's menu with Snappfood's item descriptions, prices and availability.
- Calorie estimation, built from ingredients and cached in a local SQLite database
  (`SNAPP_MCP_DB`, default `~/.local/share/snapp-mcp/calories.db`):
  - `food_menu_calories`: a menu with cached calorie ranges, or similar known dishes for uncached ones.
  - `food_calories_lookup`: exact or similar dishes from the knowledge base.
  - `food_ingredients_lookup`: known kcal per 100 g for ingredients, plus the names still missing.
  - `food_calories_save`: batch-saves ingredients and dish estimates. The server sums grams x kcal per 100 g into
    min / likely / max totals, derives a confidence level and warns when the estimate is more than 25% away from an
    online cross-check.
- Persian-aware text normalization and optional SQLite FTS5 search for finding similar dishes.
- Server instructions describing the calorie workflow for agents.
- `read_only` flag on the tool registry; `food_calories_save` is annotated as a non-read-only, non-destructive tool.

### Changed

- Package renamed to `snapp-mcp-calories`. It is not published to PyPI or the MCP Registry; install from GitHub with
  `uvx --from git+https://github.com/A512-dev/snapp-mcp snapp-mcp-calories`.
- Removed the PyPI publish workflow and `server.json`.

## 0.1.3

- Author set to Sepehr Radmard.

## 0.1.2

- SEO and agent-friendly README.

## 0.1.1

- README fixes (RTL ordering, diagram) and Python classifiers.
