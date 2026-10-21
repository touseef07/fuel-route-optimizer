"""
In-memory spatial index of fuel stations.

~7k stations are loaded from the DB once per process and bucketed into a
lat/lng grid. Matching a route is then a few dict lookups per route sample
instead of a DB query or a distance check against every station.
"""

import math
import threading
from collections import defaultdict
from dataclasses import dataclass

from .geo import haversine_miles

CELL_DEG = 0.25  # ~17 miles of latitude per cell


@dataclass(frozen=True)
class Station:
    id: int
    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lng: float


@dataclass(frozen=True)
class RouteStation:
    station: Station
    route_mile: float  # distance from the start, along the route
    detour_miles: float  # straight-line distance from the route


def _cell(lat, lng):
    return math.floor(lat / CELL_DEG), math.floor(lng / CELL_DEG)


class StationIndex:
    def __init__(self, stations):
        self.size = len(stations)
        self.grid = defaultdict(list)
        for s in stations:
            self.grid[_cell(s.lat, s.lng)].append(s)

    def along_route(self, samples, max_detour_miles):
        """
        samples: [(lat, lng, route_mile), ...] evenly spaced along the route.
        Returns stations within `max_detour_miles` of the route, each mapped to
        the route point it is closest to, sorted by route_mile.
        """
        reach = max(1, math.ceil(max_detour_miles / (CELL_DEG * 69.0)))
        best = {}  # station.id -> (detour, route_mile, station)
        for lat, lng, mile in samples:
            # Longitude cells shrink towards the poles; widen the search accordingly.
            lng_reach = math.ceil(reach / max(math.cos(math.radians(lat)), 0.2))
            ci, cj = _cell(lat, lng)
            for di in range(-reach, reach + 1):
                for dj in range(-lng_reach, lng_reach + 1):
                    for s in self.grid.get((ci + di, cj + dj), ()):
                        d = haversine_miles(lat, lng, s.lat, s.lng)
                        if d <= max_detour_miles:
                            prev = best.get(s.id)
                            if prev is None or d < prev[0]:
                                best[s.id] = (d, mile, s)
        found = [RouteStation(s, mile, d) for d, mile, s in best.values()]
        found.sort(key=lambda rs: (rs.route_mile, rs.station.price))
        return found


_index = None
_lock = threading.Lock()


def get_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                from routes.models import FuelStation

                rows = FuelStation.objects.filter(lat__isnull=False).values(
                    "id", "opis_id", "name", "address", "city", "state", "price", "lat", "lng"
                )
                _index = StationIndex([Station(**{**r, "price": float(r["price"])}) for r in rows])
    return _index


def reset_index():
    global _index
    _index = None
