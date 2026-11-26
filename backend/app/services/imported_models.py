"""
CAD / BIM models uploaded to a project (IFC, DXF) and the building model built from them.

Source priority for the 3D model: IFC (a complete BIM model) > DXF floor plans > PDF
drawings. When a project has IFC or DXF models, its PDF plans are not used for the 3D
model (they would duplicate the same building in another frame); the model says so.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.services import height_verification_ledger as ledger_service

IMPORTED_DIR = "imported"
IMPORTER_VERSION = 13            # bump when the readers change, so stored imports are refreshed
MODEL_TYPES = {".ifc": "ifc", ".dxf": "dxf"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cad_sheet_role(title: str | None) -> tuple[str, str]:
    """
    What a CAD file is, from its sheet title: "floor_plan" (a storey), "roof_plan" (sets the roof level) or
    "reference" (sections, elevations, details, schedules, finishes, site, signage...: kept, not modelled).
    A file whose name says nothing either way is read as a floor plan.
    """
    from app.services.auto_pipeline import NOT_A_FLOOR_PLAN
    from app.services.cad_import import is_roof_plan

    text = re.sub(r"[_\-.]+", " ", title or "")
    other = NOT_A_FLOOR_PLAN.search(text)
    if other:
        return "reference", f"a {other.group(0).lower()} sheet, not a floor plan"
    if is_roof_plan(text):
        return "roof_plan", "roof plan"
    return "floor_plan", "floor plan"


def _bbox(element: dict[str, Any]) -> tuple[float, float, float, float]:
    xs = [v[0] for v in element["mesh"]["vertices"]]
    ys = [v[1] for v in element["mesh"]["vertices"]]
    return min(xs), min(ys), max(xs), max(ys)


def model_records(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in project.get("models") or [] if m.get("model_type") in MODEL_TYPES.values()]


def import_project_models(folder: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    """Import every uploaded model whose file changed since its last import; returns one line per model."""
    from app.services.cad_import import is_roof_plan, quick_slab_level

    results: list = []
    out_dir = Path(folder) / "artifacts" / IMPORTED_DIR
    records = model_records(project)
    roles = {r["id"]: cad_sheet_role(r["filename"]) for r in records if r["model_type"] == "dxf"}
    plans = [r for r in records if roles.get(r["id"], ("model",))[0] != "reference"]
    tops = _storey_tops(folder, plans, quick_slab_level, is_roof_plan)
    jobs: list = []
    for record in records:
        source = Path(folder) / "models" / record["stored_filename"]
        target = out_dir / f"{record['id']}.json"
        role, why = roles.get(record["id"], ("model", ""))
        if role == "reference":
            target.unlink(missing_ok=True)            # an import from before the sheets were sorted
            results.append({"model_id": record["id"], "title": record["filename"], "status": "reference",
                            "notes": [f"{record['filename']}: {why}; kept as a reference sheet, not modelled."]})
            continue
        sha = _sha256(source)
        top = tops.get(record["id"])
        if target.is_file():
            previous = json.loads(target.read_text("utf-8"))
            if (previous.get("source_sha256") == sha and previous.get("importer_version") == IMPORTER_VERSION
                    and previous.get("top_mm_hint") == top):
                results.append({"model_id": record["id"], "title": record["filename"], "status": "kept",
                                "notes": previous.get("notes", [])})
                continue
        results.append(None)                      # filled in below, in this order
        jobs.append((len(results) - 1, record, sha, target,
                     (record["model_type"], str(source), record["id"], record["filename"], top,
                      is_roof_plan(record["filename"]))))

    # Each file is read on its own, so several are imported at once (in worker processes).
    outcomes = _import_all([job[4] for job in jobs])
    for (index, record, sha, target, _), outcome in zip(jobs, outcomes):
        if not outcome.get("ok"):         # a damaged or unsupported file must not stop the other models
            results[index] = {"model_id": record["id"], "title": record["filename"], "status": "failed",
                              "notes": [outcome.get("reason") or "import failed"]}
            continue
        imported = outcome["model"]
        imported["source_sha256"] = sha
        imported["importer_version"] = IMPORTER_VERSION
        ledger_service.save_json_atomic(target, imported)
        results[index] = {"model_id": record["id"], "title": record["filename"], "status": "imported",
                          "notes": imported.get("notes", [])}
    return results


MAX_IMPORT_WORKERS = 6


def _import_one(job) -> dict:
    """Import one IFC or DXF file (picklable for a worker process)."""
    from app.services.cad_import import import_dxf
    from app.services.ifc_import import import_ifc

    model_type, source, model_id, title, top, roof = job
    try:
        if model_type == "ifc":
            return {"ok": True, "model": import_ifc(Path(source), model_id=model_id, title=title)}
        return {"ok": True, "model": import_dxf(Path(source), model_id=model_id, title=title, top_mm=top, roof_plan=roof)}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def _import_all(jobs: list) -> list[dict]:
    """Import files in parallel worker processes (in order); one by one if a pool cannot be started."""
    import os

    workers = min(len(jobs), MAX_IMPORT_WORKERS, max(1, (os.cpu_count() or 2) - 2))
    if workers > 1:
        try:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=workers) as pool:
                return list(pool.map(_import_one, jobs))
        except Exception:                 # no process pool here: fall back to one at a time
            pass
    return [_import_one(job) for job in jobs]


def _storey_tops(folder: Path, records: list[dict[str, Any]], level_of, is_roof) -> dict[str, float]:
    """
    Several DXF floor plans of one building: each plan's storey reaches the next plan's slab level (a roof
    plan's level closes the top floor). Levels are read straight from each file's printed slab levels.
    """
    levels = {}
    for record in records:
        if record["model_type"] != "dxf":
            continue
        source = Path(folder) / "models" / record["stored_filename"]
        try:
            levels[record["id"]] = level_of(source, highest=is_roof(record["filename"]))
        except OSError:
            levels[record["id"]] = None
    tops = {}
    for record in records:
        base = levels.get(record["id"])
        if base is None or is_roof(record["filename"]):
            continue
        higher = [v for other, v in levels.items() if other != record["id"] and v is not None and v >= base + 2000.0]
        if higher:
            tops[record["id"]] = min(higher)
    return tops


def _roof_equipment(models: list[dict[str, Any]], storeys: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Equipment drawn on the roof plans, on the roof of the highest floor below it (each unit once)."""
    placed: list[dict[str, Any]] = []
    for model in models:
        for element in model.get("roof_elements") or []:
            level = min(v[2] for v in element["mesh"]["vertices"])
            below = [s for s in storeys if s["base_elevation_mm"] < level]
            if not below:
                continue
            top = max(below, key=lambda s: s["base_elevation_mm"])
            xs = [v[0] for v in element["mesh"]["vertices"]]; ys = [v[1] for v in element["mesh"]["vertices"]]
            centre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
            if any(p["component"] == element["component"] and abs(p["_centre"][0] - centre[0]) < 500
                   and abs(p["_centre"][1] - centre[1]) < 500 for p in placed):
                continue                                   # the same unit on another roof plan
            placed.append({**element, "_centre": centre,
                           "storey": {k: top[k] for k in ("placement_id", "base_level_key", "base_elevation_mm")}})
    for element in placed:
        element.pop("_centre", None)
    return placed


def _drop_duplicate_floors(models: list[dict[str, Any]]):
    """
    Two floor plans of the same storey (same slab level, same place: a plan and its furniture copy, say): the
    one with more walls is used. Plans at one level in different places (two wings) are both kept.
    """
    def extent(model):
        boxes = [_bbox(e) for e in model["elements"] if e.get("element_type") == "wall"]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    def overlap(a, b):
        w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
        if w <= 0 or h <= 0:
            return 0.0
        inter = w * h
        return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)

    plans = sorted((m for m in models if m.get("storeys")),
                   key=lambda m: -sum(1 for e in m["elements"] if e.get("element_type") == "wall"))
    kept, duplicates = [], []
    for model in plans:
        level, box = model["storeys"][0]["base_elevation_mm"], extent(model)
        twin = next((k for k in kept if abs(k["storeys"][0]["base_elevation_mm"] - level) < 100 and box
                     and extent(k) and overlap(box, extent(k)) >= 0.6), None)
        if twin is not None:
            duplicates.append((model, twin))
        else:
            kept.append(model)
    keep = [m for m in models if not m.get("storeys") or any(m is k for k in kept)]   # roof plans stay (no storey)
    return keep, duplicates


