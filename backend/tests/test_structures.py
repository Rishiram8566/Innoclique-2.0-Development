"""Cell tower, water dam and power plant models built from their drawings' printed dimensions."""

import json
import shutil
from pathlib import Path

import pytest

from app.services import building_model
from app.services import dam_model
from app.services import power_plant_model as pp
from app.services import structure_common as sc
from app.services import tower_model

DRAWINGS = Path(__file__).resolve().parents[2] / "test_drawings"


def lines_of(name):
    path = DRAWINGS / name
    if not path.is_file():
        pytest.skip(f"{name} not generated")
    return [{"text": t, "upper": t.upper(), "bbox": b, "page": pg, "page_size": sz, "drawing_id": "d", "drawing_title": name}
            for t, b, pg, sz in sc._pdf_lines(str(path), path.stat().st_mtime_ns)]


def by_component(model):
    return {e["component"]: e for e in model["elements"]}


def test_tower_reads_its_notes_and_builds_by_lod():
    facts = sc.Facts(lines_of("CELL_TOWER_40M_ELEVATION.pdf"))
    model = tower_model.build("p", facts, ["t.pdf"], {"id": "d", "title": "t.pdf"})
    values = {p["name"]: p["value"] for p in model["parameters"]}
    assert values["height"] == 40.0 and values["legs"] == 4 and values["base width"] == 6.0 and values["top width"] == 1.5
    parts = by_component(model)
    assert parts["tower envelope"]["lod"] == {"category": "tower envelope", "minimum_lod": 100, "maximum_lod": 100}
    assert parts["main legs"]["lod"]["minimum_lod"] == 200 and parts["X-bracing"]["lod"]["minimum_lod"] == 300
    assert parts["microwave dishes"]["lod"]["minimum_lod"] == 400
    z = [v[2] for v in parts["main legs"]["mesh"]["vertices"]]
    assert max(z) == pytest.approx(40000, abs=200)                      # legs reach the printed height
    colours = {part["color"] for part in parts["main legs"]["mesh_parts"]}
    assert colours == {tower_model.RED, tower_model.WHITE}              # aviation bands, as the notes say
    pads = [v for v in parts["pad foundation"]["mesh"]["vertices"]]
    assert min(v[2] for v in pads) == -3000                             # foundation depth 3.0 m below NGL


def test_tower_without_its_key_values_is_not_built():
    lines = [{"text": "TOWER HEIGHT: 30 M", "upper": "TOWER HEIGHT: 30 M", "bbox": (0, 0, 1, 1), "page": 0,
              "page_size": (1, 1), "drawing_id": "d", "drawing_title": "x"}]
    model = tower_model.build("p", sc.Facts(lines), ["x"], {"id": "d", "title": "x"})
    assert model["elements"] == [] and "do not give" in model["limitations"][0]


def test_dam_section_and_spillway_follow_the_drawing():
    facts = sc.Facts(lines_of("WATER_DAM_GA.pdf"))
    model = dam_model.build("p", facts, ["d.pdf"], {"id": "d", "title": "d.pdf"})
    values = {p["name"]: p["value"] for p in model["parameters"]}
    assert values["top of dam (crest) level"] == 320.0 and values["downstream slope (H per 1 V)"] == 0.75
    comps = [e["component"] for e in model["elements"]]
    assert comps.count("ogee spillway bay") == 5 and comps.count("radial gate") == 5 and comps.count("spillway pier") == 4
    # The deepest monolith: crest 320, foundation ~262, toe at 0.75 x height downstream.
    blocks = [e for e in model["elements"] if e["component"] == "non-overflow block"]
    deepest = max(blocks, key=lambda e: e["dimensions_mm"]["height"])
    ys = [v[1] for v in deepest["mesh"]["vertices"]]
    assert max(ys) == pytest.approx(0.75 * deepest["dimensions_mm"]["height"], rel=0.01)
    assert max(v[2] for v in deepest["mesh"]["vertices"]) == 320000
    reservoir = next(e for e in model["elements"] if e["component"] == "reservoir at FRL")
    assert max(v[2] for v in reservoir["mesh"]["vertices"]) == 317000 and reservoir["lod"]["minimum_lod"] == 500


def test_dam_foundation_profile_and_sections():
    p = {"profile": [(0, 320), (100, 266), (150, 262)], "foundation": 262}
    assert dam_model.foundation_at(p, 50) == pytest.approx(293.0)
    assert dam_model.foundation_at({"profile": [], "foundation": 262}, 50) == 262
    section = dam_model.nof_section(320000, 262000, 8000, 0.75, 0.0)
    assert section[-1] == (0.75 * 58000, 262000) and section[2] == (8000, 320000)
    ogee, tangent = dam_model.ogee_section(309000, 262000, 9500, 0.75)
    assert ogee[3] == (0.28 * 9500, 309000) and tangent[1] < 309000 and ogee[-1][1] == 262000


def test_power_plant_places_scheduled_structures_from_the_layout():
    lines = lines_of("THERMAL_POWER_PLANT_LAYOUT.pdf")
    pp._OUTLINES[("d", 0)] = pp.outlines_of(str(DRAWINGS / "THERMAL_POWER_PLANT_LAYOUT.pdf"), 0)
    model = pp.build("p", sc.Facts(lines), ["p.pdf"], {"id": "d", "title": "p.pdf"}, lines)
    assert "17 structure(s) placed" in model["limitations"][0]
    assert all(e["provenance"].get("footprint") in (None, "drawn outline on the plan") for e in model["elements"])
    shells = [e for e in model["elements"] if e["element_type"] == "cooling tower" and e["component"].startswith("Cooling Tower")
              and e["lod"]["minimum_lod"] == 200]
    assert len(shells) == 2
    for shell in shells:
        assert max(v[2] for v in shell["mesh"]["vertices"]) == pytest.approx(100000 + 110000, abs=10)
    chimney = next(e for e in model["elements"] if e["component"] == "Chimney" and e["lod"]["minimum_lod"] == 200)
    assert max(v[2] for v in chimney["mesh"]["vertices"]) == pytest.approx(250000, abs=10)


def test_plant_schedule_rows_and_kinds():
    rows = pp.schedule([{"text": t, "upper": t, "drawing_title": "x"} for t in (
        "COOLING TOWER - 2 NOS, DIA 90 M, HT 110 M", "TG BUILDING - 90 X 40 M, HT 35 M", "NOTES - SEE BELOW")])
    assert [(r["kind"], r["count"], r["dia"], r["size"], r["height"]) for r in rows] == [
        ("cooling", 2, 90.0, None, 110.0), ("turbine", 1, None, (90.0, 40.0), 35.0)]


def test_structure_projects_skip_the_floor_plan_pipeline(tmp_path):
    source = DRAWINGS / "CELL_TOWER_40M_ELEVATION.pdf"
    if not source.is_file():
        pytest.skip("tower drawing not generated")
    (tmp_path / "drawings").mkdir()
    shutil.copy(source, tmp_path / "drawings" / "t.pdf")
    (tmp_path / "project.json").write_text(json.dumps({
        "name": "Tower", "project_type": "telecom_tower",
        "drawings": [{"id": "t", "filename": "CELL_TOWER_40M_ELEVATION.pdf", "stored_filename": "t.pdf"}]}), "utf-8")
    model = building_model.build_building_model(tmp_path, "p")
    assert model["artifact_type"] == "structure_model" and model["source"]["structure"] == "cell tower"
    assert model["lod_notes"]["100"].startswith("LOD 100") and len(model["elements"]) > 10
