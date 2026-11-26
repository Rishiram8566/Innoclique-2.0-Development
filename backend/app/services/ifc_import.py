"""
Import an IFC model (e.g. exported from Revit: File > Export > IFC).

IFC is a full BIM model, so nothing is inferred here: every wall, slab,
column, beam, door, window, stair and roof comes with its own 3D geometry,
storey and material, and every room (IfcSpace) with its name. The geometry
engine returns metres; everything is stored in millimetres in the building
frame used by the viewer (X east, Y plan-down = -north, Z up).
"""

from __future__ import annotations

import math
import multiprocessing
import re
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ifc_element

SOURCE = "IFC model (authored in BIM software)"

# IFC class -> (element type, minimum LOD)
CLASSES: dict[str, tuple[str, int]] = {
    "IfcColumn": ("column", 100), "IfcBeam": ("beam", 100), "IfcMember": ("member", 100),
    "IfcFooting": ("footing", 100), "IfcPile": ("pile", 100),
    "IfcSlab": ("slab", 200), "IfcRoof": ("roof", 200),
    "IfcWall": ("wall", 300), "IfcWallStandardCase": ("wall", 300), "IfcCurtainWall": ("curtain wall", 300),
    "IfcPlate": ("curtain wall", 300), "IfcDoor": ("door", 300), "IfcWindow": ("window", 300),
    "IfcStair": ("stair", 300), "IfcStairFlight": ("stair", 300), "IfcRamp": ("ramp", 300),
    "IfcRampFlight": ("ramp", 300), "IfcRailing": ("railing", 300),
    "IfcCovering": ("covering", 400), "IfcBuildingElementProxy": ("other", 400),
    "IfcFurnishingElement": ("furniture", 500), "IfcFurniture": ("furniture", 500),
    "IfcFlowTerminal": ("fixture", 500), "IfcSanitaryTerminal": ("fixture", 500),
    "IfcTransportElement": ("lift car", 300),
}

# Plant and building-services equipment. IFC4's IfcChiller, IfcPump, IfcTransformer, IfcElectricDistributionBoard,
# IfcTank, IfcCoolingTower ... are all subclasses of these (IFC2x3 exports use the parents directly). They are
# shown with the furniture at LOD 500. Pipes, ducts, cable trays and their fittings are counted, not drawn.
EQUIPMENT_CLASSES = ("IfcEnergyConversionDevice", "IfcFlowMovingDevice", "IfcFlowStorageDevice",
                     "IfcFlowController", "IfcFlowTreatmentDevice")
SERVICE_RUN_CLASSES = ("IfcFlowSegment", "IfcFlowFitting")
EQUIPMENT_NAMES = [
    ("chiller", "chiller"), ("cooling tower", "cooling tower"), ("pump", "pump set"), ("transformer", "transformer"),
    ("generator", "generator set"), ("genset", "generator set"), ("tank", "tank"), ("boiler", "boiler"),
    ("air handling", "air handling unit"), ("ahu", "air handling unit"), ("fan coil", "fan coil unit"),
    ("fan", "fan"), ("heat exchanger", "heat exchanger"), ("compressor", "compressor"),
    ("ups", "UPS / battery rack"), ("battery", "UPS / battery rack"), ("switchgear", "electrical panel"),
    ("switchboard", "electrical panel"), ("distribution board", "electrical panel"), ("panel", "electrical panel"),
    ("mcc", "electrical panel"), ("valve", "valve"), ("damper", "damper"),
]


def _words(product) -> str:
    type_ = ifc_element.get_type(product)
    return " ".join(str(v or "") for v in (product.is_a()[3:], product.Name, getattr(product, "ObjectType", None),
                                           getattr(type_, "Name", None))).lower()


def _equipment_label(product) -> str | None:
    """What a piece of equipment is, from its IFC class (IfcChiller -> chiller) or its Revit family / type name."""
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", product.is_a()[3:]).lower() + " " + _words(product)
    for word, label in EQUIPMENT_NAMES:
        if re.search(rf"\b{re.escape(word)}", words):
            return label
    return None