def load_imported(folder: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    out_dir = Path(folder) / "artifacts" / IMPORTED_DIR
    models = []
    for record in model_records(project):
        path = out_dir / f"{record['id']}.json"
        if path.is_file():
            models.append(json.loads(path.read_text("utf-8")))
    return models


def build_from_imported(models: list[dict[str, Any]], project: dict[str, Any], project_id: str) -> dict[str, Any]:
    """Building model (same shape as the PDF one) from imported IFC or DXF models."""
    from app.services import building_model

    ifc = [m for m in models if m["source_type"] == "ifc"]
    chosen = ifc or [m for m in models if m["source_type"] == "dxf"]
    duplicates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    if not ifc:
        chosen, duplicates = _drop_duplicate_floors(chosen)
    unused = [m for m in models if m not in chosen and all(m is not d for d, _ in duplicates)]
    storeys = [s for m in chosen for s in m["storeys"]]
    elements = [e for m in chosen for e in m["elements"]]
    elements += _roof_equipment(chosen, storeys)
    rooms = [r for m in chosen for r in m["rooms"]]
    storeys.sort(key=lambda s: s["base_elevation_mm"])

    slabs: list[dict[str, Any]] = []
    envelope = None
    stair_notes: list[str] = []
    furniture_notes: list[str] = []
    soil_notes: list[str] = []
    served_count = len(elements)
    if not ifc:
        # DXF plans carry walls and columns only: join them and outline the floors like PDF plans,
        # then mark the outside walls and add the roof's parapet if the file prints its height.
        from app.services import envelope_details, roof_assembly

        from app.services import furnishing, stair_detection

        soil_notes = building_model.drop_columns_in_soil(storeys, elements)
        building_model.add_wall_joins(elements)
        slabs = building_model.approximate_slabs(storeys, elements)
        facts = envelope_details.envelope_facts([t for m in chosen for t in m.get("envelope_texts", [])])

        # Stairs and lifts from the lines drawn round each STAIR / LIFT label, rising to the next floor.
        contexts = {}
        for m in chosen:
            for storey in m["storeys"]:
                pid = storey["placement_id"]
                mine = [e for e in elements if e["storey"]["placement_id"] == pid]
                context = m.get("stair_context") or {}
                contexts[pid] = {
                    "lines": [tuple(line) for line in context.get("lines", [])],
                    "rects": [r for e in mine if e.get("element_type") in ("wall", "column", "door", "window", "opening")
                              for r in ([tuple(x) for x in e["plan_rects"]] if e.get("plan_rects") else [_bbox(e)])],
                    "arrows": [tuple(a) for a in context.get("arrows", [])],
                    "labels": [(r["name"], (r["position_mm"][0], r["position_mm"][1])) for r in rooms
                               if (r.get("storey") or {}).get("placement_id") == pid],
                    "offset": (0.0, 0.0), "served": mine}
        stairs, stair_voids, stair_notes = stair_detection.stairs_from_contexts(storeys, contexts, facts.get("slab_thickness_mm"))
        elements.extend(stairs)
        lift_doors = {i for e in stairs for i in ((e.get("lift") or {}).get("door_ids") or [])}
        for element in elements:
            if element["element_id"] in lift_doors:
                element["lift_door"] = True
        for void in stair_voids:
            slab = next((s for s in slabs if s["element_id"] == f"slab:{void['placement_id']}:base" and s.get("footprint")), None)
            if slab:
                slab["footprint"]["rects_mm"] = stair_detection.subtract_rect(slab["footprint"]["rects_mm"], void["rect_mm"])
                slab.setdefault("openings", []).append(void)

        envelope = roof_assembly.finish_envelope(
            storeys, elements, slabs, [n for m in chosen for n in m.get("parapet_notes", [])],
            {m["model_id"] for m in chosen},
            facts=facts, axes={k: v for m in chosen for k, v in (m.get("grid_axes") or {}).items()})

        furniture, furniture_notes = furnishing.furnish_building(storeys, elements, rooms)
        elements.extend(furniture)
        furniture_notes += furnishing.open_double_height_rooms(rooms, slabs)
        building_model.trim_slabs_to_slanted_walls(slabs, elements, storeys)

    excluded = [{"drawing_id": d["id"], "drawing_title": d.get("filename"),
                 "reason": "A CAD/BIM model was uploaded, so the PDF drawings are not used for the 3D model."}
                for d in project.get("drawings") or []]
    excluded += [{"drawing_id": m["model_id"], "drawing_title": m["title"],
                  "reason": "An IFC model was uploaded; it is used instead of the DXF plans."} for m in unused]
    excluded += [{"drawing_id": d["model_id"], "drawing_title": d["title"],
                  "reason": f"Same floor as {kept['title']} (same level, same place); that plan is used."}
                 for d, kept in duplicates]
    excluded += [{"drawing_id": r["id"], "drawing_title": r["filename"],
                  "reason": f"Reference sheet ({cad_sheet_role(r['filename'])[1]}): not modelled."}
                 for r in model_records(project) if r["model_type"] == "dxf"
                 and cad_sheet_role(r["filename"])[0] == "reference"]
    count = lambda kind: sum(1 for e in elements if e.get("element_type") == kind)   # noqa: E731
    source = "IFC" if ifc else "DXF"
    return {
        "schema_version": "0.1.0",
        "stage": "5.4",
        "artifact_type": "building_model",
        "project_id": project_id,
        "source": {"type": source.lower(), "files": [m["title"] for m in chosen]},
        "coordinate_system": {"unit": "mm", "origin": f"{source} model origin", "x_axis": "east / drawing right",
                              "y_axis": "plan down", "z_axis": "up"},
        "summary": {
            "placed_plans": len(storeys),
            "plans_in_model": len(storeys),
            "walls_served": served_count,
            "walls_withheld": 0,
            "walls_superseded": 0,
            "walls_joined": sum(1 for e in elements if e.get("joined_mesh")),
            "automatic_elements": 0,
            "columns_to_review": 0,
            "doors": count("door"),
            "windows": count("window"),
            "openings": count("opening"),
            "rooms": len(rooms),
            "stairs": len({e["element_id"].rsplit(":", 2)[0] for e in elements if e.get("element_type") == "stair"}),
            "lifts": count("lift shaft"),
            "furniture": count("furniture"),
            "exterior_elements": (envelope or {}).get("exterior_elements", 0),
            "parapets": (envelope or {}).get("parapets", 0),
            "roof": (envelope or {}).get("roof"),
            "plans_excluded": len(excluded),
        },
        "envelope": envelope,
        "stair_notes": stair_notes,
        "furniture_notes": furniture_notes,
        "storeys": storeys,
        "elements": elements,
        "slabs": slabs,
        "rooms": rooms,
        "withheld_elements": [],
        "superseded_elements": [],
        "excluded_plans": excluded,
        "limitations": [n for m in chosen for n in m.get("notes", [])] + (soil_notes if not ifc else []) + [
            f"Built from the uploaded {source} model(s); geometry, levels and openings come from the file(s).",
        ] + ([f"LOD 500 roof: {envelope['roof_note']}" if envelope["roof_form"] == "gable"
               else f"LOD 500 roof: the top storey's top slab. {envelope['parapet_note']}"] if envelope else []),
    }
