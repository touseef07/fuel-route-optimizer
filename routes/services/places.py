"""
Offline US place lookup ("City, ST" -> lat/lng).

The index is built once from the free US Census Gazetteer files by the
`build_places` management command and committed as data/us_places.json.gz.
Using it means:
  * fuel stations (CSV has only city/state, no coordinates) get coordinates
    without hammering a geocoding API, and
  * API requests like "Chicago, IL" are geocoded locally with zero network calls.
"""

import gzip
import json
import re
from functools import lru_cache

from django.conf import settings

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california",
    "CO": "colorado", "CT": "connecticut", "DE": "delaware", "DC": "district of columbia",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho", "IL": "illinois",
    "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}
STATE_BY_NAME = {name: code for code, name in US_STATES.items()}

# Census names carry a legal suffix: "Austin city", "Hempstead town", "Ruther Glen CDP".
_SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"charter township|township|city|town|village|borough|cdp|ccd|municipality|plantation|"
    r"urban county|unorganized territory|gore|grant|location|purchase)$",
    re.I,
)
_ABBREVIATIONS = (
    (" saint ", " st "), (" st. ", " st "), (" ste. ", " ste "),
    (" ft. ", " fort "), (" ft ", " fort "), (" mt. ", " mount "), (" mt ", " mount "),
)


def normalize(name):
    s = f" {name.lower().strip()} "
    for a, b in _ABBREVIATIONS:
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", s)


def name_variants(census_name):
    """
    'Salt Lake City city' -> ['Salt Lake City'];
    'Nashville-Davidson metropolitan government (balance)' -> ['Nashville-Davidson', 'Nashville'].
    """
    name = re.sub(r"\s*\(.*?\)", "", census_name).strip()
    name = _SUFFIX.sub("", name).strip()
    variants = [name]
    if "-" in name:
        variants.append(name.split("-")[0])
    return variants


@lru_cache(maxsize=1)
def load_index():
    """{'TX': {'austin': [lat, lng], ...}, ...}"""
    path = settings.DATA_DIR / "us_places.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def lookup(city, state):
    """Return (lat, lng) or None."""
    state = (state or "").strip().upper()
    if len(state) != 2:
        state = STATE_BY_NAME.get(state.lower(), state)
    table = load_index().get(state)
    if not table:
        return None
    hit = table.get(normalize(city))
    return tuple(hit) if hit else None


def parse_city_state(text):
    """'Chicago, IL' / 'Chicago, Illinois' / 'Chicago IL' -> ('Chicago', 'IL') or None."""
    text = re.sub(r",?\s*(usa|us|united states)$", "", text.strip(), flags=re.I)
    if "," in text:
        city, _, state = text.rpartition(",")
    else:
        city, _, state = text.rpartition(" ")
    state = state.strip()
    code = state.upper() if state.upper() in US_STATES else STATE_BY_NAME.get(state.lower())
    if not code or not city.strip():
        return None
    return city.strip(), code


CANADIAN_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"}


def foreign_region(text):
    """True for inputs like 'Toronto, ON' or 'Paris, France' that name a non-US region."""
    tail = text.rsplit(",", 1)[-1].strip() if "," in text else ""
    if not tail or tail.upper() in US_STATES or tail.lower() in STATE_BY_NAME:
        return False
    if tail.lower() in ("usa", "us", "united states", "united states of america") or tail[:1].isdigit():
        return False
    return tail.upper() in CANADIAN_PROVINCES or (len(tail) == 2 and tail.isalpha())