def _component_name(product, kind: str) -> str:
    """A readable name for furniture and equipment: the Revit family name without its size and id."""
    label = _equipment_label(product)
    if label:
        return label
    name = str(product.Name or getattr(product, "ObjectType", None) or kind).split(":")[0]
    name = re.sub(r"^(?:M_|Rvt_|RVT_)", "", name).replace("_", " ").strip()
    return name.lower() or kind


# Revit exports many details as generic proxies; their type names say what they are.
PROXY_KINDS = [
    ("seam", ("roof seam", 400)), ("muntin", ("window muntin", 400)), ("mullion", ("window muntin", 400)),
    ("trim", ("trim", 400)), ("architrave", ("trim", 400)), ("casing", ("trim", 400)),
    ("gutter", ("gutter", 400)), ("downpipe", ("downpipe", 400)), ("rainwater", ("downpipe", 400)),
    ("fascia", ("fascia", 400)), ("barge", ("fascia", 400)), ("soffit", ("fascia", 400)),
    ("chimney", ("chimney", 300)), ("flue", ("chimney", 300)), ("step", ("step", 300)), ("stair", ("stair", 300)),
    ("railing", ("railing", 300)), ("handrail", ("railing", 300)),
    ("ridge", ("roof seam", 400)), ("frieze", ("trim", 400)), ("cornice", ("trim", 400)),
]


def _kind_of(product) -> tuple[str, int]:
    """Element type and minimum LOD, using the IFC class, slab type and (for proxies) the type name."""
    if product.is_a("IfcSlab") and (getattr(product, "PredefinedType", None) == "ROOF"
                                    or (ifc_element.get_aggregate(product) or product).is_a("IfcRoof")):
        return "roof", 300
    if product.is_a("IfcBuildingElementProxy") or product.is_a("IfcCovering") is False and product.is_a("IfcFurnishingElement"):
        type_ = ifc_element.get_type(product)
        name = " ".join(str(v or "") for v in (product.Name, getattr(product, "ObjectType", None),
                                                 getattr(type_, "Name", None))).lower()
        for word, kind in PROXY_KINDS:
            if word in name:
                return kind
    if product.is_a("IfcCovering"):
        return ("ceiling", 400) if getattr(product, "PredefinedType", None) == "CEILING" else ("covering", 400)
    if any(product.is_a(c) for c in EQUIPMENT_CLASSES):
        return "furniture", 500
    if product.is_a("IfcBuildingElementProxy") and _equipment_label(product):
        return "furniture", 500                     # equipment exported as a generic proxy, named by its family
    return next((v for c, v in CLASSES.items() if product.is_a(c)), ("other", 400))


def _to_building(x: float, y: float, z: float) -> list[float]:
    """IFC metres (x east, y north, z up) -> building mm (X east, Y plan-down, Z up)."""
    return [round(x * 1000.0, 1), round(-y * 1000.0, 1), round(z * 1000.0, 1)]


def _hex(material) -> str | None:
    try:
        d = material.diffuse
        r, g, b = (d.r(), d.g(), d.b()) if hasattr(d, "r") else (d[0], d[1], d[2])
        return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(round(c * 255)))) for c in (r, g, b)))
    except Exception:
        return None


def _material_parts(geometry, triangles) -> list[dict[str, Any]] | None:
    """Faces grouped by their IFC surface style (e.g. a window's frame and its glass), with colour and opacity."""
    materials = list(geometry.materials)
    ids = list(geometry.material_ids)
    if not materials or len(ids) != len(triangles):
        return None
    groups: dict[int, list] = {}
    for triangle, material_id in zip(triangles, ids):
        groups.setdefault(material_id, []).append(triangle)
    parts = []
    for material_id, faces in sorted(groups.items()):
        material = materials[material_id] if 0 <= material_id < len(materials) else None
        transparency = float(getattr(material, "transparency", 0.0) or 0.0) if material is not None else 0.0
        if transparency != transparency:          # NaN
            transparency = 0.0
        parts.append({"color": _hex(material) if material is not None else None,
                      "opacity": round(max(0.15, 1.0 - transparency), 2), "faces": faces})
    return parts if len(parts) > 1 or any(part["opacity"] < 1 for part in parts) else None


