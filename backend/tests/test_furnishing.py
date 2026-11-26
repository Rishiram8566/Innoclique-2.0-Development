"""LOD 500 furniture laid out by room type inside the walls round each room label."""

from app.services import building_model
from app.services import furnishing as fu

STOREY = {"placement_id": "g", "drawing_id": "plan", "drawing_title": "plan", "base_level_key": "SSL@+300mm",
          "base_elevation_mm": 300.0, "storey_height_mm": 3000.0}
T = 230.0


def element(element_id, rect, element_type="wall", opening=None):
    return {"element_id": element_id, "element_type": element_type, "opening": opening,
            "storey": {"placement_id": "g", "base_level_key": "SSL@+300mm", "base_elevation_mm": 300.0},
            "mesh": {"vertices": building_model._box_vertices(rect, 300.0, 3300.0), "faces": []}}


def room_walls(x0, y0, x1, y1, *, door_south=None, window_north=None, window_east=None):
    """Walls round a room (inner faces x0..x1, y0..y1), with a door in the south wall and windows."""
    out = [element("w", (x0 - T, y0 - T, x0, y1 + T)), element("e", (x1, y0 - T, x1 + T, y1 + T))]
    for name, face, gap, kind in (("n", (y0 - T, y0), window_north, "window"), ("s", (y1, y1 + T), door_south, "door")):
        if gap:
            out += [element(f"{name}1", (x0 - T, face[0], gap[0], face[1])), element(f"{name}2", (gap[1], face[0], x1 + T, face[1])),
                    element(f"{name}-{kind}", (gap[0], face[0], gap[1], face[1]), kind,
                            {"head_mm": 2100.0, "sill_mm": 900.0 if kind == "window" else 0.0})]
        else:
            out.append(element(name, (x0 - T, face[0], x1 + T, face[1])))
    if window_east:
        out = [e for e in out if e["element_id"] != "e"]
        a, b = window_east
        out += [element("e1", (x1, y0 - T, x1 + T, a)), element("e2", (x1, b, x1 + T, y1 + T)),
                element("e-window", (x1, a, x1 + T, b), "window", {"head_mm": 2100.0, "sill_mm": 900.0})]
    return out


def furnish(name, walls, centre):
    rooms = [{"name": name, "position_mm": [centre[0], centre[1], 300.0], "storey": {"placement_id": "g"}}]
    return fu.furnish_building([STOREY], walls, rooms)


def rect(e):
    return fu._rect_of(e)


def inside(r, room):
    return room[0] - 1 <= r[0] and room[1] - 1 <= r[1] and r[2] <= room[2] + 1 and r[3] <= room[3] + 1


def test_abbreviations_and_regional_names_are_understood():
    cases = {
        "HALL": ["living"], "HALL CUM DINING": ["living", "dining"], "LVG": ["living"], "D/R": ["living"],
        "MAJLIS": ["living"], "FAMILY LOUNGE": ["living"],
        "KIT": ["kitchen"], "KITCHENETTE": ["kitchen"],
        "M.BED": ["bedroom"], "MBR": ["bedroom"], "BR 2": ["bedroom"], "KIDS ROOM": ["bedroom"],
        "T&B": ["toilet"], "T & B": ["toilet"], "W.C.": ["toilet"], "MASTER BEDROOM TOILET": ["toilet"],
        "EN-SUITE": ["toilet"], "POWDER ROOM": ["toilet"],
        "DNG": ["dining"], "WIC": ["dress"], "UTL": ["utility"], "LIBRARY": ["office"],
    }
    for label, kinds in cases.items():
        assert fu.room_types(label) == kinds, label
    assert fu.room_types("BR-1") == ["bedroom"] and fu.room_types("M.BR") == ["bedroom"]
    for not_living in ("CHILLER HALL", "LIFT HALL", "ENTRANCE HALL", "STAIR HALL", "ELECTRICAL ROOM", "BR"):
        kinds = fu.room_types(not_living)
        assert "living" not in kinds and "bedroom" not in kinds, not_living


