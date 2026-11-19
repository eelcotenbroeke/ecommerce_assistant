import pytest

from linkname import looks_like_link, name_from_link


@pytest.mark.parametrize(
    "url, name",
    [
        # bol: folders, the slug, the id, and a tracking query
        ("https://www.bol.com/nl/nl/p/logitech-mx-master-4-draadloze-muis-rechtshandig-graphite/9300000236202343/?cid=1795-3761",
         "logitech mx master 4 draadloze muis rechtshandig graphite"),
        # the slug before the id, and the id in its own folder
        ("https://www.kruidvat.nl/biodermal-p-cl-e-creme/p/130611", "biodermal p cl e creme"),
        ("https://www.kruidvat.nl/brita-waterfilterkan-style-essential-wit-2-4l-3x-maxtra-pro-pure-performance/p/mp-00049824",
         "brita waterfilterkan style essential wit 2 4l 3x maxtra pro pure performance"),
        # the slug after folders and an id folder
        ("https://www.ah.nl/producten/product/wi333098/segafredo-intermezzo-bonen", "segafredo intermezzo bonen"),
        # an escaped percent sign, an extension and a trailing id
        ("https://www.etos.nl/producten/rimmel-london-100%25-waterproof-mascara-001-black-110663745.html",
         "rimmel london 100% waterproof mascara 001 black"),
        ("https://www.wehkamp.nl/bjorn-borg-boxershorts-set-van-7-17551967/?ref=%7B%22id%22%3A%22mode%22%7D",
         "bjorn borg boxershorts set van 7"),
        # an id folder, a uuid folder and a category code after the slug
        ("https://www.decathlon.nl/p/mp/trainingsset-muurbeugels-met-dipstation-optrekstang-bankje/34294e25-0b70-4a28-8d9c-24553bdacfb6/c1c14",
         "trainingsset muurbeugels met dipstation optrekstang bankje"),
        ("https://www.decathlon.nl/p/sporttas-linear-duffel-s-30-liter/387112/c27m9032091", "sporttas linear duffel s 30 liter"),
        # a short number in front of the name (shops that number their pages) and a barcode behind it
        ("https://www.magimix.nl/nl/foodprocessor/362-cs-3200-xl-zwart-met-citruspers-3519280023090.html",
         "cs 3200 xl zwart met citruspers"),
        # a set number at the end is part of the name, not an id
        ("https://www.intertoys.nl/lego-classic-10696", "lego classic 10696"),
        # shops that are not in anybody's list
        ("https://www.coolblue.nl/product/937057/dyson-v15-detect-absolute.html", "dyson v15 detect absolute"),
        ("https://www.mediamarkt.nl/nl/product/_samsung-galaxy-s24-128gb-zwart-1234567.html", "samsung galaxy s24 128gb zwart"),
        ("https://www.amazon.nl/Anker-PowerWave-Wireless-Charger/dp/B07THHQMHM", "anker powerwave wireless charger"),
        # no scheme, upper case host, a fragment
        ("KRUIDVAT.nl/dove-douchegel-original/p/12345#reviews", "dove douchegel original"),
    ],
)
def test_the_product_name_is_read_from_the_words_of_the_link(url, name):
    assert name_from_link(url) == name


@pytest.mark.parametrize(
    "url",
    [
        "https://www.my-origines.com/nl/calendula-P0300036.html",  # one word and an id
        "https://www.kruidvat.nl/p/shampoo/123",  # one word
        "https://www.shop.nl/",
        "https://www.shop.nl/nl/p/123456",
        "https://www.amazon.nl/dp/B01M635J8I",
        "https://www.amazon.nl/-/en/dp/B07THHQMHM?ref_=ppx_hzod",
        "kerastase drops",  # words, not a link
        "3474637283414",
        "https://www.bol.com/nl/nl/p/ x /1/",  # a space: that is no link
        "",
        None,
    ],
)
def test_a_link_without_a_product_name_gives_none(url):
    assert name_from_link(url) is None


def test_a_very_long_name_is_cut_at_a_word():
    slug = "-".join(["woord"] * 60)
    name = name_from_link(f"https://www.shop.nl/p/{slug}/12345678")
    assert len(name) <= 120 and name.endswith("woord") and " woord woord" in name


def test_the_query_string_is_never_part_of_the_name():
    assert name_from_link("https://www.shop.nl/p/echte-naam-product/1?q=zoek-term-hier-ook") == "echte naam product"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("https://www.kruidvat.nl/p/1", True),
        ("www.kruidvat.nl", True),
        ("kruidvat.nl/p/1", True),
        ("Kijk eens https://www.kruidvat.nl/p/1", True),  # an address with words around it is still not a name
        ("kerastase gloss absolu", False),
        ("Nutella", False),
        ("3474637283414", False),
        ("", False),
        (None, False),
    ],
)
def test_what_looks_like_a_link(text, expected):
    assert looks_like_link(text) is expected