def _is_external(product) -> bool | None:
    """Pset_*Common.IsExternal when the model states it (Revit exports it for walls, doors, windows)."""
    try:
        for name, values in ifc_element.get_psets(product).items():
            if name.endswith("Common") and "IsExternal" in values:
                return bool(values["IsExternal"])
    except Exception:
        pass
    return None


def _storey_of(product):
    container = ifc_element.get_container(product)
    if container is None:
        parent = ifc_element.get_aggregate(product)
        while parent is not None and not parent.is_a("IfcBuildingStorey"):
            parent = ifc_element.get_aggregate(parent)
        container = parent
    while container is not None and not container.is_a("IfcBuildingStorey"):
        container = ifc_element.get_aggregate(container)
    return container


def _material_name(product) -> str | None:
    material = ifc_element.get_material(product)
    if material is None:
        return None
    if material.is_a("IfcMaterial"):
        return material.Name
    for attr in ("ForLayerSet", "MaterialLayers", "Materials"):
        items = getattr(material, attr, None)
        if items:
            first = items[0] if isinstance(items, (list, tuple)) else items
            return getattr(first, "Name", None) or getattr(getattr(first, "Material", None), "Name", None)
    return getattr(material, "Name", None)


def import_ifc(path: Path, *, model_id: str, title: str) -> dict[str, Any]:
    model = ifcopenshell.open(str(path))
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)

    storeys = []
    for storey in model.by_type("IfcBuildingStorey"):
        placement = getattr(storey, "ObjectPlacement", None)
        elevation_m = None
        if storey.Elevation is not None:
            from ifcopenshell.util.unit import calculate_unit_scale
            elevation_m = float(storey.Elevation) * calculate_unit_scale(model)
        storeys.append({"entity": storey, "elevation_m": elevation_m, "placement": placement})
    storeys.sort(key=lambda s: s["elevation_m"] if s["elevation_m"] is not None else 0.0)

    wanted = [p for p in model.by_type("IfcProduct")
              if any(p.is_a(c) for c in CLASSES) or any(p.is_a(c) for c in EQUIPMENT_CLASSES) or p.is_a("IfcSpace")]
    service_runs = sum(1 for p in model.by_type("IfcProduct") if any(p.is_a(c) for c in SERVICE_RUN_CLASSES))
    elements, rooms = [], []
    if wanted:
        iterator = ifcopenshell.geom.iterator(settings, model, max(1, multiprocessing.cpu_count() - 1), include=wanted)
        if iterator.initialize():
            while True:
                shape = iterator.get()
                product = model.by_id(shape.id)
                verts, faces = shape.geometry.verts, shape.geometry.faces
                vertices = [_to_building(verts[i], verts[i + 1], verts[i + 2]) for i in range(0, len(verts), 3)]
                triangles = [[faces[i], faces[i + 2], faces[i + 1]] for i in range(0, len(faces), 3)]  # mirrored frame
                storey = _storey_of(product)
                xs = [v[0] for v in vertices]; ys = [v[1] for v in vertices]; zs = [v[2] for v in vertices]
                if product.is_a("IfcSpace"):
                    name = (product.LongName or product.Name or "Space").strip()
                    rooms.append({
                        "name": name.upper(),
                        "size_mm": [round(max(xs) - min(xs)), round(max(ys) - min(ys))] if xs else None,
                        "position_mm": [round((min(xs) + max(xs)) / 2, 1), round((min(ys) + max(ys)) / 2, 1), min(zs)] if xs else None,
                        "storey_entity": storey,
                        "source": "IfcSpace in the IFC model",
                    })
                else:
                    kind, min_lod = _kind_of(product)
                    colours = [c for c in (_hex(m) for m in shape.geometry.materials) if c and c != "#000000"]
                    parts = _material_parts(shape.geometry, triangles)
                    material = _material_name(product)
                    size = sorted([max(xs) - min(xs), max(ys) - min(ys)]) if xs else [0, 0]
                    if kind == "furniture":
                        component = _component_name(product, kind)
                    else:
                        component = kind if kind not in ("wall", "other", "covering") else (material or kind).lower()
                    elements.append({
                        "element_id": product.GlobalId,
                        "element_type": kind,
                        "component": component,
                        **({"furniture": {"room": None, "room_type": component, "modelled": True}}
                           if kind == "furniture" else {}),
                        "ifc_class": product.is_a(),
                        "name": product.Name,
                        "material": material,
                        "is_external": _is_external(product),
                        "color": next((c for c in colours if c), None),
                        "detected_by": "ifc",
                        "reviewed_by": SOURCE,
                        "storey_entity": storey,
                        # Slabs and roofs are flat: their thickness is the vertical extent, not the plan width.
                        "dimensions_mm": ({"length": round(size[1], 1), "thickness": round(max(zs) - min(zs), 1),
                                           "height": round(max(zs) - min(zs), 1), "volume": 0.0}
                                          if kind in ("slab", "roof", "ceiling") and zs else
                                          {"length": round(size[1], 1), "thickness": round(size[0], 1),
                                           "height": round(max(zs) - min(zs), 1) if zs else 0.0, "volume": 0.0}),
                        "mesh": {"vertices": vertices, "faces": triangles},
                        "mesh_parts": parts,
                        "lod": {"category": kind, "minimum_lod": min_lod},
                        "provenance": {"source": SOURCE, "ifc_class": product.is_a(), "global_id": product.GlobalId,
                                       "file": title},
                    })
                if not iterator.next():
                    break

    # Storeys: elevation from the storey, height up to the next storey.
    storey_rows = []
    for i, storey in enumerate(storeys):
        entity = storey["entity"]
        elevation_mm = round((storey["elevation_m"] or 0.0) * 1000.0, 1)
        next_mm = round(storeys[i + 1]["elevation_m"] * 1000.0, 1) if i + 1 < len(storeys) and storeys[i + 1]["elevation_m"] is not None else None
        storey_rows.append({
            "placement_id": entity.GlobalId,
            "drawing_id": model_id,
            "drawing_title": f"{title} — {entity.Name or 'Storey'}",
            "page_number": 1,
            "base_level_key": entity.Name or f"Level {i}",
            "base_elevation_mm": elevation_mm,
            "top_level_key": storeys[i + 1]["entity"].Name if next_mm is not None else None,
            "storey_height_mm": round(next_mm - elevation_mm, 1) if next_mm is not None else None,
            "reference_label": "IFC project origin",
        })
    by_storey = {row["placement_id"]: row for row in storey_rows}
    default = storey_rows[0] if storey_rows else {"placement_id": f"{model_id}:storey", "base_level_key": "Model",
                                                   "base_elevation_mm": 0.0}

    def storey_ref(entity):
        row = by_storey.get(entity.GlobalId) if entity is not None else None
        row = row or default
        return {"placement_id": row["placement_id"], "base_level_key": row["base_level_key"],
                "base_elevation_mm": row["base_elevation_mm"]}

    for element in elements:
        element["storey"] = storey_ref(element.pop("storey_entity"))
        element["drawing_id"] = model_id
        element["drawing_title"] = title
    for room in rooms:
        room["storey"] = storey_ref(room.pop("storey_entity"))
        room["drawing_id"] = model_id
    if not storey_rows:
        storey_rows = [{**default, "drawing_id": model_id, "drawing_title": title, "page_number": 1,
                        "top_level_key": None, "storey_height_mm": None, "reference_label": "IFC project origin"}]
    for row in storey_rows:
        row["walls_served"] = sum(1 for e in elements if e["storey"]["placement_id"] == row["placement_id"])
        row["walls_withheld"] = 0

    return {
        "source_type": "ifc",
        "model_id": model_id,
        "title": title,
        "schema": model.schema,
        "storeys": storey_rows,
        "elements": elements,
        "rooms": [r for r in rooms if r.get("position_mm")],
        "units": "mm",
        "notes": [f"{len(elements)} elements and {len(rooms)} spaces read from {title} ({model.schema})."]
                 + ([f"{sum(1 for e in elements if e['element_type'] == 'furniture')} equipment / furniture item(s) "
                     "as modelled (shown at LOD 500)."] if any(e["element_type"] == "furniture" for e in elements) else [])
                 + ([f"{service_runs} pipe, duct and cable-tray piece(s) in the model are not drawn."] if service_runs else []),
    }
