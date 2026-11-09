"""Product listing upserts should resolve existing ids in O(1)."""
from __future__ import annotations

from app.store import store
from app.tools import ToolBox


def test_upsert_listing_reuses_product_id_via_index():
    session = store.create("笔记本", ["jd"])
    box = ToolBox(session)
    assert box._upsert_listing(
        "jd",
        {
            "source_product_id": "sku-1",
            "title": "A",
            "price": 100,
            "currency": "CNY",
        },
    )
    first_id = next(iter(session.products))
    # Poison linear scan: many decoys that would slow O(n) lookup.
    for i in range(50):
        session.products[f"decoy-{i}"] = {
            "product_id": f"decoy-{i}",
            "source": "jd",
            "source_product_id": f"other-{i}",
            "title": "x",
        }
    # Rebuild index after external mutation of products dict.
    box._reindex_products()
    assert box._upsert_listing(
        "jd",
        {
            "source_product_id": "sku-1",
            "title": "A2",
            "price": 120,
            "currency": "CNY",
        },
    )
    matches = [
        pid
        for pid, product in session.products.items()
        if product.get("source_product_id") == "sku-1"
    ]
    assert matches == [first_id]
    assert session.products[first_id]["title"] == "A2"
