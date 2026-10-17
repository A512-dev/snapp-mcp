import pytest

from snapp_mcp import calories

pytestmark = pytest.mark.anyio

SHIRAZ = {"lat": 29.61695, "long": 52.53099}


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPP_MCP_DB", str(tmp_path / "cal.db"))
    return tmp_path / "cal.db"


ING = [
    {"name": "برنج", "kcal_per_100g": 130},
    {"name": "ران مرغ", "kcal_per_100g": 200},
    {"name": "روغن", "kcal_per_100g": 880},
]


def dish(title="زرشک پلو با مرغ", description="برنج و مرغ", **extra):
    return {
        "title": title,
        "description": description,
        "ingredients": [
            {"name": "برنج", "grams_min": 300, "grams_likely": 400, "grams_max": 500},
            {"name": "ران مرغ", "grams_min": 150, "grams_likely": 200, "grams_max": 250},
            {"name": "روغن", "grams_min": 5, "grams_likely": 10, "grams_max": 20},
        ],
        **extra,
    }


async def save(client, dishes=(), ingredients=()):
    result = await client.call_tool("food_calories_save", {"dishes": list(dishes), "ingredients": list(ingredients)})
    return result.structured_content


async def test_save_math_and_exact_lookup_with_spelling_variants(client):
    out = await save(client, [dish()], ING)
    saved = out["dishes"][0]
    assert (saved["kcal_min"], saved["kcal_likely"], saved["kcal_max"]) == (
        round(300 * 1.3 + 150 * 2 + 5 * 8.8),
        round(400 * 1.3 + 200 * 2 + 10 * 8.8),
        round(500 * 1.3 + 250 * 2 + 20 * 8.8),
    )
    assert saved["confidence"] == "low" and not out["errors"]
    hit = await client.call_tool(
        "food_calories_lookup", {"items": [{"title": "زرشك پلو با مرغ", "description": "برنج و مرغ"}]}
    )
    assert hit.structured_content["items"][0]["cached"] is True

    again = await save(client, [dish(notes="v2")])
    assert again["dishes"][0]["status"] == "updated"


async def test_confidence_and_cross_check(client):
    await save(client, [], ING)
    out = await save(client, [dish(weight_g=700, cross_check_kcal=1100, cross_check_source="x")])
    assert out["dishes"][0]["confidence"] == "high" and not out["warnings"]
    out = await save(client, [dish(title="دیگر", cross_check_kcal=500)])
    assert out["warnings"] and out["dishes"][0]["confidence"] == "medium"


async def test_validation_errors(client):
    await save(client, [], ING)
    bad_order = dish(title="a b")
    bad_order["ingredients"][0]["grams_min"] = 450
    out = await save(client, [bad_order])
    assert out["errors"] and not out["dishes"]
    huge = dish(title="huge")
    huge["ingredients"][0].update(grams_min=5000, grams_likely=5000, grams_max=5000)
    assert (await save(client, [huge]))["errors"]
    unknown = dish(title="unk")
    unknown["ingredients"][0]["name"] = "ناشناخته"
    assert (await save(client, [unknown]))["errors"]
    result = await client.call_tool("food_calories_save", {"ingredients": [{"name": "x", "kcal_per_100g": 5000}]})
    assert result.is_error
    negative = dish(title="neg")
    negative["ingredients"] = [{"name": "برنج", "grams_min": -1, "grams_likely": 1, "grams_max": 2}]
    result = await client.call_tool("food_calories_save", {"dishes": [negative]})
    assert result.is_error


async def test_similar_dishes_rank_and_fts_fallback(client, monkeypatch):
    other = dish(title="زرشک پلو مرغ ویژه", description="")
    await save(client, [dish(), other, dish(title="چای", description="دمنوش")], ING)
    ref = {"items": [{"title": "زرشک پلو با مرغ مجلسی"}]}
    similar = (await client.call_tool("food_calories_lookup", ref)).structured_content["items"][0]["similar"]
    assert similar[0]["title"].startswith("زرشک") and all(s["title"] != "چای" for s in similar[:2])
    monkeypatch.setattr(calories, "FTS_ENABLED", False)
    similar = (await client.call_tool("food_calories_lookup", ref)).structured_content["items"][0]["similar"]
    assert similar[0]["title"].startswith("زرشک")


async def test_ingredient_lookup_lists_missing(client):
    await save(client, [], ING)
    out = (await client.call_tool("food_ingredients_lookup", {"names": ["برنج", "زعفران"]})).structured_content
    assert [i["name"] for i in out["known"]] == ["برنج"] and out["missing"] == ["زعفران"]


async def test_user_source_is_not_overwritten(client):
    await save(client, [dish(source="user")], ING)
    out = await save(client, [dish()])
    assert out["dishes"][0]["status"] == "kept"


