"""The parser against real SerpApi answers (owner's app, 2026-11-19, EAN 3474637283414).

Google Shopping does not match a barcode exactly: for this EAN it returned 40 loosely related
shampoos, of which the right one (Kérastase Gloss Absolu) was number 10. The store list in the
fixture is the one of the Elvive Full Resist product (#7 in Google's own order)."""

import json
from pathlib import Path

import lookup
import serp

EAN = "3474637283414"
DATA = json.loads((Path(__file__).parent / "data" / "serp_real_3474637283414.json").read_text(encoding="utf-8"))
ELVIVE = "4035399787308802396"
KERASTASE = "4110889395471276496"


ELVIVE_TOKEN = next(
    i["immersive_product_page_token"]
    for i in DATA["shopping"]["shopping_results"]
    if str(i.get("product_id")) == ELVIVE and i.get("immersive_product_page_token")
)


def fetch_real(params):
    """Shopping: the real answer. Stores: the real list for the Elvive product, nothing for others."""
    if params["engine"] == "google_shopping":
        return DATA["shopping"], None
    if params["page_token"] == ELVIVE_TOKEN:
        return DATA["immersive"], None
    return {"product_results": {"stores": []}}, None


def lookup_real(hint=None):
    source = serp.serp_source(fetch_real, now=lambda: "NOW", hint=hint)
    return lookup.lookup(EAN, None, source=source)


def stores():
    return {o.shop: o for o in lookup.finish_choice(lookup_real(), pick(ELVIVE)).offers}


def pick(key):
    return next(c for c in lookup_real().candidates if c.key == key)


def test_the_noisy_answer_becomes_a_list_to_choose_from_not_a_wrong_guess():
    result = lookup_real()
    assert result.status == lookup.CHOOSE
    assert 1 < len(result.candidates) <= serp.MAX_CANDIDATES
    keys = [c.key for c in result.candidates]
    assert keys[0] == "4359797698752056346"  # Google's own order without a name
    assert ELVIVE in keys  # the product the old "largest group" rule would have picked, by luck
    assert result.offers == ()


def test_the_right_product_is_on_the_list_and_a_name_puts_it_first_and_settles_it():
    result = lookup_real()
    kerastase = next(c for c in result.candidates if c.key == KERASTASE)
    assert kerastase.shops == ("Celini.nl",) and kerastase.price_min == 35.28
    ranked = serp.rank(serp.candidates(DATA["shopping"]["shopping_results"]), "kerastase gloss absolu hydra-glaze")
    assert ranked[0].key == KERASTASE and ranked[0].score == 1.0
    picked = lookup_real("kerastase gloss absolu hydra-glaze")
    assert picked.status == lookup.OK  # one clear match: no list
    assert [o.shop for o in picked.offers] == ["Celini.nl"]


def test_the_elvive_stores_are_all_read_with_price_and_shipping():
    shops = stores()
    assert len(shops) == 14  # the 13 stores plus bol.com, which only the direct results had
    assert (shops["bol.com"].price, shops["bol.com"].shipping) == (12.03, None)
    assert (shops["Onlinevoordeelshop.nl"].price, shops["Onlinevoordeelshop.nl"].shipping) == (4.69, 4.95)
    assert shops["DeOnlineDrogist.nl"].total == 3.4 + 3.95
    for name in ("Kruidvat.nl", "Albert Heijn", "Trekpleister", "Leef.nl"):
        assert shops[name].shipping is None, name  # Google gave no shipping: never guessed


def test_promo_prefixes_move_from_the_title_to_the_details():
    shops = stores()
    expected = {
        "Kruidvat.nl": "2 voor 10.00",
        "Albert Heijn": "Bonus 2e gratis",
        "Trekpleister": "1+1 gratis",
        "Plein.nl": "2+3 gratis",
        "Koopjesdrogisterij.nl": "47% korting",
    }
    for shop, promo in expected.items():
        offer = shops[shop]
        assert f"Actie: {promo}" in offer.details, shop
        assert promo not in (offer.title or ""), shop
    assert not any(d.startswith("Actie") for d in shops["Stuntpakker.nl"].details)


def test_relative_goto_links_point_at_google_and_the_others_are_kept():
    shops = stores()
    assert shops["123schoon.nl"].url.startswith("https://www.google.com/goto?url=")
    assert shops["Onlinevoordeelshop.nl"].url.startswith("https://onlinevoordeelshop.nl/")
    assert all(o.url is None or o.url.startswith("https://") for o in shops.values())


def test_tags_and_the_shops_own_conditions_are_in_the_details():
    offer = stores()["Onlinevoordeelshop.nl"]
    assert "Populairst" in offer.details
    assert any("Gratis bezorging bij bestellingen van" in d for d in offer.details)
    assert len(offer.details) <= serp.MAX_DETAILS
