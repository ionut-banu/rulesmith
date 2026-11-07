"""Build ``04-orders-wide.parquet``, the wide Parquet sample file.

Rebuild it from the repo root with:

    uv run python sample-data/make_parquet.py

Every value is written out below: nothing is random and nothing depends on
the current date or time, so the file always holds the same table. The
table is written without the pandas index (``index=False``) so row
positions line up with ``df.iloc`` in the failing-rows view.

Pass an output path as the only argument to write somewhere else (the unit
test does this to check the committed file is up to date).
"""

import sys
from pathlib import Path

import pandas as pd

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "04-orders-wide.parquet"

COLUMNS = [
    "order_id",
    "customer_id",
    "amount",
    "quantity",
    "status",
    "currency",
    "unit_price",
    "discount",
    "tax",
    "shipping_fee",
    "warehouse",
    "city",
    "country_code",
    "sales_region",
    "channel",
    "payment_method",
    "product_sku",
    "product_category",
    "carrier",
    "priority",
    "gift_wrap",
    "promo_code",
]

# One tuple per row, in COLUMNS order. ORD-0031 and ORD-0033 are in USD.
ROWS = [
    ("ORD-0029", "CUST-021", 30.00, 3, "paid", "EUR", 10.00, 0.00, 5.70, 4.90,
     "WH-01", "Sampleton", "XX", "REGION-A", "web", "card", "SKU-0001",
     "category-a", "CARRIER-1", "standard", False, "PROMO-NONE"),
    ("ORD-0030", "CUST-022", 54.00, 6, "shipped", "EUR", 9.00, 0.00, 10.26, 0.00,
     "WH-02", "Testville", "XX", "REGION-B", "store", "cash", "SKU-0002",
     "category-b", "CARRIER-2", "express", True, "PROMO-10"),
    ("ORD-0031", "CUST-023", 18.50, 1, "paid", "USD", 18.50, 0.00, 3.52, 4.90,
     "WH-03", "Exampleburg", "YY", "REGION-C", "web", "card", "SKU-0003",
     "category-a", "CARRIER-1", "standard", False, "PROMO-NONE"),
    ("ORD-0032", "CUST-005", 75.00, 5, "refunded", "EUR", 15.00, 0.00, 14.25, 4.90,
     "WH-01", "Sampleton", "XX", "REGION-A", "phone", "transfer", "SKU-0004",
     "category-c", "CARRIER-3", "standard", False, "PROMO-NONE"),
    ("ORD-0033", "CUST-024", 42.00, 2, "shipped", "USD", 21.00, 0.00, 7.98, 0.00,
     "WH-02", "Testville", "YY", "REGION-C", "web", "card", "SKU-0005",
     "category-b", "CARRIER-2", "express", True, "PROMO-10"),
    ("ORD-0034", "CUST-025", 12.00, 4, "paid", "EUR", 3.00, 0.00, 2.28, 4.90,
     "WH-03", "Sampleton", "XX", "REGION-B", "store", "card", "SKU-0006",
     "category-c", "CARRIER-3", "standard", False, "PROMO-NONE"),
]


def build_table() -> pd.DataFrame:
    """Return the sample table as a DataFrame."""
    return pd.DataFrame(ROWS, columns=COLUMNS)


def write_parquet(path: Path) -> None:
    """Write the sample table to ``path`` as Parquet, without the index."""
    build_table().to_parquet(path, engine="pyarrow", index=False)


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    write_parquet(output)
    print(f"wrote {output.name}")
