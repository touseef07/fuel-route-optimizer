# Fuel Route Optimizer

A Django 6.1 API that takes a start and a finish location in the USA and returns:

- the driving route, as GeoJSON plus a link to an interactive map,
- where to fuel up along the way, choosing cheap stations from the provided price list,
- the total fuel cost for a vehicle with a **500-mile range** that gets **10 MPG**.

Each request makes **one** call to the routing API, and none when the route is cached. Responses take about 20–100 ms plus the routing call (~0.2–1.2 s on the first request for a route).

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate   # Python 3.12+
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations      # imports the CSV, works offline, ~2 s
python manage.py runserver
```

- API: <http://127.0.0.1:8000/api/route/?start=Chicago,%20IL&finish=Los%20Angeles,%20CA>
- Map: <http://127.0.0.1:8000/map/?start=Chicago,%20IL&finish=Los%20Angeles,%20CA>
- Postman: import `postman/fuel_route_optimizer.postman_collection.json`
- Tests: `python manage.py test routes` (no network needed)

## API

`GET /api/route/?start=...&finish=...` or `POST /api/route/` with a JSON body.

| param | required | description |
|---|---|---|
| `start`, `finish` | yes | `"City, ST"`, `"lat,lng"` or any US street address |
| `start_tank` | no | `empty` (default): you pay for every mile driven. `full`: you leave with a full tank and pay only for fuel bought on the way |
| `stop_penalty` | no | USD cost assigned to each stop, standing in for the driver's time (default `10`). `0` gives the absolute cheapest plan |
| `max_detour` | no | ignore stations farther than this many miles from the route (default `10`) |

Example response (trimmed):

```json
{
  "start":  {"label": "Chicago, IL", "lat": 41.837, "lng": -87.685, "source": "local_index"},
  "finish": {"label": "Los Angeles, CA", "...": "..."},
  "route":  {"distance_miles": 2028.9, "duration_hours": 35.65},
  "fuel_stops": [
    {"stop": 1, "name": "Gulf", "city": "Bensenville", "state": "IL", "route_mile": 15.0,
     "price_per_gallon": 3.059, "gallons": 48.26, "cost_usd": 147.64, "leg_miles": 482.6,
     "lat": 41.958, "lng": -87.944, "detour_miles": 6.2, "...": "..."}
  ],
  "summary": {"total_fuel_cost_usd": 632.66, "total_gallons": 202.89, "number_of_stops": 5,
              "average_price_per_gallon": 3.118, "assumptions": {"mpg": 10, "max_range_miles": 500}},
  "map":  {"url": "http://127.0.0.1:8000/map/?start=...", "geojson": {"type": "FeatureCollection", "...": "..."}},
  "meta": {"external_api_calls": 1, "stations_near_route": 269, "timing_ms": {"total": 100.0}}
}
```

Errors: `400` for invalid input or a location outside the USA, `422` if the route has a gap longer than 500 miles with no station, `502` if the routing provider fails.

## How it works

```
request ─► geocode start/finish ─► OSRM route (1 call, cached) ─► resample route every 1 mi
        ─► find stations within 10 mi (in-memory grid index) ─► DP optimiser ─► JSON + GeoJSON
```

1. **Free services, no API keys.** Routing uses [OSRM](https://project-osrm.org/) (OpenStreetMap). Map tiles come from OpenStreetMap through Leaflet. Geocoding uses [Nominatim](https://nominatim.org/), but only as a fallback.
2. **Few external calls.**
   - `"City, ST"` and `"lat,lng"` inputs are geocoded locally from a US Census Gazetteer index (`data/us_places.json.gz`, 48k place names), so a typical request makes exactly **one** call, to OSRM.
   - Street addresses add one Nominatim call each.
   - Routes and geocodes are cached, so repeating a query makes zero calls.
3. **Station coordinates.** The CSV has only city and state for each station.
   - `load_stations` matches each station's town against the Census index, which locates 97%.
   - The remaining towns were geocoded once with Nominatim (`load_stations --nominatim`) and cached in `data/city_geocode_cache.json`.
   - Together that covers 6,849 of 6,855 US stations. Canadian rows are skipped.
   - Duplicate rows keep their lowest price.
4. **Matching stations to the route.**
   - All stations are loaded into memory once and bucketed into a 0.25° lat/lng grid.
   - The route is resampled to one point per mile, and each point checks only the nearby grid cells.
   - Each station gets a *route mile*, its distance along the route, and a detour distance.
5. **Choosing stops (`routes/services/optimizer.py`).** Dynamic programming over the stations sorted by route mile:

   `best[k] = min over stations i within 500 mi before k of: best[i] + (gallons from i to k) × price[i] + stop_penalty`

   Each stop buys exactly the fuel needed to reach the next stop. Runtime is O(n·w), a few milliseconds. The stop penalty drops plans with many tiny "save 3 cents" top-ups. Chicago → LA takes 5 stops, the minimum that a 500-mile range allows.

## Assumptions & trade-offs

- **Empty tank at the start (default).** The first fill-up is within 50 miles of the start, and the total cost covers every mile driven (`total_gallons = distance / 10`). Use `start_tank=full` for the other interpretation.
- **Approximate station locations.** Coordinates are at town level, because the CSV has no exact addresses. That is why stations within 10 miles of the route count as "on route". Detours are reported but not added to the fuel used.
- **Production changes.** The public OSRM demo server is fine for an assessment. In production, use a self-hosted OSRM (`OSRM_URL` env var), Redis for the cache and PostgreSQL.

## Project layout

```
config/                     Django settings & urls
routes/
  models.py                 FuelStation
  views.py                  /api/route/ (GET/POST) and /map/
  services/
    routing.py              OSRM + geocoding client (cached, counts external calls)
    places.py               offline Census place index ("City, ST" -> lat/lng)
    stations.py             in-memory spatial grid of stations
    optimizer.py            DP fuel-stop optimiser
    planner.py              orchestrates a request
    geo.py                  haversine, route resampling, USA bounds
  management/commands/
    build_places.py         builds data/us_places.json.gz from Census Gazetteer
    load_stations.py        imports the CSV and attaches coordinates
  templates/routes/map.html Leaflet map
  tests/                    optimiser, geo and API tests (network mocked)
data/                       fuel prices CSV, place index, geocode cache
postman/                    Postman collection
```
