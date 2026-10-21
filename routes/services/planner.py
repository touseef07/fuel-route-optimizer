"""Orchestrates one request: geocode -> route (1 external call) -> match stations -> optimise."""

import time

from django.conf import settings

from . import routing
from .geo import resample_line
from .optimizer import plan_fuel_stops
from .stations import get_index


def build_trip(start_text, finish_text, start_full=False, max_detour_miles=None, stop_penalty=None):
    t0 = time.perf_counter()
    routing.external_calls.set(0)
    max_detour = settings.FUEL["MAX_DETOUR_MILES"] if max_detour_miles is None else max_detour_miles
    stop_penalty = settings.FUEL["STOP_PENALTY_USD"] if stop_penalty is None else stop_penalty

    start = routing.geocode(start_text)
    finish = routing.geocode(finish_text)
    route = routing.get_route(start, finish)
    t_route = time.perf_counter()

    # Scale our haversine mileage to OSRM's road distance so mile markers line up.
    samples = resample_line(route["coordinates"], settings.FUEL["SAMPLE_STEP_MILES"])
    geo_len = samples[-1][2] if samples else 0
    scale = route["distance_miles"] / geo_len if geo_len else 1.0
    samples = [(lat, lng, mile * scale) for lat, lng, mile in samples]

    candidates = get_index().along_route(samples, max_detour)
    plan = plan_fuel_stops(
        candidates,
        total_miles=route["distance_miles"],
        max_range_miles=settings.FUEL["MAX_RANGE_MILES"],
        mpg=settings.FUEL["MPG"],
        start_full=start_full,
        start_window_miles=settings.FUEL["START_WINDOW_MILES"],
        stop_penalty=stop_penalty,
    )
    t_done = time.perf_counter()

    stops = [_stop_json(n, s) for n, s in enumerate(plan.stops, start=1)]
    line = [[round(lng, 5), round(lat, 5)] for lat, lng, _ in samples]
    return {
        "start": start,
        "finish": finish,
        "route": {
            "distance_miles": round(route["distance_miles"], 1),
            "duration_hours": round(route["duration_hours"], 2),
        },
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost_usd": round(plan.total_cost, 2),
            "total_gallons": round(plan.total_gallons, 2),
            "number_of_stops": len(stops),
            "average_price_per_gallon": round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else None,
            "assumptions": {
                "mpg": settings.FUEL["MPG"],
                "max_range_miles": settings.FUEL["MAX_RANGE_MILES"],
                "start_tank": "full" if start_full else "empty",
                "max_detour_miles": max_detour,
                "stop_penalty_usd": stop_penalty,
            },
        },
        "map": {
            "geojson": _geojson(start, finish, line, stops),
        },
        "meta": {
            "external_api_calls": routing.external_calls.get(),
            "stations_near_route": len(candidates),
            "timing_ms": {
                "geocode_and_route": round((t_route - t0) * 1000, 1),
                "optimise": round((t_done - t_route) * 1000, 1),
                "total": round((t_done - t0) * 1000, 1),
            },
        },
    }


def _stop_json(n, stop):
    rs = stop.route_station
    s = rs.station
    return {
        "stop": n,
        "station_id": s.id,
        "opis_id": s.opis_id,
        "name": s.name,
        "address": s.address,
        "city": s.city,
        "state": s.state,
        "lat": s.lat,
        "lng": s.lng,
        "price_per_gallon": round(s.price, 3),
        "route_mile": round(rs.route_mile, 1),
        "detour_miles": round(rs.detour_miles, 1),
        "leg_miles": round(stop.leg_miles, 1),
        "gallons": round(stop.gallons, 2),
        "cost_usd": round(stop.cost, 2),
    }


def _geojson(start, finish, line, stops):
    def point(lat, lng, props):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": props}

    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": line}, "properties": {"kind": "route"}},
            point(start["lat"], start["lng"], {"kind": "start", "label": start["label"]}),
            point(finish["lat"], finish["lng"], {"kind": "finish", "label": finish["label"]}),
            *[point(s["lat"], s["lng"], {"kind": "fuel_stop", **s}) for s in stops],
        ],
    }
