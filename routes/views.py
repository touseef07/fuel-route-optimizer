import json
from urllib.parse import urlencode

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .services.optimizer import NoFeasiblePlan
from .services.planner import build_trip
from .services.routing import LocationError, RoutingError


def _error(status, message):
    return JsonResponse({"error": message}, status=status)


def _number(params, name, low, high):
    """Optional numeric param -> float or None (use default)."""
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be a number.") from None
    if not low <= value <= high:
        raise ValueError(f"'{name}' must be between {low:g} and {high:g}.")
    return value


# Stateless JSON API (no cookie auth), so CSRF protection doesn't apply.
@method_decorator(csrf_exempt, name="dispatch")
class RouteView(View):
    """
    GET  /api/route/?start=Chicago, IL&finish=Houston, TX[&start_tank=full][&max_detour=10][&stop_penalty=10]
    POST /api/route/  {"start": "...", "finish": "...", "start_tank": "empty"|"full",
                       "max_detour": 10, "stop_penalty": 10}

    start / finish accept "City, ST", "lat,lng" or any US address.
    """

    def get(self, request):
        return self._handle(request.GET)

    def post(self, request):
        try:
            data = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return _error(400, "Body must be valid JSON.")
        if not isinstance(data, dict):
            return _error(400, "Body must be a JSON object.")
        return self._handle(data)

    def _handle(self, params):
        start, finish = str(params.get("start", "")).strip(), str(params.get("finish", "")).strip()
        if not start or not finish:
            return _error(400, "Both 'start' and 'finish' are required.")

        start_tank = str(params.get("start_tank", "empty")).lower()
        if start_tank not in ("empty", "full"):
            return _error(400, "'start_tank' must be 'empty' or 'full'.")

        try:
            max_detour = _number(params, "max_detour", low=0.5, high=50)
            stop_penalty = _number(params, "stop_penalty", low=0, high=1000)
        except ValueError as exc:
            return _error(400, str(exc))

        try:
            trip = build_trip(
                start, finish,
                start_full=start_tank == "full",
                max_detour_miles=max_detour,
                stop_penalty=stop_penalty,
            )
        except LocationError as exc:
            return _error(400, str(exc))
        except NoFeasiblePlan as exc:
            return _error(422, str(exc))
        except RoutingError as exc:
            return _error(502, str(exc))

        # Same query, rendered on an interactive map (served from cache: no extra routing call).
        query = {k: params[k] for k in ("start", "finish", "start_tank", "max_detour", "stop_penalty") if params.get(k)}
        trip["map"] = {"url": self.request.build_absolute_uri(f"{reverse('map')}?{urlencode(query)}"), **trip["map"]}
        return JsonResponse(trip)


def map_view(request):
    """Interactive Leaflet map; it calls the API above (cached, so no extra routing call)."""
    return render(request, "routes/map.html")