def test_label_reader_accepts_the_abbreviations():
    from app.services.drawing_text_facts import room_label

    for label in ("KIT", "T&B", "M.BED", "LVG", "D/R", "WIC", "HALL", "BR 2", "MAJLIS"):
        assert room_label(label) == label
    for not_a_room in ("SCALE 1:100", "DRAWING NO. A-101", "230 THK. BRICK MASONRY WALL"):
        assert room_label(not_a_room) is None


def test_room_types_come_from_the_label():
    assert fu.room_types("MASTER BEDROOM") == ["bedroom"]
    assert fu.room_types("LIVING / DINING") == ["living", "dining"]
    assert fu.room_types("TOILET") == ["toilet"] and fu.room_types("KITCHEN") == ["kitchen"]
    assert fu.room_types("STAIRCASE") == [] and fu.room_types("ELECTRICAL ROOM") == ["electrical"]


def test_bedroom_gets_bed_tables_wardrobe_painting_and_curtains_clear_of_the_door():
    room = (0, 0, 4000, 4300)
    walls = room_walls(*room, door_south=(2800, 3700), window_north=(1200, 2700))
    furniture, notes = furnish("BEDROOM 1", walls, (2000, 2000))
    names = sorted(e["component"] for e in furniture)
    assert names.count("bed") == 1 and names.count("bedside table") == 2 and "wardrobe" in names
    assert "painting" in names and "curtains" in names
    door_zone = (2750, 4300 - 900, 3750, 4300)
    for e in furniture:
        r = rect(e)
        assert inside(r, room), e["component"]
        if e["component"] not in ("painting", "curtains"):
            assert not (r[0] < door_zone[2] and door_zone[0] < r[2] and r[1] < door_zone[3] and door_zone[1] < r[3])
        assert e["lod"]["minimum_lod"] == 500 and "indicative" in e["provenance"]["basis"]
        assert e["mesh_parts"] and min(v[2] for v in e["mesh"]["vertices"]) >= 300.0     # on the storey floor
    bed = next(rect(e) for e in furniture if e["component"] == "bed")
    assert bed[2] - bed[0] == 1800 or bed[3] - bed[1] == 1800                            # a double in a 4 m room


def test_living_room_faces_a_tv_across_a_coffee_table():
    room = (0, 0, 5000, 4300)
    furniture, _ = furnish("LIVING ROOM", room_walls(*room, door_south=(600, 1600)), (2500, 2000))
    names = [e["component"] for e in furniture]
    for wanted in ("sofa", "coffee table", "rug", "tv unit"):
        assert wanted in names
    sofa = next(rect(e) for e in furniture if e["component"] == "sofa")
    tv = next(rect(e) for e in furniture if e["component"] == "tv unit")
    table = next(rect(e) for e in furniture if e["component"] == "coffee table")
    centre = lambda r: ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)     # noqa: E731
    # The table sits between the sofa and the TV.
    (sx, sy), (tx, ty), (cx, cy) = centre(sofa), centre(tv), centre(table)
    assert min(sx, tx) - 1 <= cx <= max(sx, tx) + 1 and min(sy, ty) - 1 <= cy <= max(sy, ty) + 1


def test_toilet_and_kitchen_fittings():
    room = (0, 0, 2400, 2100)
    furniture, _ = furnish("TOILET", room_walls(*room, door_south=(1200, 1950)), (1200, 1000))
    assert sorted(e["component"] for e in furniture) == ["washbasin", "wc"]
    kitchen = (0, 0, 4000, 4300)
    furniture, _ = furnish("KITCHEN", room_walls(*kitchen, door_south=(500, 1400), window_north=(1600, 3100)), (2000, 2000))
    names = [e["component"] for e in furniture]
    for wanted in ("kitchen counter", "kitchen sink", "hob and hood", "refrigerator"):
        assert wanted in names
    sink = next(rect(e) for e in furniture if e["component"] == "kitchen sink")
    assert 1600 <= (sink[0] + sink[2]) / 2 <= 3100                       # under the window


