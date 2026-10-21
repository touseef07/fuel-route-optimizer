"""Small, dependency-free geometry helpers (miles, degrees)."""

import math

EARTH_RADIUS_MILES = 3958.8
METERS_PER_MILE = 1609.344


def haversine_miles(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


# Rough bounding boxes for the drivable USA (lower 48 + Alaska).
# Hawaii is excluded on purpose: you cannot drive there from the mainland.
US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),  # contiguous US
    (51.0, 71.5, -170.0, -129.9),  # Alaska
)


def is_in_usa(lat, lng):
    return any(s <= lat <= n and w <= lng <= e for s, n, w, e in US_BOXES)


def resample_line(coords, step_miles):
    """
    Walk a [(lat, lng), ...] polyline and return points roughly every
    `step_miles`, each as (lat, lng, miles_from_start).

    OSRM returns thousands of vertices (dense in cities, sparse on highways);
    resampling gives an even, much smaller set to match stations against.
    """
    if not coords:
        return []
    samples = [(coords[0][0], coords[0][1], 0.0)]
    travelled = 0.0
    since_last = 0.0
    for (lat1, lng1), (lat2, lng2) in zip(coords, coords[1:]):
        seg = haversine_miles(lat1, lng1, lat2, lng2)
        if seg == 0:
            continue
        # Insert evenly spaced points inside long segments too.
        pos = 0.0
        while since_last + (seg - pos) >= step_miles:
            pos += step_miles - since_last
            t = pos / seg
            samples.append((lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t, travelled + pos))
            since_last = 0.0
        since_last += seg - pos
        travelled += seg
    last = coords[-1]
    if samples[-1][2] < travelled:
        samples.append((last[0], last[1], travelled))
    return samples
