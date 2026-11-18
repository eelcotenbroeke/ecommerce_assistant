"""Fake Beslist pages shared by the probe and lookup tests."""
import json

from beslist import search_url

EAN = "3474637283414"
PRODUCT_PATH = f"/p/kerastase-gloss-absolu-bain-hydra-glaze-shampoo-500ml/286/{EAN}/"
PRODUCT_URL = f"https://www.beslist.nl{PRODUCT_PATH}"
SEARCH_URL = search_url(EAN)

# Made-up shop rows except the shapes: (shop, price, shipping, delivery, availability)
ROWS = [
    ("Celini.nl", 35.28, 3.95, "1-2 dagen", "InStock"),
    ("Flaconi.nl", 38.63, 0, "2-3 dagen", "InStock"),
    ("bol.com", 44.18, 0, "1 dag", "InStock"),
    ("Ghost.nl", 20.0, 0, "Niet leverbaar", "InStock"),
]


def product_page(rows=ROWS, gtin=EAN):
    offers = [
        {
            "@type": "Offer",
            "availability": f"http://schema.org/{availability}",
            "price": price,
            "priceCurrency": "EUR",
            "url": f"/p/redirect/{i}",
            "seller": {"@type": "Organization", "name": shop},
            "shippingDetails": {
                "shippingRate": {"value": shipping, "currency": "EUR"},
                "deliveryTime": {"description": delivery},
            },
        }
        for i, (shop, price, shipping, delivery, availability) in enumerate(rows)
    ]
    product = {"@type": "Product", "offers": {"@type": "AggregateOffer", "offers": offers}}
    if gtin:
        product["gtin13"] = gtin
    return f'<script type="application/ld+json">{json.dumps(product)}</script>'


SEARCH_PAGE = f'<html><a href="{PRODUCT_PATH}?x=1">product</a></html>'
