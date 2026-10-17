"""MCP server entry point: registers every read-only Snappfood and SnappMarket tool."""

import logging

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, food, location, market, nutrition  # noqa: F401  (imports register the tools)
from .registry import TOOLS

INSTRUCTIONS = """\
Unofficial, read-only access to Snappfood (restaurant delivery, snappfood.ir) and SnappMarket
(grocery delivery, snapp.market) in Iran. Nothing here can log in, add to a basket or order.

Workflow:
1. Get coordinates: find_location (address/landmark) or list_cities (city center). All food_* and
   market_* tools need lat/long because menus, prices and delivery depend on the delivery point.
2. Food: food_search / food_find_cheapest for dishes, food_restaurants for restaurants,
   food_menu / food_menu_descriptions for one restaurant, food_order_costs before recommending (delivery fee, minimum
order, coupons), deals via food_party_deals / food_meal_for_one / food_discounted_vendors.
3. Groceries: market_search / market_find_cheapest, market_stores, market_store_info,
   market_store_products, market_party_deals.
4. Calories (estimates, never exact): food_menu_calories lists a menu with cached estimates or, for
   uncached dishes, similar known dishes. For each uncached dish: (a) decompose it into ingredients using
   title, description, toppings and stated weights (e.g. '350 گرم ران مرغ'); estimate typical portions when
   none are stated; list cooking fat as its own wide-range ingredient; (b) give each ingredient
   grams_min/grams_likely/grams_max; (c) call food_ingredients_lookup and research only the missing kcal
   per 100 g via web search; (d) search the web for the same or a close dish and pass that figure and its
   source as cross_check_kcal / cross_check_source; (e) call food_calories_save once with all dishes and
   new ingredients. The server computes the totals and confidence; if it returns a warning (>25% gap),
   revisit the ingredients. Treat similar dishes as a sanity check, never copy them blindly.

Conventions: prices are Toman (1 Toman = 10 Rial). Ratings are 0-5. Vendor codes look like
'947evd'. Persian queries match best ('پیتزا', 'شیر'). True cost of a food order =
price - discount + packaging + delivery fee - coupon, and the basket must reach the minimum order.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    "snapp-mcp",
    title="Snappfood & SnappMarket",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/sepehr071/snapp-mcp",
)

for fn, title, read_only in TOOLS:
    mcp.add_tool(fn, title=title, annotations=READ_ONLY if read_only else LOCAL_WRITE)


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request floods client logs
    mcp.run()


if __name__ == "__main__":
    main()
