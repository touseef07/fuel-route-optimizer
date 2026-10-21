"""
Choose where to fuel along a fixed route.

Dynamic programming over the stations near the route, sorted by mile:

    best[k] = min over stops i within range behind k of
              best[i] + gallons(i -> k) * price[i] + stop_penalty

At each stop we buy exactly the fuel needed to reach the next stop (or the
finish), so every leg must be <= the vehicle's range. `stop_penalty` (USD)
stands for the driver's time per stop: with 0 you get the absolute
cheapest plan, which tends to include tiny "save 3 cents" top-ups; a few
dollars gives a realistic plan with roughly distance / range stops that still
uses cheap stations.

Complexity: O(n * w) where w = stations within one range (~100s): a few ms.
"""

from dataclasses import dataclass, field

EPS = 1e-9
INF = float("inf")


class NoFeasiblePlan(Exception):
    """A stretch of the route is longer than the vehicle's range with no station."""


@dataclass
class FuelStop:
    route_station: object  # stations.RouteStation
    gallons: float
    cost: float
    leg_miles: float  # miles this purchase covers


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    total_gallons: float = 0.0
    total_cost: float = 0.0


def plan_fuel_stops(
    route_stations,
    total_miles,
    max_range_miles,
    mpg,
    start_full=False,
    start_window_miles=50.0,
    stop_penalty=0.0,
):
    """
    route_stations: list[RouteStation] sorted by route_mile.
    start_full:  False -> leave with an empty tank; the first stop (within
                          `start_window_miles`) also pays for the miles driven
                          to reach it, so total cost covers every mile.
                 True  -> leave with a full tank; cost = fuel bought en route.
    """
    stations = _cheapest_per_location([rs for rs in route_stations if rs.route_mile < total_miles])
    n = len(stations)
    miles = [rs.route_mile for rs in stations]
    price = [rs.station.price for rs in stations]

    if start_full and total_miles <= max_range_miles + EPS:
        return FuelPlan()

    best = [INF] * n
    prev = [None] * n  # index of previous stop, or -1 for the start

    # Legs from the start.
    for k in range(n):
        if start_full:
            if miles[k] > max_range_miles + EPS:
                break
            best[k], prev[k] = stop_penalty, -1
        else:
            if miles[k] > start_window_miles + EPS:
                break
            # Fuel used to reach the first station is bought there.
            best[k], prev[k] = miles[k] / mpg * price[k] + stop_penalty, -1
    if not any(c < INF for c in best):
        where = f"{max_range_miles:.0f}" if start_full else f"{start_window_miles:.0f}"
        hint = "" if start_full else " Try start_tank=full."
        raise NoFeasiblePlan(f"No fuel station within {where} miles of the start.{hint}")

    # Station -> station legs (stations are sorted, so best[i] is final when we reach i).
    finish_cost, finish_prev = INF, None
    for i in range(n):
        if best[i] == INF:
            continue
        for k in range(i + 1, n):
            leg = miles[k] - miles[i]
            if leg > max_range_miles + EPS:
                break
            cost = best[i] + leg / mpg * price[i] + stop_penalty
            if cost < best[k] - EPS:
                best[k], prev[k] = cost, i
        leg = total_miles - miles[i]
        if leg <= max_range_miles + EPS:
            cost = best[i] + leg / mpg * price[i]
            if cost < finish_cost - EPS:
                finish_cost, finish_prev = cost, i

    if finish_prev is None:
        raise NoFeasiblePlan(_gap_message(miles, stations, max_range_miles, best))

    # Walk back from the finish and price each purchase.
    path = []
    i = finish_prev
    while i is not None and i != -1:
        path.append(i)
        i = prev[i]
    path.reverse()

    plan = FuelPlan()
    for pos, i in enumerate(path):
        next_mile = miles[path[pos + 1]] if pos + 1 < len(path) else total_miles
        leg = next_mile - miles[i]
        if pos == 0:
            if start_full:
                leg = max(0.0, leg - (max_range_miles - miles[i]))  # fuel still in the tank
            else:
                leg += miles[i]  # also covers start -> first station
        gallons = leg / mpg
        plan.stops.append(FuelStop(stations[i], gallons, gallons * price[i], leg))
    plan.total_gallons = sum(s.gallons for s in plan.stops)
    plan.total_cost = sum(s.cost for s in plan.stops)
    return plan


def _cheapest_per_location(stations):
    """Many stations share a town's coordinates; only the cheapest there can matter."""
    best = {}
    for rs in stations:
        key = (rs.station.lat, rs.station.lng)
        if key not in best or rs.station.price < best[key].station.price:
            best[key] = rs
    return sorted(best.values(), key=lambda rs: rs.route_mile)


def _gap_message(miles, stations, max_range_miles, best):
    reachable = [i for i, c in enumerate(best) if c < INF]
    last = max(reachable, key=lambda i: miles[i])
    s = stations[last].station
    return (
        f"No station within {max_range_miles:.0f} miles after mile {miles[last]:.0f} "
        f"({s.city}, {s.state}); the route cannot be completed with this range."
    )
