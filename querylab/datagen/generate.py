"""Generate deterministic customer, order, and product data."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from querylab.database import Database
from querylab.model import Row
from querylab.storage.table import Table


@dataclass(frozen=True)
class DataConfig:
    """Controls row counts, page layout, distributions, and reproducibility."""

    customer_rows: int = 10_000
    order_rows: int = 200_000
    product_rows: int = 1_000
    tuples_per_page: int = 100
    page_counts: dict[str, int] = field(default_factory=dict)
    seed: int = 42
    skew: float = 0.0
    correlation: float = 0.8


def _choose_customer_id(
    random_source: random.Random,
    customer_count: int,
    skew: float,
) -> int:
    if skew <= 0:
        return random_source.randint(1, customer_count)

    # A power transform creates a visible hot-key distribution without
    # requiring a third-party Zipf library.
    sample = random_source.random() ** (1.0 + skew)
    return 1 + min(customer_count - 1, int(sample * customer_count))


def _generate_customers(config: DataConfig, random_source: random.Random) -> list[Row]:
    regions = ["north", "south", "east", "west"]
    tiers = ["bronze", "silver", "gold"]
    rows: list[Row] = []

    for customer_id in range(1, config.customer_rows + 1):
        region = regions[(customer_id - 1) % len(regions)]

        if random_source.random() < config.correlation:
            tier = "gold" if region == "north" else "bronze"
        else:
            tier = random_source.choice(tiers)

        rows.append(
            {
                "id": customer_id,
                "region": region,
                "tier": tier,
                "signup_year": random_source.randint(2015, 2026),
            }
        )

    return rows


def _generate_products(config: DataConfig, random_source: random.Random) -> list[Row]:
    categories = ["books", "electronics", "home", "sports", "toys"]
    rows: list[Row] = []

    for product_id in range(1, config.product_rows + 1):
        rows.append(
            {
                "id": product_id,
                "category": categories[(product_id - 1) % len(categories)],
                "price": round(random_source.uniform(5, 1_000), 2),
            }
        )

    return rows


def _generate_orders(config: DataConfig, random_source: random.Random) -> list[Row]:
    rows: list[Row] = []

    for order_id in range(1, config.order_rows + 1):
        customer_id = _choose_customer_id(
            random_source,
            config.customer_rows,
            config.skew,
        )
        rows.append(
            {
                "id": order_id,
                "customer_id": customer_id,
                "product_id": random_source.randint(1, config.product_rows),
                "amount": round(random_source.lognormvariate(4.5, 0.9), 2),
                "order_date": random_source.randint(2019, 2026),
            }
        )

    return rows


def generate_database(config: DataConfig) -> Database:
    """Build all three tables and the default demonstration indexes."""

    if min(config.customer_rows, config.order_rows, config.product_rows) < 1:
        raise ValueError("all row counts must be at least 1")

    random_source = random.Random(config.seed)
    database = Database()

    generated_rows = {
        "customers": _generate_customers(config, random_source),
        "products": _generate_products(config, random_source),
        "orders": _generate_orders(config, random_source),
    }

    for table_name, rows in generated_rows.items():
        table = Table(
            table_name,
            rows,
            tuples_per_page=config.tuples_per_page,
            target_page_count=config.page_counts.get(table_name),
        )
        database.add_table(table)

    database.create_index("customers", "id", clustered=True)
    database.create_index("customers", "tier")
    database.create_index("orders", "customer_id")
    database.create_index("orders", "product_id")
    database.create_index("products", "id", clustered=True)
    database.create_index("products", "category")
    return database
