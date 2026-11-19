"""Which kind of shop is this? A rough category per Thuiswinkel member, from its own description.

Pure: no network. The descriptions are the members' own texts from their Thuiswinkel Waarborg
profile page; they are not kept in the repo, only the category each shop got (the `categorie`
column of `thuiswinkel_leden.csv`). The rules below were tuned on the 1,169 members on 2026-11-19.

A shop gets at most two categories, the ones whose keywords occur most in its name, slug and
description. A shop whose description says nothing useful gets `OVERIG`. This is a first guess to
narrow a list of shops, never a verdict.
"""
from __future__ import annotations

import re
import unicodedata

OVERIG = "overig"
MAX_CATEGORIES = 2
SECOND_SHARE = 0.6  # a second category needs at least this share of the best score (and 2 hits)

# (code, label shown in the dropdown, keyword patterns). A pattern is a regular expression on the
# lower-case text without accents; short words carry their own \b so they do not match inside
# other words, longer stems also match inside Dutch compounds ("tuinmeubelshop").
RULES = (
    ("elektronica", "Elektronica & computers", (
        r"elektronic", r"computer", r"laptop", r"notebook", r"\btablet", r"smartphone", r"telefoon",
        r"\bmobiel", r"printer", r"\binkt", r"toner", r"camera", r"\bdrone", r"televisie", r"\btv\b",
        r"audio", r"koptelefoon", r"\bgaming", r"console", r"netwerk", r"\bkabel", r"\baccu",
        r"batterij", r"oplader", r"gadget", r"\bapple", r"refurbished", r"\bgps", r"satelliet",
        r"software", r"hifi", r"speaker", r"beeld ?(?:&|en) ?geluid", r"domotica", r"smart ?home",
        r"\bit-", r"\bgsm\b", r"\bphone", r"smartwatch", r"powerbank", r"voip", r"firewall", r"router",
        r"wifi", r"\bmodem", r"\bwlan", r"\bvpn\b", r"beamer", r"projector", r"presentatie",
        r"beveilig", r"alarmsys",
        r"\bgames?\b", r"nintendo", r"playstation", r"\bxbox", r"walkie",
    )),
    ("huishouden", "Huishouden, keuken & klimaat", (
        r"witgoed", r"huishoudelijke", r"keuken", r"kookgerei", r"\bpannen", r"bestek", r"servies",
        r"schoonmaak", r"stofzuig", r"wasmachine", r"koelkast", r"inbouwapparat", r"koffiemachine",
        r"espresso", r"sous-?vide", r"strijk", r"afwas", r"\bblender", r"\bmixer", r"bakken",
        r"keukenapparat", r"huishoud", r"waterkoker", r"airfryer", r"juicer", r"sapcentrifuge",
        r"afzuigkap", r"drinkfles", r"waterfles", r"bidon", r"airco", r"\bkachel", r"verwarming",
        r"ventilat", r"luchtreinig", r"ontvochtig", r"luchtbevochtig", r"klimaat", r"\bhaarden?\b",
        r"glaswerk", r"\bkluis", r"\bkluizen", r"brandkast", r"waterfilter", r"gasveer",
    )),
    ("wonen", "Wonen & slapen", (
        r"meubel", r"meubil", r"interieur", r"inrichten", r"\bwonen", r"woonkamer", r"woonaccessoires",
        r"slaap", r"matras", r"dekbed", r"kussen", r"beddengoed", r"gordijn", r"vloer", r"tapijt",
        r"verlichting", r"\blamp", r"\bkast(?:en)?\b", r"\bbank(?:en)?\b", r"\btafel", r"\bstoel",
        r"decoratie", r"behang", r"\bkaars", r"vloerkleed", r"raamdecoratie", r"\bbed(?:den)?\b",
        r"zonwering", r"\bspiegel", r"rolluik", r"jaloezie", r"\bhorren\b", r"hangmat", r"hangstoel",
        r"zitzak", r"om het huis", r"\bhome\b",
    )),
    ("tuin_klussen", "Tuin, bouw & klussen", (
        r"tuin", r"gazon", r"graszo", r"\bplanten", r"haagplant", r"zaden", r"\bhek", r"afrastering",
        r"zwembad", r"parasol", r"\bklus", r"gereedschap", r"bouwmarkt", r"bouwmateriaal",
        r"bouwmaterialen", r"bouwproject", r"\bverf\b", r"\bhout", r"planken", r"ijzerwaren",
        r"dakpannen", r"\btrappen", r"barbecue", r"buitenmeubel", r"buitenkeuken", r"buitenleven",
        r"meststof", r"bestrijding", r"\bdeur(?:en)?\b", r"\bkozijn", r"bevestiging", r"\bsanitair",
        r"badkamer", r"\btegel", r"\bijzer", r"\bsteigers?\b", r"hekwerk", r"schutting", r"lichtkoepel",
        r"aluminium", r"\bmarter", r"ongedierte", r"knaag",
        r"\brvs\b", r"\blasklus", r"\blassen\b", r"\bgevelsteen", r"\bbakstenen", r"overkapping", r"schuifwand", r"\bleiding",
    )),
    ("mode", "Mode, schoenen & tassen", (
        r"mode\b", r"\bmodezaak", r"\bmodewinkel", r"kleding", r"kleren", r"\bjurk", r"ondergoed",
        r"lingerie", r"schoen", r"\bshoes?\b", r"sneaker", r"\blaarz", r"\btassen\b", r"\bjas(?:sen)?\b",
        r"\bshirt", r"\bbroek", r"kleertjes", r"zwemkleding", r"fashion", r"merkkleding", r"\bsokken",
        r"\bhoed", r"outfit", r"\bsandalen", r"\bbadmode", r"\bbikini", r"\btruien", r"\bbeha\b",
        r"\bbh's", r"shapewear", r"sloggi", r"boxershort", r"about you",
        r"pantoffel", r"\bhelm",
    )),
    ("sieraden", "Sieraden & horloges", (
        r"sieraden", r"juweel", r"juwelier", r"jewel", r"horloge", r"\bgouden?\b", r"goudonline", r"edelmetaal", r"\bzilver",
        r"diamant", r"\bringen\b", r"\bketting", r"armband", r"\bbijoux",
        r"sieraad", r"diamonds?",
    )),
    ("beauty_gezondheid", "Beauty, verzorging & gezondheid", (
        r"parfum", r"cosmetic", r"make-?up", r"verzorging", r"\bhuid", r"haarverzorging", r"haarproduct",
        r"haarverf", r"hairextension", r"\bhair\b", r"haarstyl", r"beauty", r"drogist", r"supplement",
        r"vitamin", r"gezondheid", r"gezonder", r"gezonde", r"apotheek", r"medisch", r"zorghulp",
        r"zorgartikel", r"zorgproduct", r"thuiszorg", r"hulpmiddel", r"rollator", r"scootmobiel",
        r"orthoped", r"hoortoestel", r"contactlens", r"lenzen", r"\bbril(?:len)?\b", r"scheerapparaat",
        r"tondeuse", r"massage", r"natuurgeneeskund", r"\bkruiden", r"\bnagel", r"shampoo", r"\bzeep",
        r"\bcreme", r"tandpasta", r"mondverzorging", r"fysio",
        r"defibrillator", r"\bdna\b", r"\bsoa\b", r"optiek", r"optisch", r"voetklacht", r"\bbrandblus",
    )),
    ("erotiek", "Erotiek", (
        r"erotisch", r"\bseks", r"sextoy", r"pleasure", r"\bvibrator",
    )),
    ("eten_drinken", "Eten & drinken", (
        r"koffie", r"\bthee\b", r"wijn", r"\bbier\b", r"\bdrank", r"chocolade", r"supermarkt",
        r"biologisch", r"delicatess", r"\bkaas", r"\bvlees", r"boodschappen", r"maaltijd", r"\bsnoep",
        r"whisky", r"popcorn", r"nachos", r"levensmiddel", r"\bspecerij", r"\bgin\b", r"likeur",
        r"champagne", r"\bbrood\b", r"\bbakkerij", r"zoetwaren", r"gebak", r"\btaart", r"\bnoten\b",
    )),
    ("baby_speelgoed", "Baby, kind & speelgoed", (
        r"\bbaby", r"kinderen", r"\bkind(?:er)?\b", r"\bkids", r"speelgoed", r"\bpeuter", r"kinderwagen",
        r"puzzel", r"bordspel", r"gezelschapsspel", r"\blego", r"knuffel", r"modelbouw", r"modeltrein",
        r"\bhobby", r"stoommachine", r"zwangerschap", r"kraam", r"kinderkamer", r"verkleed", r"kindje",
        r"kleintje", r"ouderschap", r"prenatal",
    )),
    ("sport_outdoor", "Sport & outdoor", (
        r"\bsport", r"voetbal", r"fitness", r"vechtsport", r"outdoor", r"kamperen", r"camping",
        r"wandel", r"hardloop", r"\bgolf", r"\btennis", r"\bhockey", r"\bduik", r"\bsurf", r"\bzwem",
        r"luchtbed", r"\bski", r"\bvissen\b", r"hengel", r"\bjacht", r"\bboks", r"yoga", r"\bpadel",
        r"\bklim",
        r"squash", r"\bdarts?\b", r"cycling", r"parapent", r"schermvlieg", r"buitenavontu", r"buitenambassadeur", r"wielren",
    )),
    ("auto_fiets_motor", "Auto, fiets, motor & boot", (
        r"\bauto\b", r"\bautos\b", r"auto-?onderdel", r"autoparts", r"automotive", r"\bmotor",
        r"\bfiets", r"scooter", r"\bbanden\b", r"\bboot\b", r"\bboten\b", r"maritiem", r"caravan",
        r"e-?bike", r"\bschroot", r"\bvelgen", r"\bbrommer", r"trailer", r"aanhanger", r"\bscheeps",
        r"\bwatersport", r"\bjachtlak",
        r"pleziervaart", r"\bzeilen\b", r"zeilboot", r"zeilschip", r"jachtbouw",
    )),
    ("boeken_kantoor_cadeau", "Boeken, kantoor, cadeaus & bloemen", (
        r"\bboek", r"uitgever", r"\bkantoor", r"\bpapier", r"wenskaart", r"kaarten\b", r"kaartje",
        r"cadeau", r"\bgift", r"bloemen", r"\bboeket", r"\bfeest", r"fotoboek", r"drukwerk",
        r"stationery", r"\bkunst", r"\bposter", r"schilderij", r"\bmuziek", r"\bfilm", r"\bgeschenk",
        r"\bkado", r"\bfoto's?\b", r"\bfotos?\b", r"\bspellen\b", r"verpakking", r"\bballon",
        r"\bdvd", r"\bvinyl", r"graveer", r"gravure", r"gepersonaliseerd", r"stickers?\b", r"museum",
        r"\bpennen?\b", r"\brouw", r"kerstpakket",
        r"fotoservice", r"smartphoto", r"\bdarts?\b",
    )),
    ("dieren", "Dieren", (
        r"\bdier", r"\bhond", r"\bkat(?:ten)?\b", r"\bpaard", r"\bvogel", r"aquarium", r"huisdier",
        r"\bvee\b", r"diervoeding", r"\bkonijn", r"\bhamster", r"\bvijver",
    )),
    ("algemeen", "Warenhuis & algemeen", (
        (r"\bbol\b", 3), (r"\bamazon\b", 3), r"warenhuis", r"groupdeal", r"\bdeals?\b", r"dagelijkse aanbiedingen", r"\bmarktplaats",
        r"veelzijdig", r"alles voor", r"\bdiscount",
        r"retailorganisatie", r"\bone-stop-shop", r"lokale winkelier",
    )),
    ("diensten", "Diensten (geen producten)", (
        # a pattern with a weight of 2 is enough on its own: "verzekering" is not a shop for goods
        (r"(?<!inmeet)verzekering", 2), (r"verzekeraar", 2), (r"hypotheek", 2), (r"dating", 2), (r"matchmak", 2),
        (r"vakantiehuis|vakantiewoning|vakantievilla|vakantiepark|bungalow", 2),
        (r"travel agency|reisbureau|citytrip|groepsreis|singlereis|\btours\b", 2),
        (r"\btickets?\b", 2), (r"huurwoning|huurder", 2), (r"reserveringsplatform|vergelijkingssite", 2),
        (r"behandelcentrum", 2), (r"\bglasvezel", 2), (r"\binternet ?provider", 2), (r"\bbeleggen", 2),
        (r"\btui\b", 2), (r"treinkaart", 2), (r"\bdienstverlen", 2), (r"financiele diensten", 2),
        r"energie", r"\blening", r"\bsparen", r"abonnement", r"\breizen\b", r"vakantie",
        r"\bplatform", r"cursus", r"opleiding", r"\buitjes", r"\bevents?\b", r"\blease",
        r"financiel", r"\bsingles\b", r"\btests?\b", r"assessment", r"verhuur", r"\bpremie\b",
    )),
)

