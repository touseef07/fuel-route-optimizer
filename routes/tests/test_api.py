from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from routes.models import FuelStation
from routes.services import routing
from routes.services.stations import reset_index

# A straight north-south "road" along lng -100 from lat 30 to lat 40 (~690 miles).
FAKE_ROUTE = {
    "distance_miles": 690.9,
    "duration_hours": 10.5,
    "coordinates": [(30.0 + i * 0.1, -100.0) for i in range(101)],
}


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_index()
        for i, (lat, price) in enumerate([(30.0, 3.50), (33.0, 2.90), (36.0, 3.40), (38.0, 3.10)]):
            FuelStation.objects.create(
                opis_id=i, name=f"Station {i}", address="I-1", city=f"Town{i}", state="TX",
                price=price, lat=lat, lng=-100.0,
            )
        patcher = mock.patch.object(routing, "get_route", side_effect=self._fake_route)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(reset_index)

    @staticmethod
    def _fake_route(start, finish):
        routing.external_calls.set(routing.external_calls.get() + 1)
        return FAKE_ROUTE

    def test_get_returns_plan_map_and_cost(self):
        res = self.client.get("/api/route/", {"start": "30,-100", "finish": "40,-100"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        summary = body["summary"]
        self.assertAlmostEqual(summary["total_gallons"], 69.09, places=1)  # 690.9 mi / 10 mpg
        self.assertEqual(summary["number_of_stops"], len(body["fuel_stops"]))
        self.assertAlmostEqual(summary["total_fuel_cost_usd"], sum(s["cost_usd"] for s in body["fuel_stops"]), places=1)
        # The cheap station (2.90) should carry the long middle leg.
        self.assertIn("Station 1", [s["name"] for s in body["fuel_stops"]])
        self.assertEqual(body["map"]["geojson"]["type"], "FeatureCollection")
        self.assertTrue(body["map"]["url"].startswith("http://testserver/map/?"))
        self.assertEqual(body["meta"]["external_api_calls"], 1)

    def test_post_json(self):
        res = self.client.post(
            "/api/route/", {"start": "30,-100", "finish": "40,-100", "start_tank": "full"},
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["summary"]["assumptions"]["start_tank"], "full")

    def test_validation_errors(self):
        self.assertEqual(self.client.get("/api/route/", {"start": "30,-100"}).status_code, 400)
        self.assertEqual(self.client.get("/api/route/", {"start": "48.85,2.35", "finish": "40,-100"}).status_code, 400)
        self.assertEqual(self.client.get("/api/route/", {"start": "Toronto, ON", "finish": "40,-100"}).status_code, 400)
        res = self.client.get("/api/route/", {"start": "30,-100", "finish": "40,-100", "stop_penalty": "x"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.post("/api/route/", "nope", content_type="application/json").status_code, 400)

    def test_unreachable_gap_returns_422(self):
        FuelStation.objects.exclude(name="Station 0").delete()
        reset_index()
        res = self.client.get("/api/route/", {"start": "30,-100", "finish": "40,-100"})
        self.assertEqual(res.status_code, 422)

    def test_map_page(self):
        res = self.client.get("/map/", {"start": "Chicago, IL", "finish": "Houston, TX"})
        self.assertContains(res, "leaflet")
