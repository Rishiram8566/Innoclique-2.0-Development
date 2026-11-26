"""
Civil structures (cell towers, water dams, power plants) built from the dimensions
printed on their drawings.

The project type decides: "telecom_tower", "water_dam" or "power_station". These
projects skip the floor-plan pipeline (no walls or rooms to find). Their model is
built from the text of every uploaded PDF by the matching module, and has the same
shape as a building model so the viewer can show it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services import structure_common as sc


def is_structure(project: dict[str, Any]) -> bool:
    return (project.get("project_type") or "") in sc.STRUCTURE_TYPES


def build(folder: Path, project: dict[str, Any], project_id: str) -> dict[str, Any]:
    kind = project.get("project_type")
    lines = sc.drawing_lines(folder, project)
    facts = sc.Facts(lines)
    files = [d.get("filename") for d in project.get("drawings") or []]
    first = (project.get("drawings") or [{}])[0]
    drawing = {"id": first.get("id"), "title": first.get("filename")}
    if not lines:
        storey = sc.structure_storey(drawing["title"], drawing["id"], 0.0, 0.0, "ground")
        why = ("No text could be read from the uploaded PDFs (scanned drawings need OCR)." if files else
               "No drawings uploaded to this project yet: upload its PDF drawings, then press Generate 3D model.")
        return sc.model(project_id, sc.STRUCTURE_TYPES.get(kind, kind), storey, [], facts, {}, [why], files)
    if kind == "telecom_tower":
        from app.services import tower_model
        model = tower_model.build(project_id, facts, files, drawing)
    elif kind == "water_dam":
        from app.services import dam_model
        model = dam_model.build(project_id, facts, files, drawing)
    else:
        from app.services import power_plant_model
        power_plant_model.register_outlines(folder, project, lines)
        model = power_plant_model.build(project_id, facts, files, drawing, lines)
    if not model.get("elements"):
        hint = building_plan_hint(lines)
        if hint:
            model.setdefault("limitations", []).insert(0, hint)
    return model


def building_plan_hint(lines: list[dict[str, Any]]) -> str | None:
    """A structure project whose drawings are a building's floor plan: say so, and which type to choose."""
    from app.services.drawing_text_facts import room_label

    import re

    rooms = sorted({name for name in (room_label(line.get("text", "")) for line in lines) if name})
    title = next((line["text"] for line in lines
                  if re.search(r"\bFLOOR PLAN\b|\b(?:RESIDENTIAL|HOUSE|VILLA|APARTMENT)\b", line.get("text", ""), re.I)), None)
    if len(rooms) < 3 and title is None:
        return None
    evidence = f"rooms such as {', '.join(rooms[:4])}" if len(rooms) >= 3 else f"\"{title[:60]}\""
    return (f"These drawings look like a building floor plan ({evidence}), not a structure of this project's type. "
            "Create the project as \"Building\" and upload them there.")
