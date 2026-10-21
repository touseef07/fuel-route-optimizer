"""
Build data/us_places.json.gz from the free US Census Gazetteer files.

Run once (the output is committed to the repo):
    python manage.py build_places
"""

import csv
import gzip
import io
import json
import zipfile

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from routes.services.places import name_variants, normalize

SOURCES = [
    # Incorporated places + CDPs first (preferred), then county subdivisions (towns/townships).
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip",
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_cousubs_national.zip",
]


class Command(BaseCommand):
    help = "Download US Census Gazetteer files and build the offline place index."

    def handle(self, *args, **options):
        index = {}
        for url in SOURCES:
            self.stdout.write(f"Downloading {url}")
            resp = requests.get(url, timeout=120, headers={"User-Agent": settings.HTTP_USER_AGENT})
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                name = next(n for n in zf.namelist() if n.endswith(".txt"))
                text = zf.read(name).decode("utf-8", errors="replace")
            added = 0
            for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
                row = {k.strip(): (v or "").strip() for k, v in row.items()}
                table = index.setdefault(row["USPS"], {})
                point = [round(float(row["INTPTLAT"]), 5), round(float(row["INTPTLONG"]), 5)]
                for variant in name_variants(row["NAME"]):
                    key = normalize(variant)
                    if key and key not in table:
                        table[key] = point
                        added += 1
            self.stdout.write(f"  +{added} names")

        out = settings.DATA_DIR / "us_places.json.gz"
        with gzip.open(out, "wt", encoding="utf-8") as fh:
            json.dump(index, fh, separators=(",", ":"))
        total = sum(len(t) for t in index.values())
        self.stdout.write(self.style.SUCCESS(f"Wrote {total} place names to {out}"))
