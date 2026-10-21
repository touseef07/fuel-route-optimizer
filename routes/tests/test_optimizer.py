from django.test import SimpleTestCase

from routes.services.geo import haversine_miles, resample_line
from routes.services.optimizer import NoFeasiblePlan, plan_fuel_stops
from routes.services.places import foreign_region, name_variants, normalize, parse_city_state
from routes.services.stations import RouteStation, Station, StationIndex


def rs(sid, mile, price):
    # Distinct coordinates per station so they aren't merged as "same town".
    return RouteStation(Station(sid, sid, f"S{sid}", "", "Town", "TX", price, float(sid), 0.0), mile, 0.0)


class OptimizerTests(SimpleTestCase):
    def plan(self, stations, total, **kw):
        return plan_fuel_stops(stations, total, max_range_miles=500, mpg=10, **kw)

    def ids(self, plan):
        return [s.route_station.station.id for s in plan.stops]

    def test_empty_tank_pays_for_every_mile(self):
        p = self.plan([rs(1, 0, 3.0), rs(2, 400, 2.0)], 800)
        self.assertAlmostEqual(p.total_gallons, 80)  # 800 mi / 10 mpg

    def test_uses_cheap_station_for_the_longest_leg(self):
        # 900 mi: start at #1, the cheap #2 at mile 300 can cover up to mile 800.
        p = self.plan([rs(1, 0, 4.0), rs(2, 300, 3.0), rs(3, 500, 3.9), rs(4, 800, 5.0)], 900)
        self.assertEqual(self.ids(p), [1, 2, 4])
        self.assertAlmostEqual(p.total_cost, 30 * 4 + 50 * 3 + 10 * 5)

    def test_stop_penalty_avoids_tiny_top_ups(self):
        stations = [rs(1, 0, 3.00), rs(2, 100, 2.99), rs(3, 400, 3.00)]
        cheapest = self.plan(stations, 700, stop_penalty=0)
        realistic = self.plan(stations, 700, stop_penalty=10)
        self.assertEqual(self.ids(cheapest), [1, 2, 3])
        self.assertEqual(self.ids(realistic), [1, 3])
        self.assertLess(cheapest.total_cost, realistic.total_cost)

    def test_merges_stations_in_same_town_keeping_cheapest(self):
        a = RouteStation(Station(1, 1, "A", "", "T", "TX", 3.5, 1.0, 1.0), 0, 0)
        b = RouteStation(Station(2, 2, "B", "", "T", "TX", 3.1, 1.0, 1.0), 0, 0)
        p = self.plan([a, b], 300)
        self.assertEqual(self.ids(p), [2])

    def test_full_tank_short_trip_needs_no_stops(self):
        p = self.plan([rs(1, 100, 3.0)], 400, start_full=True)
        self.assertEqual(p.stops, [])

    def test_full_tank_long_trip(self):
        p = self.plan([rs(1, 100, 9.0), rs(2, 450, 3.0)], 900, start_full=True)
        self.assertEqual(self.ids(p), [2])
        # Arrive at mile 450 with 50 miles of fuel left; need 450 more.
        self.assertAlmostEqual(p.total_gallons, 40)

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(NoFeasiblePlan):
            self.plan([rs(1, 0, 3.0), rs(2, 600, 3.0)], 900)

    def test_no_station_near_start(self):
        with self.assertRaises(NoFeasiblePlan):
            self.plan([rs(1, 120, 3.0)], 300)


class GeoTests(SimpleTestCase):
    def test_haversine(self):
        # Chicago -> Houston is ~940 miles as the crow flies
        self.assertAlmostEqual(haversine_miles(41.88, -87.63, 29.76, -95.37), 940, delta=10)

    def test_resample_spacing(self):
        line = [(30.0, -100.0), (31.0, -100.0)]  # ~69 miles
        pts = resample_line(line, 10)
        self.assertEqual(len(pts), 8)
        self.assertAlmostEqual(pts[-1][2], 69.1, delta=0.2)

    def test_station_index_matches_nearby_only(self):
        near = Station(1, 1, "near", "", "", "TX", 3.0, 30.5, -100.05)
        far = Station(2, 2, "far", "", "", "TX", 3.0, 30.5, -101.0)
        idx = StationIndex([near, far])
        found = idx.along_route(resample_line([(30.0, -100.0), (31.0, -100.0)], 1), 10)
        self.assertEqual([f.station.id for f in found], [1])
        self.assertAlmostEqual(found[0].route_mile, 34.5, delta=1.5)


class PlacesTests(SimpleTestCase):
    def test_parse_city_state(self):
        self.assertEqual(parse_city_state("Chicago, IL"), ("Chicago", "IL"))
        self.assertEqual(parse_city_state("Salt Lake City, Utah"), ("Salt Lake City", "UT"))
        self.assertEqual(parse_city_state("Austin TX, USA"), ("Austin", "TX"))
        self.assertIsNone(parse_city_state("1600 Pennsylvania Ave"))

    def test_foreign_regions(self):
        self.assertTrue(foreign_region("Toronto, ON"))
        self.assertTrue(foreign_region("Paris, FR"))
        self.assertFalse(foreign_region("Austin, TX"))
        self.assertFalse(foreign_region("1600 Pennsylvania Ave NW, Washington, DC"))
        self.assertFalse(foreign_region("350 5th Ave, New York, NY 10118"))

    def test_census_name_variants(self):
        self.assertEqual(name_variants("Salt Lake City city"), ["Salt Lake City"])
        self.assertEqual(name_variants("Indianapolis city (balance)"), ["Indianapolis"])
        self.assertIn("Nashville", name_variants("Nashville-Davidson metropolitan government (balance)"))
        self.assertEqual(normalize("St. Louis"), normalize("Saint Louis"))
