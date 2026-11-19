import json
from pathlib import Path

import pytest

import members

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def listed():
    return members.load()


def test_the_list_is_the_owners_export_of_1169_members(listed):
    assert len(listed) == 1166  # 1,169 rows: favoshop.nl, fravour.com and lidl.nl are on it twice
    assert {"bol.com", "amazon.nl", "coolblue.nl", "kruidvat.nl", "ah.nl"} <= listed.domains
    assert all(domain == domain.lower() and not domain.startswith("www.") for domain in listed.domains)


def test_the_real_store_list_of_the_elvive_answer(listed):
    stores = json.loads((DATA / "serp_real_3474637283414.json").read_text(encoding="utf-8"))
    names = [s["name"] for s in stores["immersive"]["product_results"]["stores"]]
    links = {s["name"]: s.get("link") for s in stores["immersive"]["product_results"]["stores"]}
    found = {name: listed.match(name, links[name]) for name in names}
    assert {name: domain for name, domain in found.items() if domain} == {
        "123schoon.nl": "123schoon.nl",
        "Kruidvat.nl": "kruidvat.nl",
        "Albert Heijn": "ah.nl",
        "Drogist.nl": "drogist.nl",
        "Plein.nl": "plein.nl",
        "DeOnlineDrogist.nl": "deonlinedrogist.nl",
        "Trekpleister": "trekpleister.nl",
    }
    for name in ("Onlinevoordeelshop.nl", "Koopjesdrogisterij.nl", "Stuntpakker.nl", "Vitatheek.nl",
                 "Varuvo.nl", "Leef.nl"):
        assert not listed.is_member(name, links[name])


def test_the_other_shops_of_that_answer(listed):
    assert listed.is_member("bol.com")
    for name in ("Celini.nl", "flaconi.nl", "eBay", "Makeup", "Cosmética Click"):
        assert not listed.is_member(name)


def test_the_host_of_the_link_counts_with_or_without_www_and_subdomains(listed):
    assert listed.match("Een naam", "https://www.bol.com/nl/p/x/9200000001/") == "bol.com"
    assert listed.match("Een naam", "https://BOL.com:443/nl/p/x") == "bol.com"
    assert listed.match("Een naam", "http://shop.coolblue.nl/product/1") == "coolblue.nl"
    assert listed.match("Een naam", "https://notbol.com/") is None  # not a subdomain of bol.com


def test_googles_own_links_say_nothing_about_the_shop(listed):
    assert listed.match("Onbekend", "https://www.google.com/goto?url=CAES") is None
    assert listed.match("Onbekend", "/goto?url=CAES") is None
    assert listed.match("Onbekend", "https://www.google.nl/shopping/product/1") is None
    assert listed.match("Onbekend", None) is None
    assert listed.match(None, None) is None
    assert listed.match("Trekpleister", "https://www.google.com/goto?url=CAES") == "trekpleister.nl"


def test_a_name_that_is_a_domain_must_be_on_the_list_as_written(listed):
    assert listed.match("Kruidvat.nl") == "kruidvat.nl"
    assert listed.match("www.Kruidvat.nl") == "kruidvat.nl"
    assert listed.match("easycosmetic.nl") == "easycosmetic.nl"
    # the German shop is not the Dutch member, even though the first label is the same
    assert listed.match("easycosmetic.DE") is None


def test_a_plain_name_is_compared_with_the_first_label_and_the_slug(listed):
    assert listed.match("Trekpleister") == "trekpleister.nl"
    assert listed.match("Coólblue") == "coolblue.nl"  # accents and case do not matter
    assert listed.match("Media Markt") == "mediamarkt.nl"
    assert listed.match("Bol") == "bol.com"
    assert listed.match("Amazon") == "amazon.nl"
    assert listed.match("HEMA") == "hema.nl"


def test_albert_heijn_is_known_by_its_alias_and_a_two_letter_name_is_too_short(listed):
    assert listed.match("Albert Heijn") == "ah.nl"
    assert listed.match("ah") is None


def test_a_host_on_the_list_beats_an_unknown_name(listed):
    assert listed.match("Webwinkel X", "https://www.kruidvat.nl/p/1") == "kruidvat.nl"
    # a link through a tracker does not hide a name that is itself a member domain
    assert listed.match("Kruidvat.nl", "https://tracker.example.com/r?x=1") == "kruidvat.nl"


def test_host_of_survives_junk():
    assert members.host_of("https://www.Bol.com/x") == "bol.com"
    assert members.host_of("not a link") == ""
    assert members.host_of("http://[broken") == ""
    assert members.host_of("") == ""
    assert members.host_of(None) == ""


def test_build_strips_legal_tails_and_short_names():
    built = members.build(
        [
            ("wehkamp-retail-group-b-v", "www.Wehkamp.nl"),
            ("ah-nl", "ah.nl"),
            ("bold-monkey", "boldmonkey.com"),
            ("x-b-v", "x.nl"),
            ("", ""),
        ]
    )
    assert built.domains == {"wehkamp.nl", "ah.nl", "boldmonkey.com", "x.nl"}
    assert built.match("Wehkamp") == "wehkamp.nl"
    assert built.match("Wehkamp Retail Group") == "wehkamp.nl"
    assert built.match("Bold Monkey") == "boldmonkey.com"
    assert built.match("Albert Heijn") == "ah.nl"  # the alias is added because ah.nl is listed
    assert built.match("ah") is None and built.match("x") is None
    assert members.build([("lidl-nl", "lidl.nl")]).match("Albert Heijn") is None  # ah.nl not listed


def test_load_reads_a_csv_with_a_byte_order_mark(tmp_path):
    path = tmp_path / "leden.csv"
    path.write_bytes("﻿webshop_slug,domain\nplein-nl,plein.nl\n".encode("utf-8"))
    assert members.load.__wrapped__(path).is_member("Plein.nl")