def test_living_dining_label_is_split_between_the_two_uses():
    room = (0, 0, 7600, 4300)
    furniture, notes = furnish("LIVING / DINING", room_walls(*room, door_south=(5000, 6200)), (2000, 2000))
    kinds = {e["furniture"]["room_type"] for e in furniture}
    assert kinds == {"living", "dining"} and "dining table" in [e["component"] for e in furniture]


def test_rooms_without_walls_round_the_label_are_skipped():
    furniture, notes = furnish("BEDROOM", [], (0, 0))
    assert furniture == [] and "not furnished" in notes[0]


def test_technical_rooms_get_their_equipment():
    assert fu.room_types("CHILLER HALL") == ["chiller"] and fu.room_types("FIRE PUMP ROOM") == ["pump"]
    assert fu.room_types("RVSS & VFD ROOM-01") == ["electrical"] and fu.room_types("DC CHARGER / UPS ROOM") == ["ups"]
    assert fu.room_types("TELECOM ROOM") == ["it"] and fu.room_types("UF BACK WASH / RO FEED") == ["water"]
    assert fu.room_types("GENERATOR ROOM BELOW") == []                  # a pointer to another level, not a room
    cases = {"CHILLER HALL": ((0, 0, 20000, 12000), "chiller"), "LV ROOM": ((0, 0, 10000, 6000), "switchgear panel"),
             "IT ROOM": ((0, 0, 6000, 5000), "server rack"), "FIRE PUMP ROOM": ((0, 0, 8000, 6000), "pump set"),
             "GENERATOR ROOM": ((0, 0, 14000, 8000), "generator set")}
    for label, (room, piece) in cases.items():
        furniture, _ = furnish(label, room_walls(*room, door_south=(500, 1700)), ((room[0] + room[2]) / 2, (room[1] + room[3]) / 2))
        names = [e["component"] for e in furniture]
        assert piece in names, (label, names)
        for e in furniture:
            r = rect(e)
            assert inside(r, room) and "indicative" in e["provenance"]["basis"]
    pumps, _ = furnish("FIRE PUMP ROOM", room_walls(0, 0, 8000, 6000, door_south=(500, 1700)), (4000, 3000))
    assert len(pumps[0]["mesh"]["vertices"]) > 16                       # pump casings are cylinders, not just boxes


def test_double_height_plant_room_is_equipped_once_but_stacked_toilets_are_not_merged():
    upper = {**STOREY, "placement_id": "u", "base_level_key": "SSL@+4300mm", "base_elevation_mm": 4300.0}

    def on(storey, walls):
        return [{**w, "storey": {"placement_id": storey["placement_id"], "base_level_key": storey["base_level_key"],
                                 "base_elevation_mm": storey["base_elevation_mm"]}} for w in walls]

    walls = room_walls(0, 0, 9000, 7000, door_south=(500, 1700))
    rooms = [{"name": name, "position_mm": [4500 + dx, 3500, 0.0], "storey": {"placement_id": pid}}
             for name in ("FIRE PUMP ROOM",) for pid, dx in (("g", 0), ("u", 400))]
    furniture, notes = fu.furnish_building([STOREY, upper], on(STOREY, walls) + on(upper, walls), rooms)
    assert {e["storey"]["placement_id"] for e in furniture} == {"g"}
    assert any("double-height" in n for n in notes)

    toilets = [{"name": "TOILET", "position_mm": [4500, 3500, 0.0], "storey": {"placement_id": pid}} for pid in ("g", "u")]
    furniture, _ = fu.furnish_building([STOREY, upper], on(STOREY, walls) + on(upper, walls), toilets)
    assert {e["storey"]["placement_id"] for e in furniture} == {"g", "u"}


