"""Stairs read from riser lines near a STAIR label, and lifts from LIFT labels (page-local mm)."""

from app.services import stair_detection as sd

# Stair room of the G+1 test house: inner faces x 4315..7685, y 115..4385.
WALLS = [(4085, 0, 4315, 4500), (7685, 0, 7915, 4500), (4085, -115, 7915, 115), (4085, 4385, 7915, 4615)]
ROOM = (4315, 115, 7685, 4385)
LABEL = (5000, 1230)


def dog_leg_lines(start=1615, count=10, going=250):
    lines = []
    for k in range(count):                    # two 1500 mm flights
        y = start + k * going
        lines += [("h", y, 4400, 5900), ("h", y, 6100, 7600)]
    lines.append(("v", 6000, start, start + (count - 1) * going))   # centre line (not a riser)
    return lines


def planned(lines=None, arrows=(("UP", 5100, 4250),), walls=WALLS, label=LABEL):
    flights = sd.pick_flights(sd.find_flights(sd.candidate_lines(lines or dog_leg_lines(), walls, label)), label)
    return sd.plan_stair(flights, list(arrows), walls)


def test_room_is_bounded_by_the_nearest_walls():
    assert sd.room_bounds(5000, 1230, WALLS) == ROOM
    assert sd.room_bounds(20000, 1230, WALLS) is None


def test_flights_are_even_runs_of_equal_lines_near_the_label():
    flights = sd.find_flights(sd.candidate_lines(dog_leg_lines() + [("h", 0, 4400, 5900)], WALLS, LABEL))
    assert len(flights) == 2                                # the wall-face line inside a wall is ignored
    for f in flights:
        assert f["orient"] == "h" and len(f["positions"]) == 10 and f["going_mm"] == 250
        assert f["a1"] - f["a0"] == 1500
    assert sd.find_flights(sd.candidate_lines(dog_leg_lines(), WALLS, (40000, 40000))) == []   # too far away


def test_dog_leg_climbs_from_the_up_text_to_the_landing_and_back():
    plan = planned()
    assert plan["kind"] == "dog-leg"
    first, second = plan["flights"]
    assert first["a0"] == 4400 and first["climb"][0] == 3865 and first["climb"][-1] == 1615   # up towards the landing
    assert second["a0"] == 6100 and second["climb"][0] == 1615 and second["climb"][-1] == 3865
    assert plan["landing"]["along"] == (115, 1615) and plan["landing"]["across"] == (4400, 7600)
    assert not plan["landing"]["boundary_assumed"] and "UP text" in plan["direction_basis"]
    assert first["open_sides"] == [5900] and second["open_sides"] == [6100]                   # the well side


def test_rise_is_the_storey_height_over_the_drawn_risers():
    built = sd.build_stair(planned(), 3000.0, 150.0, WALLS)
    assert built["risers"] == 20 and built["rise_mm"] == 150.0 and built["count_basis"] == "20 risers drawn"
    flights = [p for p in built["parts"] if p["kind"] == "flight"]
    landing = next(p for p in built["parts"] if p["kind"] == "landing")
    assert [(f["from_z"], f["to_z"]) for f in flights] == [(0.0, 1500.0), (1500.0, 3000.0)]
    assert landing["z"] == 1500.0                                                   # half landing
    assert max(v[2] for v in flights[1]["mesh"]["vertices"]) == 2850.0             # last tread; then the floor slab
    assert len([p for p in built["parts"] if p["kind"] == "railing"]) == 2
    assert built["void_mm"] == (4400, 115, 7600, 3865)


def test_partial_flights_are_lengthened_at_a_standard_rise():
    # A taller room, a 5.6 m storey and only 10 + 10 risers drawn (280 mm each): the plan cut the flights short.
    walls = [(4085, -115, 4315, 8230), (7685, -115, 7915, 8230), (4085, -115, 7915, 115), (4085, 8000, 7915, 8230)]
    plan = planned(dog_leg_lines(start=3000, going=278), arrows=[("UP", 5100, 6000)], walls=walls, label=(5000, 2000))
    built = sd.build_stair(plan, 5600.0, 150.0, walls)
    assert built["risers"] == 32 and built["rise_mm"] == 175.0
    assert built["count_basis"].startswith("assumed")
    first, second = (p for p in built["parts"] if p["kind"] == "flight")
    assert first["risers"] == second["risers"] == 16
    assert first["climb"][0] == second["climb"][-1] == 3000 + 9 * 278          # both run from the far end
    landing = next(p for p in built["parts"] if p["kind"] == "landing")
    assert landing["along"][0] == 115 and landing["along"][1] - 115 >= 750      # landing still half a flight deep


