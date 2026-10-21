"""
Load the fuel-price CSV into the database, attaching coordinates.

    python manage.py load_stations                 # offline: Census index + committed cache
    python manage.py load_stations --nominatim     # also geocode unknown cities (1 req/sec)

Coordinates are city-level: the CSV only gives city + state, and the
stations are highway truck stops in/near those towns.
"""

import csv
import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routes.models import FuelStation
from routes.services import places
from routes.services.geo import is_in_usa
from routes.services.stations import reset_index

CACHE_FILE = "city_geocode_cache.json"


class Command(BaseCommand):
    help = "Import fuel stations from the CSV (US only) and geocode them by city/state."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.DATA_DIR / "fuel_prices.csv"))
        parser.add_argument("--nominatim", action="store_true", help="Geocode cities missing from the index.")

    def handle(self, *args, **opts):
        cache_path = settings.DATA_DIR / CACHE_FILE
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

        rows = {}
        skipped_non_us = 0
        with open(Path(opts["csv"]), newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                r = {k.strip(): (v or "").strip() for k, v in raw.items()}
                if r["State"] not in places.US_STATES:
                    skipped_non_us += 1  # Canadian provinces are in the file too
                    continue
                key = (int(r["OPIS Truckstop ID"]), r["Truckstop Name"], r["Address"])
                price = float(r["Retail Price"])
                # The file repeats some stations; keep the cheapest listing.
                if key not in rows or price < rows[key]["price"]:
                    rows[key] = {"city": r["City"], "state": r["State"], "rack": r["Rack ID"], "price": price}

        unresolved = set()
        stations = []
        for (opis_id, name, address), r in rows.items():
            point, source = places.lookup(r["city"], r["state"]), "census"
            if not point:
                cached = cache.get(f"{r['city']}|{r['state']}")
                point, source = (tuple(cached), "nominatim") if cached else (None, "")
            if not point:
                unresolved.add((r["city"], r["state"]))
            stations.append(FuelStation(
                opis_id=opis_id, name=name, address=address, city=r["city"], state=r["state"],
                rack_id=int(r["rack"]) if r["rack"].isdigit() else None, price=r["price"],
                lat=point[0] if point else None, lng=point[1] if point else None, geocode_source=source,
            ))

        if unresolved and opts["nominatim"]:
            self.stdout.write(f"Geocoding {len(unresolved)} cities with Nominatim (~1/sec)...")
            found = self._nominatim(sorted(unresolved), cache, cache_path)
            for s in stations:
                if s.lat is None and (s.city, s.state) in found:
                    s.lat, s.lng = found[(s.city, s.state)]
                    s.geocode_source = "nominatim"
                    unresolved.discard((s.city, s.state))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        reset_index()

        located = sum(1 for s in stations if s.lat is not None)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(stations)} US stations ({located} located, {len(stations) - located} without "
            f"coordinates); skipped {skipped_non_us} non-US rows."
        ))
        if unresolved:
            self.stdout.write(f"Unresolved cities: {len(unresolved)} (run with --nominatim to geocode them)")

    def _nominatim(self, cities, cache, cache_path):
        import requests

        found = {}
        session = requests.Session()
        session.headers["User-Agent"] = settings.HTTP_USER_AGENT
        for city, state in cities:
            try:
                resp = session.get(settings.NOMINATIM_URL, timeout=settings.HTTP_TIMEOUT, params={
                    "city": city, "state": places.US_STATES[state], "country": "USA",
                    "format": "jsonv2", "limit": 1,
                })
                hits = resp.json() if resp.ok else []
            except (requests.RequestException, ValueError):
                hits = []
            if hits:
                lat, lng = round(float(hits[0]["lat"]), 5), round(float(hits[0]["lon"]), 5)
                if is_in_usa(lat, lng):
                    found[(city, state)] = (lat, lng)
                    cache[f"{city}|{state}"] = [lat, lng]
            time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
        cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True))
        return found