def test_equipment_drawn_in_cad_replaces_the_indicative_layout_and_double_height_floors_open():
    walls = room_walls(0, 0, 9000, 7000, door_south=(500, 1700))
    drawn = element("pump", (3000, 3000, 4000, 4500), "furniture")
    drawn.update({"detected_by": "cad_block", "component": "pump set", "furniture": {"room": None}, "provenance": {}})
    furniture, notes = fu.furnish_building([STOREY], walls + [drawn], [
        {"name": "FIRE PUMP ROOM", "position_mm": [4500, 1500, 0.0], "storey": {"placement_id": "g"}}])
    assert furniture == [] and drawn["furniture"]["room"] == "FIRE PUMP ROOM"
    assert any("equipment from the CAD file" in n for n in notes)

    upper = {**STOREY, "placement_id": "u", "base_level_key": "SSL@+4300mm", "base_elevation_mm": 4300.0}
    lifted = [{**w, "storey": {"placement_id": "u", "base_level_key": "SSL@+4300mm", "base_elevation_mm": 4300.0}} for w in walls]
    rooms = [{"name": "CHILLER HALL", "position_mm": [4500, 3500, 0.0], "storey": {"placement_id": pid}} for pid in ("g", "u")]
    fu.furnish_building([STOREY, upper], walls + lifted, rooms)
    slab = {"element_id": "slab:u:base", "footprint": {"rects_mm": [[-500, -500, 9500, 7500]]}}
    notes = fu.open_double_height_rooms(rooms, [slab])
    assert notes and slab["openings"][0]["rect_mm"] == rooms[1]["bounds_mm"]
    assert all(not (r[0] < 4500 < r[2] and r[1] < 3500 < r[3]) for r in slab["footprint"]["rects_mm"])


def test_office_and_workplace_labels_are_read():
    from app.services.drawing_text_facts import room_label

    for label in ("OPEN PLAN OFFICE", "OPEN-PLAN OFFICE", "WORKSTATIONS", "STAFF CAFETERIA", "BREAK-OUT AREA",
                  "PRINT ROOM", "BOARD ROOM", "CO-WORKING"):
        assert room_label(label) == label, label
    assert room_label("GROUND FLOOR PLAN") is None and room_label("OFFICE LAYOUT PLAN") is None
    assert fu.room_types("OPEN OFFICE") == ["office"] and fu.room_types("WORKSTATIONS") == ["office"]
    assert fu.room_types("STAFF CAFETERIA") == ["cafeteria"] and fu.room_types("BREAK-OUT AREA") == ["cafeteria"]
    assert fu.room_types("COPY / PRINT") == ["print"] and fu.room_types("BOARD ROOM") == ["meeting"]
    assert fu.room_types("SERVER ROOM") == ["it"]


def overlaps(a, b):
    return a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1


def test_open_plan_office_is_filled_with_workstation_rows_round_its_columns():
    room = (0.0, 0.0, 20000.0, 12000.0)                       # wider than a home's room (15 m)
    columns = [element("c1", (9700, 5700, 10300, 6300), "column"), element("c2", (4300, 3000, 4900, 3600), "column")]
    walls = room_walls(*room, door_south=(9000, 10000)) + columns
    # The label stands in line with a column: the room is still the whole space between the walls.
    items, _ = furnish("OPEN PLAN OFFICE", walls, (10000, 3000))
    desks = [e for e in items if e["component"] == "workstation desk"]
    chairs = [e for e in items if e["component"] == "office chair"]
    assert 24 <= len(desks) <= 32 and len(chairs) == len(desks)
    door_zone = (8950, 11000, 10050, 12000)
    for e in items:
        r = rect(e)
        assert inside(r, room)
        assert not any(overlaps(r, rect(c)) for c in columns) and not overlaps(r, door_zone)
    assert len({round(rect(d)[1]) for d in desks}) >= 4          # two rows, each of back-to-back desks
    assert all(e["provenance"]["room_type"] == "office" for e in items)


def test_small_office_gets_a_desk_and_a_cabinet():
    room = (0.0, 0.0, 4000.0, 4500.0)
    items, _ = furnish("OFFICE", room_walls(*room, door_south=(500, 1400)), (2000, 2000))
    names = sorted(e["component"] for e in items)
    assert names == ["desk", "office chair", "storage cabinet"]