def test_parse_weight():
    assert calories.parse_weight_g("350 گرم ران مرغ") == 350
    assert calories.parse_weight_g("x", "۲ کیلوگرم برنج") == 2000
    assert calories.parse_weight_g("بدون وزن") is None


async def test_menu_calories_mixes_cached_and_uncached(client, api):
    await save(client, [dish()], ING)
    api["/menu-read-model/947evd"] = {
        "data": {
            "vendor": {},
            "menuCategories": [
                {
                    "title": "غذا",
                    "products": [
                        {
                            "title": "زرشک پلو با مرغ",
                            "description": "برنج و مرغ",
                            "variations": [{"id": 1, "price": 100}],
                        },
                        {
                            "title": "زرشک پلو با مرغ مجلسی",
                            "description": "350 گرم ران مرغ، 500 گرم برنج",
                            "variations": [{"id": 2, "price": 200}],
                        },
                    ],
                }
            ],
        }
    }
    out = (await client.call_tool("food_menu_calories", {"vendor_code": "947evd", **SHIRAZ})).structured_content
    first, second = out["items"]
    assert first["cached"] is True and first["kcal_likely"] > 0
    assert second["cached"] is False and second["weight_g_hint"] == 350
    assert second["similar"][0]["title"] == "زرشک پلو با مرغ"
    assert out["uncached"] == 1


def test_normalize_unifies_arabic_persian_and_digits():
    assert calories.normalize("زرشك‌پلو ١٢٣") == calories.normalize("زرشک پلو ۱۲۳") == "زرشک پلو 123"
    assert calories.dish_key("پیتزا", None, ["a", "b"]) == calories.dish_key("پیتزا", None, ["b", "a"])
    assert calories.dish_key("پیتزا") != calories.dish_key("پیتزا", "پنیر")


def test_db_created_with_schema_version(db):
    conn = calories.connect()
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == calories.SCHEMA_VERSION
    finally:
        conn.close()
    assert db.exists()


async def test_ingredients_reused_and_resave_replaces_lines(client):
    await save(client, [dish()], ING)
    lean = dish(title="مرغ ساده")
    lean["ingredients"] = [{"name": "ران مرغ", "grams_min": 100, "grams_likely": 100, "grams_max": 100}]
    out = await save(client, [lean])
    assert out["dishes"][0]["kcal_likely"] == 200 and not out["errors"]
    changed = dish()
    changed["ingredients"] = changed["ingredients"][:1]
    await save(client, [changed])
    hit = await client.call_tool(
        "food_calories_lookup", {"items": [{"title": "زرشک پلو با مرغ", "description": "برنج و مرغ"}]}
    )
    assert hit.structured_content["items"][0]["ingredients"] == ["برنج 400g"]


async def test_user_ingredient_is_kept(client):
    await save(client, [], [{"name": "برنج", "kcal_per_100g": 111, "source": "user"}])
    out = await save(client, [], [{"name": "برنج", "kcal_per_100g": 130, "source": "web"}])
    assert out["ingredients"][0]["status"] == "kept"
    known = (await client.call_tool("food_ingredients_lookup", {"names": ["برنج"]})).structured_content["known"]
    assert known[0]["kcal_per_100g"] == 111


async def test_lookup_mixed_batch(client):
    await save(client, [dish()], ING)
    items = [{"title": "زرشک پلو با مرغ", "description": "برنج و مرغ"}, {"title": "کباب"}]
    out = (await client.call_tool("food_calories_lookup", {"items": items})).structured_content["items"]
    assert out[0]["cached"] is True
    assert out[1]["cached"] is False and out[1]["similar"] == []


async def test_menu_calories_query_limit_and_toppings(client, api):
    products = [
        {
            "title": "پیتزا",
            "description": "",
            "variations": [{"id": 1, "price": 100, "toppings": [{"options": [{"title": "قارچ"}]}]}],
        },
        {"title": "نوشابه", "variations": [{"id": 2, "price": 10}]},
    ]
    api["/menu-read-model/947evd"] = {"data": {"menuCategories": [{"title": "x", "products": products}]}}
    out = (
        await client.call_tool("food_menu_calories", {"vendor_code": "947evd", **SHIRAZ, "query": "پیتزا"})
    ).structured_content
    assert out["total_items"] == 1 and out["items"][0]["toppings"] == ["قارچ"]
    out = (
        await client.call_tool("food_menu_calories", {"vendor_code": "947evd", **SHIRAZ, "limit": 1})
    ).structured_content
    assert out["total_items"] == 2 and len(out["items"]) == 1


async def test_menu_calories_rejects_bad_vendor_code(client, api):
    result = await client.call_tool("food_menu_calories", {"vendor_code": "../x", **SHIRAZ})
    assert result.is_error and not api.calls