def test_one_drawn_flight_gets_a_mirrored_second_flight_against_the_far_wall():
    lines = [("h", 1615 + k * 250, 4400, 5900) for k in range(6)]                # only one flight, partly drawn
    built = sd.build_stair(planned(lines, arrows=[("UP", 5100, 4250)]), 3000.0, 150.0, WALLS)
    flights = [p for p in built["parts"] if p["kind"] == "flight"]
    assert len(flights) == 2 and built["risers"] == 18
    assert flights[1]["a"] == (6185, 7685)                                       # against the opposite wall


def test_implausible_rise_is_not_built():
    plan = planned(arrows=[])
    assert "assumed" in plan["direction_basis"]
    assert "rejected" in sd.build_stair(plan, 1800.0, 150.0, WALLS)              # 90 mm risers


def test_straight_flight_starts_at_the_up_end():
    lines = [("v", 5000 + k * 280, 200, 1200) for k in range(12)]
    plan = planned(lines, arrows=[("UP", 8300, 700)], walls=[], label=(6500, 700))
    assert plan["kind"] == "straight" and plan["flights"][0]["climb"][0] == 5000 + 11 * 280


def test_slab_opening_cuts_the_floor_rectangles():
    pieces = sd.subtract_rect([[0, 0, 12000, 9000]], [4400, 115, 7600, 3865])
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in pieces)
    assert area == 12000 * 9000 - 3200 * 3750
    assert all(not (r[0] < 7600 and r[2] > 4400 and r[1] < 3865 and r[3] > 115) for r in pieces)


# ------------------------------------------------------------ lifts

SHAFT_WALLS = [(0, 0, 200, 3200), (2400, 0, 2600, 3200), (0, 0, 2600, 200), (0, 3000, 1000, 3200),
               (1900, 3000, 2600, 3200)]


def test_lift_shaft_from_its_walls_or_the_drawn_car():
    assert sd.lift_outline(600, 1500, SHAFT_WALLS, []) == ((200, 200, 2400, 3000), "walls round the lift label")
    car = [("v", 300, 300, 2800), ("v", 2300, 300, 2800), ("h", 300, 300, 2300), ("h", 2800, 300, 2300)]
    outline, basis = sd.lift_outline(1300, 1500, [], car)
    assert outline == (300, 300, 2300, 2800) and "car outline" in basis
    assert sd.lift_outline(1300, 1500, [], [])[0] is None


def _storey(pid, base, height=3000.0):
    return {"placement_id": pid, "drawing_id": pid, "drawing_title": pid, "base_level_key": f"L{base:.0f}",
            "base_elevation_mm": base, "storey_height_mm": height}


def test_lift_stops_on_each_storey_make_one_shaft_with_slab_openings():
    door = {"element_id": "g:door", "element_type": "opening",
            "mesh": {"vertices": [[1000, 3000, 0], [1900, 3000, 0], [1900, 3200, 0], [1000, 3200, 0],
                                  [1000, 3000, 2100], [1900, 3000, 2100], [1900, 3200, 2100], [1000, 3200, 2100]]}}
    storeys = [_storey("g", 0.0), _storey("f", 3000.0)]
    stops = [{"storey": s, "rect": (200, 200, 2400, 3000), "base_mm": s["base_elevation_mm"], "name": "LIFT 3000 KG",
              "basis": "walls round the lift label", "capacity_kg": 3000.0, "rects": SHAFT_WALLS,
              "served": [door] if s["placement_id"] == "g" else [], "offset": (0.0, 0.0)} for s in storeys]
    elements, voids, notes = sd.build_lifts(stops, storeys)
    shaft = next(e for e in elements if e["element_type"] == "lift shaft")
    car = next(e for e in elements if e["element_type"] == "lift car")
    assert {v[2] for v in shaft["mesh"]["vertices"]} == {0.0, 6000.0}           # lowest stop to the top storey's top
    assert shaft["lift"]["capacity_kg"] == 3000.0 and shaft["lift"]["stops"] == ["L0", "L3000"]
    assert shaft["lift"]["door_ids"] == ["g:door"] and shaft["lod"]["minimum_lod"] == 100
    assert car["provenance"]["display_only_sizes"]
    assert voids == [{"placement_id": "f", "rect_mm": [200, 200, 2400, 3000], "basis": "lift shaft (LIFT 3000 KG)"}]
    assert "2 stop(s)" in notes[0] and "1 landing door" in notes[0]
