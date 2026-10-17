"""Calorie estimation tools backed by the local ingredient knowledge base."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from . import calories
from .food import VendorCode, _has, _menu_description_items, _menu_descriptions
from .location import Lat, Long
from .registry import tool


class DishRef(BaseModel):
    title: str
    description: str | None = None
    toppings: list[str] = []


class IngredientLine(BaseModel):
    name: str
    grams_min: float = Field(ge=0)
    grams_likely: float = Field(ge=0)
    grams_max: float = Field(ge=0)
    note: str | None = None


class DishEstimate(BaseModel):
    title: str
    description: str | None = None
    toppings: list[str] = []
    vendor_code: str | None = None
    weight_g: float | None = Field(default=None, ge=0, description="Stated total weight of the dish in grams.")
    ingredients: list[IngredientLine] = Field(min_length=1)
    cross_check_kcal: float | None = Field(default=None, gt=0, description="kcal found online for the same dish.")
    cross_check_source: str | None = None
    source: str = Field(default="ingredients", description="ingredients, web or user")
    notes: str | None = None


class IngredientInfo(BaseModel):
    name: str
    kcal_per_100g: float = Field(ge=0, le=calories.MAX_KCAL_PER_100G)
    protein_g: float | None = Field(default=None, ge=0)
    carbs_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)
    typical_portion_g: float | None = Field(default=None, ge=0)
    source: str | None = Field(default=None, description="Where the value came from, or 'user'.")


def _lookup(conn: Any, ref: dict[str, Any]) -> dict[str, Any]:
    hit = calories.exact(conn, ref["title"], ref.get("description"), ref.get("toppings"))
    if hit:
        return hit
    return {
        "cached": False,
        "title": ref["title"],
        "description": ref.get("description") or "",
        "toppings": ref.get("toppings") or [],
        "weight_g_hint": calories.parse_weight_g(ref["title"], ref.get("description")),
        "similar": calories.similar(conn, ref["title"], ref.get("description"), ref.get("toppings")),
    }


def _toppings(payload: dict[str, Any]) -> dict[Any, list[str]]:
    found: dict[Any, list[str]] = {}
    for category in payload.get("menuCategories", []):
        for product in category.get("products", []):
            for variation in product.get("variations", []):
                raw = variation.get("toppings") or product.get("toppings") or []
                names = []
                for group in raw:
                    for option in group.get("options") or group.get("toppings") or [group]:
                        title = option.get("title") if isinstance(option, dict) else None
                        if title:
                            names.append(title)
                found.setdefault(variation.get("id"), names)
    return found


@tool("Menu with calorie estimates")
async def food_menu_calories(
    vendor_code: VendorCode,
    lat: Lat,
    long: Long,
    query: Annotated[str | None, Field(description="Only items whose title contains this text.")] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max items to return.")] = 30,
) -> dict[str, Any]:
    """List a restaurant's dishes with cached calorie estimates from the local knowledge base.

    Uncached dishes come back with description, toppings, a stated-weight hint and up to 3 similar
    known dishes, so you can estimate them from ingredients (see the server instructions).
    """
    data = await _menu_descriptions(vendor_code, {"lat": lat, "long": long})
    payload = data.get("data", {})
    items = _menu_description_items(payload)
    if query:
        items = [item for item in items if _has(query, item["title"] or "")]
    toppings = _toppings(payload)
    conn = calories.connect()
    try:
        out = []
        for item in items[:limit]:
            ref = {
                "title": item["title"] or "",
                "description": item["description"],
                "toppings": toppings.get(item["id"], []),
            }
            out.append({"id": item["id"], "price": item["final_price"], **_lookup(conn, ref)})
    finally:
        conn.close()
    return {
        "vendor_code": vendor_code,
        "total_items": len(items),
        "uncached": sum(1 for entry in out if not entry["cached"]),
        "items": out,
    }


@tool("Look up dish calories")
async def food_calories_lookup(items: Annotated[list[DishRef], Field(min_length=1, max_length=50)]) -> dict[str, Any]:
    """Look up dishes (from any tool) in the local calorie knowledge base: exact hit or similar dishes."""
    conn = calories.connect()
    try:
        return {"items": [_lookup(conn, item.model_dump()) for item in items]}
    finally:
        conn.close()


@tool("Look up ingredient calories")
async def food_ingredients_lookup(names: Annotated[list[str], Field(min_length=1, max_length=100)]) -> dict[str, Any]:
    """Return known kcal per 100 g for ingredients and list the names still missing (research only those)."""
    conn = calories.connect()
    try:
        return calories.lookup_ingredients(conn, names)
    finally:
        conn.close()


@tool("Save calorie estimates", read_only=False)
async def food_calories_save(
    dishes: Annotated[list[DishEstimate], Field(max_length=50)] = [],  # noqa: B006
    ingredients: Annotated[list[IngredientInfo], Field(max_length=100)] = [],  # noqa: B006
) -> dict[str, Any]:
    """Save ingredients and dish estimates to the local knowledge base in one batch.

    The server sums grams x kcal per 100 g into kcal_min/likely/max and derives confidence. Save new
    ingredients here too. A warning means the estimate differs >25% from cross_check_kcal.
    Entries with source 'user' are never overwritten by other sources.
    """
    conn = calories.connect()
    try:
        saved_ingredients = [
            {"name": item.name, "status": calories.save_ingredient(conn, item.model_dump())} for item in ingredients
        ]
        results, errors, warnings = [], [], []
        for dish in dishes:
            try:
                result = calories.save_dish(conn, dish.model_dump())
            except ValueError as exc:
                errors.append(str(exc))
                continue
            warnings.extend(result.pop("warnings"))
            results.append(result)
        conn.commit()
    finally:
        conn.close()
    return {"ingredients": saved_ingredients, "dishes": results, "warnings": warnings, "errors": errors}