def test_board_room_table_grows_with_the_room_and_faces_a_display():
    room = (0.0, 0.0, 9000.0, 5000.0)
    items, _ = furnish("BOARD ROOM", room_walls(*room, door_south=(500, 1400)), (4500, 2500))
    table = next(e for e in items if e["component"] == "meeting table")
    assert table["dimensions_mm"]["length"] == 7200 and table["dimensions_mm"]["thickness"] == 1500
    assert sum(e["component"] == "chair" for e in items) >= 16
    assert any(e["component"] == "display screen" for e in items)


def test_cafeteria_and_print_room():
    room = (0.0, 0.0, 14000.0, 9000.0)
    items, _ = furnish("STAFF CAFETERIA", room_walls(*room, door_south=(500, 1400)), (7000, 4500))
    tables = [e for e in items if e["component"] == "cafe table"]
    assert sum(e["component"] == "servery counter" for e in items) == 1
    assert len(tables) >= 6 and sum(e["component"] == "chair" for e in items) == 4 * len(tables)
    small = (0.0, 0.0, 3000.0, 3000.0)
    items, _ = furnish("PRINT ROOM", room_walls(*small, door_south=(200, 1100)), (1500, 1500))
    assert sorted(e["component"] for e in items) == ["paper cabinet", "printer / copier"]


def test_office_buildings_get_a_glass_facade_unless_the_drawing_names_masonry():
    offices = [{"name": n} for n in ("OPEN PLAN OFFICE", "MEETING ROOM", "CAFETERIA", "TOILET")]
    homes = [{"name": n} for n in ("BEDROOM 1", "KITCHEN", "LIVING", "STUDY")]
    assert fu.facade_style(offices, [])["style"] == "glass"
    assert fu.facade_style(homes, [])["style"] == "masonry"
    brick = [{"element_type": "wall", "is_external": True, "component": "wall", "wall_type": "230 THK. BRICK MASONRY WALL"}]
    stated = fu.facade_style(offices, brick)
    assert stated["style"] == "masonry" and "BRICK" in stated["basis"]


def drawn_item(element_id, r, component, source="cad_block"):
    item = element(element_id, r, "furniture")
    item.update(detected_by=source, component=component, furniture={"room": None, "drawn": True}, provenance={})
    return item


def test_an_empty_ifc_space_is_furnished_and_a_modelled_one_is_left_alone():
    def space(name):
        return [{"name": name, "position_mm": [2500.0, 2000.0, 300.0], "size_mm": [5000, 4000],
                 "source": "IfcSpace in the IFC model", "storey": {"placement_id": "g"}}]
    # No walls are needed: the IFC space gives the room's extent.
    items, _ = fu.furnish_building([STOREY], [], space("BEDROOM 1"))
    assert any(e["component"] == "bed" for e in items)
    assert all(inside(rect(e), (0.0, 0.0, 5000.0, 4000.0)) for e in items)
    modelled = drawn_item("ifc-bed", (1000, 1000, 2600, 3000), "double bed", source="ifc")
    items, notes = fu.furnish_building([STOREY], [modelled], space("BEDROOM 1"))
    assert items == [] and any("from the IFC model" in n for n in notes)


def test_furniture_drawn_in_cad_replaces_the_default_layout_but_does_not_equip_a_plant_room():
    room = (0.0, 0.0, 6000.0, 5000.0)
    desks = [drawn_item("d1", (1000, 1000, 2400, 1700), "desk"), drawn_item("c1", (1400, 1800, 1900, 2300), "chair")]
    items, notes = furnish("OPEN PLAN OFFICE", room_walls(*room, door_south=(500, 1400)) + desks, (3000, 2500))
    assert items == [] and any("2 item(s) of furniture from the CAD file" in n for n in notes)
    pump_room = (0.0, 0.0, 8000.0, 6000.0)
    chair = [drawn_item("c2", (6000, 1000, 6500, 1500), "chair")]
    items, _ = furnish("FIRE PUMP ROOM", room_walls(*pump_room, door_south=(500, 1700)) + chair, (4000, 3000))
    assert any(e["component"] == "pump set" for e in items)
