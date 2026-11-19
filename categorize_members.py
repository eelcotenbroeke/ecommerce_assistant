"""Offline: write the `categorie` column of thuiswinkel_leden.csv from the members' descriptions.

    python categorize_members.py thuiswinkel-omschrijvingen.json

The JSON is the owner's export of the "Informatie" text of each member's Thuiswinkel Waarborg
profile page: {"leden": {slug: [name, description]}}. The descriptions themselves are not kept in
the repo; only the categories (rules in categories.py) end up in the CSV.
"""
import csv
import json
import sys
from pathlib import Path

import categories
import members


def rewrite(csv_path, json_path):
    """Rewrite the CSV in place with a `categorie` column; returns the number of rows."""
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = [{"webshop_slug": r["webshop_slug"], "domain": r["domain"]} for r in csv.DictReader(handle)]
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    rows = categories.categorize_rows(rows, data["leden"])
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["webshop_slug", "domain", "categorie"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(f"{rewrite(members.LIST_PATH, argv[1])} winkels ingedeeld")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
