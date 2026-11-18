"""Builders for fake SerpApi answers, shared by the serp and phone-screen tests."""

TOKEN = "eyJlaSI6IlRpaFph"
TITLE = "Kérastase Gloss Absolu Bain Hydra-Glaze Shampoo 500 ml"


def item(source, price, delivery=None, product_id="111", title=TITLE, token=TOKEN, link=None, **extra):
    entry = {
        "title": title,
        "product_id": product_id,
        "product_link": f"https://www.google.com/shopping/product/{product_id}",
        "source": source,
        "price": f"€ {price:.2f}".replace(".", ","),
        "extracted_price": price,
    }
    if delivery is not None:
        entry["delivery"] = delivery
    if token:
        entry["immersive_product_page_token"] = token
    if link:
        entry["link"] = link
    entry.update(extra)
    return entry


def in_dollars(entry):
    return {**entry, "price": f"${entry['extracted_price']:.2f}"}


def store(name, price, shipping_extracted=None, shipping=None, link=None, extensions=None, **extra):
    entry = {"name": name, "price": f"€ {price:.2f}".replace(".", ","), "extracted_price": price}
    if shipping_extracted is not None:
        entry["shipping_extracted"] = shipping_extracted
    if shipping is not None:
        entry["shipping"] = shipping
    if link:
        entry["link"] = link
    if extensions is not None:
        entry["extensions"] = extensions
    entry.update(extra)
    return entry