LABELS = {code: label for code, label, _ in RULES}
LABELS[OVERIG] = "Overig"


def _compile(pattern):
    """(regex, weight): a pattern is a plain regex (weight 1) or a (regex, weight) pair."""
    regex, weight = pattern if isinstance(pattern, tuple) else (pattern, 1)
    return re.compile(regex), weight


_COMPILED = tuple((code, tuple(_compile(p) for p in patterns)) for code, _label, patterns in RULES)


def fold(text):
    """'Café-Bar' -> 'cafe-bar': lower case, accents removed."""
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return plain.lower()


def scores(text):
    """{category code: number of different keywords found} for the codes with at least one."""
    folded = fold(text)
    found = {}
    for code, patterns in _COMPILED:
        hits = sum(weight for pattern, weight in patterns if pattern.search(folded))
        if hits:
            found[code] = hits
    return found


def classify(name="", slug="", description=""):
    """The (at most two) category codes of a shop, best first; (OVERIG,) when nothing fits."""
    text = " ".join([str(name or ""), str(slug or "").replace("-", " "), str(description or "")])
    found = scores(text)
    if not found:
        return (OVERIG,)
    order = {code: i for i, (code, _l, _p) in enumerate(RULES)}
    ranked = sorted(found, key=lambda code: (-found[code], order[code]))
    best = found[ranked[0]]
    chosen = [ranked[0]]
    for code in ranked[1:MAX_CATEGORIES]:
        if found[code] >= 2 and found[code] >= SECOND_SHARE * best:
            chosen.append(code)
    return tuple(chosen)


def categorize_rows(rows, members):
    """Rows (dicts with `webshop_slug`) with a `categorie` column: the codes joined by "|".

    `members` maps slug -> (name, description), the way the owner's page-reading export keeps it.
    A slug without a description is classified on its slug alone.
    """
    out = []
    for row in rows:
        name, description = members.get(row["webshop_slug"], ("", ""))
        codes = classify(name, row["webshop_slug"], description)
        out.append({**row, "categorie": "|".join(codes)})
    return out
