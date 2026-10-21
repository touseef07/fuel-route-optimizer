"""
External calls: OSRM for routing (exactly 1 call per new route) and,
only when the input is not "lat,lng" or a known "City, ST", Nominatim
for geocoding. Both are free and need no API key.
"""

import contextvars
import hashlib
import re

import requests
from django.conf import settings
from django.core.cache import cache

from . import places
from .geo import METERS_PER_MILE, is_in_usa


class LocationError(ValueError):
    """The input could not be resolved to a point in the USA."""


class RoutingError(RuntimeError):
    """The routing provider failed or found no route."""


_LATLNG = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Counts real outbound HTTP calls for the current request (reported in the response).
external_calls = contextvars.ContextVar("external_calls", default=0)

_session = requests.Session()
_session.headers["User-Agent"] = settings.HTTP_USER_AGENT


def _cache_key(prefix, raw):
    return f"{prefix}:{hashlib.sha1(raw.encode()).hexdigest()}"


def geocode(text):
    """
    Resolve user input to {"query", "label", "lat", "lng", "source"}.

    Order (cheapest first): "lat,lng" -> local Census index -> Nominatim.
    """
    text = (text or "").strip()
    if not text:
        raise LocationError("Location is empty.")

    match = _LATLNG.match(text)
    if match:
        lat, lng = float(match.group(1)), float(match.group(2))
        source, label = "coordinates", text
    else:
        city_state = places.parse_city_state(text)
        point = places.lookup(*city_state) if city_state else None
        if point:
            lat, lng = point
            source, label = "local_index", f"{city_state[0]}, {city_state[1]}"
        else:
            if places.foreign_region(text):
                raise LocationError(f"'{text}' is not in the USA.")
            lat, lng, label = _nominatim(text)
            source = "nominatim"

    if not is_in_usa(lat, lng):
        raise LocationError(f"'{text}' is not inside the (drivable) USA.")
    return {"query": text, "label": label, "lat": round(lat, 6), "lng": round(lng, 6), "source": source}


def _nominatim(text):
    key = _cache_key("geo", text.lower())
    hit = cache.get(key)
    if hit:
        return hit
    external_calls.set(external_calls.get() + 1)
    try:
        resp = _session.get(
            settings.NOMINATIM_URL,
            params={"q": text, "format": "jsonv2", "limit": 1, "countrycodes": "us"},
            timeout=settings.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as exc:
        raise LocationError(f"Could not geocode '{text}': {exc}") from exc
    if not results:
        raise LocationError(f"Could not find '{text}' in the USA.")
    best = results[0]
    value = (float(best["lat"]), float(best["lon"]), best.get("display_name", text))
    cache.set(key, value, timeout=None)
    return value


def get_route(start, finish):
    """
    One OSRM request -> {"distance_miles", "duration_hours", "coordinates": [(lat, lng), ...]}.
    Results are cached, so repeating a query makes zero external calls.
    """
    coords = f"{start['lng']},{start['lat']};{finish['lng']},{finish['lat']}"
    key = _cache_key("route", coords)
    hit = cache.get(key)
    if hit:
        return hit

    external_calls.set(external_calls.get() + 1)
    try:
        resp = _session.get(
            f"{settings.OSRM_URL}/route/v1/driving/{coords}",
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
            timeout=settings.HTTP_TIMEOUT,
        )
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc
    if resp.status_code != 200 or data.get("code") != "Ok" or not data.get("routes"):
        raise RoutingError(f"No drivable route found ({data.get('message') or data.get('code')}).")

    route = data["routes"][0]
    value = {
        "distance_miles": route["distance"] / METERS_PER_MILE,
        "duration_hours": route["duration"] / 3600,
        # GeoJSON is [lng, lat]; we work in (lat, lng) internally.
        "coordinates": [(lat, lng) for lng, lat in route["geometry"]["coordinates"]],
    }
    cache.set(key, value, timeout=settings.ROUTE_CACHE_SECONDS)
    return value
