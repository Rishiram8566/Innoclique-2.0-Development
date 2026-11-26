"""
Shared pieces for civil structures (cell towers, dams, power plants) built from the
dimensions printed on their drawings.

* Text: every line of every uploaded PDF, with its position on the sheet.
* Parameters: a printed value with the text and drawing it came from. A structure
  model lists every parameter it used.
* Meshes: boxes, prisms, tubes, frustums and solids of revolution in building
  millimetres (X right, Y plan-down, Z up), grouped by colour.
* Elements: the same shape as building elements, with a LOD range (minimum_lod and
  maximum_lod), so a LOD 100 massing can give way to members at LOD 200 and up.
* Model: the same shape as the building model, so the viewer shows structures with
  the same LOD buttons, views and hover cards.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

BOX_FACES = [
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
    [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
]
STRUCTURE_TYPES = {"telecom_tower": "cell tower", "water_dam": "water dam", "power_station": "power plant"}
NUMBER = r"[+-]?\d+(?:\.\d+)?"


# ---------------------------------------------------------------------------
# Text from the PDFs
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def _pdf_lines(path: str, mtime_ns: int) -> tuple:
    import pymupdf

    out = []
    with pymupdf.open(path) as doc:
        for page_index, page in enumerate(doc):
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    text = " ".join("".join(span.get("text", "") for span in line.get("spans", [])).split())
                    if text:
                        out.append((text, tuple(line["bbox"]), page_index, (page.rect.width, page.rect.height)))
    return tuple(out)


def drawing_lines(folder: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    """Every text line of every uploaded PDF: {text, bbox (pt), page, page_size, drawing_id, drawing_title}."""
    lines = []
    for drawing in project.get("drawings") or []:
        path = Path(folder) / "drawings" / drawing.get("stored_filename", "")
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        for text, bbox, page, size in _pdf_lines(str(path), path.stat().st_mtime_ns):
            lines.append({"text": text, "upper": text.upper(), "bbox": bbox, "page": page, "page_size": size,
                          "drawing_id": drawing["id"], "drawing_title": drawing.get("filename")})
    return lines


class Facts:
    """Values printed on the drawings, each kept with the line it came from."""

    def __init__(self, lines: list[dict[str, Any]]):
        self.lines = lines
        self.used: dict[str, dict[str, Any]] = {}

    def find(self, key: str, pattern: str, *, group: int = 1, convert=float, label: str | None = None):
        """First match of the pattern on any line; records it under `key`."""
        regex = re.compile(pattern, re.IGNORECASE)
        for line in self.lines:
            match = regex.search(line["upper"])
            if match:
                value = convert(match.group(group)) if convert else match
                if convert:
                    self.note(key, value, line, label)
                return value
        return None

    def find_all(self, pattern: str):
        regex = re.compile(pattern, re.IGNORECASE)
        return [(regex.search(line["upper"]), line) for line in self.lines if regex.search(line["upper"])]

    def has(self, pattern: str) -> dict[str, Any] | None:
        regex = re.compile(pattern, re.IGNORECASE)
        return next((line for line in self.lines if regex.search(line["upper"])), None)

    def note(self, key: str, value, line: dict[str, Any], label: str | None = None):
        self.used[key] = {"name": label or key.replace("_", " "), "value": value, "text": line["text"],
                          "drawing": line.get("drawing_title")}

    def assume(self, key: str, value, why: str):
        self.used[key] = {"name": key.replace("_", " "), "value": value, "text": None, "assumed": why}

    def parameters(self) -> list[dict[str, Any]]:
        return list(self.used.values())


# ---------------------------------------------------------------------------
# Meshes
# ---------------------------------------------------------------------------

def _normalise(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class Mesh:
    """Triangles grouped by (colour, opacity)."""

    def __init__(self):
        self.vertices: list[list[float]] = []
        self.parts: dict[tuple, list[list[int]]] = {}

    def add(self, points, faces, colour, opacity=1.0):
        offset = len(self.vertices)
        self.vertices += [[round(float(x), 1), round(float(y), 1), round(float(z), 1)] for x, y, z in points]
        self.parts.setdefault((colour, opacity), []).extend([[offset + i for i in f] for f in faces])
        return self

    def box(self, x0, y0, z0, x1, y1, z1, colour, opacity=1.0):
        x0, x1 = sorted((x0, x1)); y0, y1 = sorted((y0, y1)); z0, z1 = sorted((z0, z1))
        pts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        return self.add(pts, BOX_FACES, colour, opacity)

    def prism_x(self, polygon_yz, x0, x1, colour, opacity=1.0):
        """A polygon in the (y, z) plane extruded along X from x0 to x1 (dam sections)."""
        n = len(polygon_yz)
        pts = [(x0, y, z) for y, z in polygon_yz] + [(x1, y, z) for y, z in polygon_yz]
        faces = []
        for k in range(1, n - 1):
            faces += [[0, k + 1, k], [n, n + k, n + k + 1]]
        for k in range(n):
            a, b = k, (k + 1) % n
            faces += [[a, b, n + b], [a, n + b, n + a]]
        return self.add(pts, faces, colour, opacity)

    def terrain_y(self, top_xz, bottom, y0, y1, colour):
        """Ground whose top follows a line in (x, z) (x increasing), over a flat bottom, from y0 to y1."""
        m = len(top_xz)
        pts = []
        for y in (y0, y1):
            pts += [(x, y, z) for x, z in top_xz] + [(x, y, bottom) for x, _ in top_xz]
        a0, b0, a1, b1 = 0, m, 2 * m, 3 * m            # top@y0, bottom@y0, top@y1, bottom@y1
        faces = []
        for i in range(m - 1):
            faces += [[a0 + i, a1 + i, a1 + i + 1], [a0 + i, a1 + i + 1, a0 + i + 1]]      # top surface
            faces += [[b0 + i, a0 + i, a0 + i + 1], [b0 + i, a0 + i + 1, b0 + i + 1]]      # end at y0
            faces += [[b1 + i, b1 + i + 1, a1 + i + 1], [b1 + i, a1 + i + 1, a1 + i]]      # end at y1
        faces += [[b0, b0 + m - 1, b1 + m - 1], [b0, b1 + m - 1, b1]]                      # bottom
        faces += [[b0, b1, a1], [b0, a1, a0], [b0 + m - 1, a0 + m - 1, a1 + m - 1], [b0 + m - 1, a1 + m - 1, b1 + m - 1]]
        return self.add(pts, faces, colour)

    def tube(self, p0, p1, r0, r1=None, colour="#9aa3ad", n=6, caps=True):
        """A (tapered) cylinder between two points: members, legs, pipes."""
        r1 = r0 if r1 is None else r1
        ax = [p1[i] - p0[i] for i in range(3)]
        length = math.sqrt(sum(a * a for a in ax)) or 1.0
        w = [a / length for a in ax]
        helper = (0.0, 0.0, 1.0) if abs(w[2]) < 0.9 else (1.0, 0.0, 0.0)
        u = _normalise(_cross(w, helper))
        v = _cross(w, u)
        pts = []
        for centre, r in ((p0, r0), (p1, r1)):
            for k in range(n):
                a = 2 * math.pi * k / n
                pts.append(tuple(centre[i] + r * (math.cos(a) * u[i] + math.sin(a) * v[i]) for i in range(3)))
        faces = []
        for k in range(n):
            a, b = k, (k + 1) % n
            faces += [[a, b, n + b], [a, n + b, n + a]]
        if caps:
            faces += [[0, k + 1, k] for k in range(1, n - 1)] + [[n, n + k, n + k + 1] for k in range(1, n - 1)]
        return self.add(pts, faces, colour)

    def revolve(self, cx, cy, profile, colour, n=32, opacity=1.0, cap_bottom=True, cap_top=True):
        """A solid of revolution about a vertical axis; profile = [(radius, z), ...] bottom to top."""
        pts, faces = [], []
        for r, z in profile:
            for k in range(n):
                a = 2 * math.pi * k / n
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a), z))
        for ring in range(len(profile) - 1):
            for k in range(n):
                a, b = ring * n + k, ring * n + (k + 1) % n
                faces += [[a, b, b + n], [a, b + n, a + n]]
        base = len(pts)
        if cap_bottom:
            pts.append((cx, cy, profile[0][1]))
            faces += [[base, (k + 1) % n, k] for k in range(n)]
        if cap_top:
            top = len(pts)
            pts.append((cx, cy, profile[-1][1]))
            last = (len(profile) - 1) * n
            faces += [[top, last + k, last + (k + 1) % n] for k in range(n)]
        return self.add(pts, faces, colour, opacity)

    def polygon_prism(self, polygon_xy, z0, z1, colour, opacity=1.0):
        """A plan polygon extruded vertically."""
        n = len(polygon_xy)
        pts = [(x, y, z0) for x, y in polygon_xy] + [(x, y, z1) for x, y in polygon_xy]
        faces = []
        for k in range(1, n - 1):
            faces += [[0, k + 1, k], [n, n + k, n + k + 1]]
        for k in range(n):
            a, b = k, (k + 1) % n
            faces += [[a, b, n + b], [a, n + b, n + a]]
        return self.add(pts, faces, colour, opacity)

    def empty(self) -> bool:
        return not self.vertices

    def out(self):
        faces = [f for fs in self.parts.values() for f in fs]
        parts = [{"color": c, "opacity": o, "faces": fs} for (c, o), fs in self.parts.items()]
        return {"vertices": self.vertices, "faces": faces}, parts


# ---------------------------------------------------------------------------
# Elements and the model
# ---------------------------------------------------------------------------

def element(element_id: str, element_type: str, component: str, mesh: Mesh, storey: dict[str, Any], *,
            lod: tuple[int, int], provenance: dict[str, Any], dimensions: dict[str, Any] | None = None,
            envelope: bool = False, **extra) -> dict[str, Any]:
    data, parts = mesh.out()
    colours = {p["color"] for p in parts}
    return {
        "element_id": element_id, "element_type": element_type, "component": component,
        "detected_by": "structure_parameters", "wall_type": None,
        "dimensions_mm": dimensions or {"length": None, "thickness": None, "height": None},
        "lod": {"category": element_type, "minimum_lod": lod[0], "maximum_lod": lod[1]},
        "height_status": "drawing_parameters", "height_verification": None,
        "provenance": provenance, "opening": None, "between": None, "host_component": None,
        "drawing_id": storey.get("drawing_id"), "drawing_title": storey.get("drawing_title"),
        "storey": {key: storey[key] for key in ("placement_id", "base_level_key", "base_elevation_mm")},
        "mesh": data, "mesh_parts": parts if len(colours) > 1 or any(p["opacity"] < 1 for p in parts) else None,
        "color": parts[0]["color"] if parts else None, "reviewed_by": None, "is_external": True,
        "envelope": envelope, **extra,
    }


def structure_storey(title: str | None, drawing_id: str | None, base_mm: float, height_mm: float, label: str):
    return {
        "placement_id": "structure", "drawing_id": drawing_id, "drawing_title": title,
        "base_level_key": label, "base_elevation_mm": base_mm, "top_level_key": None,
        "storey_height_mm": height_mm, "reference_label": "structure origin", "walls_served": 0, "walls_withheld": 0,
    }


def model(project_id: str, kind: str, storey: dict[str, Any], elements: list[dict[str, Any]], facts: Facts,
          lod_notes: dict[str, str], notes: list[str], files: list[str]) -> dict[str, Any]:
    storey["walls_served"] = len(elements)
    counts: dict[str, int] = {}
    for e in elements:
        counts[e["component"]] = counts.get(e["component"], 0) + 1
    return {
        "schema_version": "0.1.0",
        "stage": "5.4",
        "artifact_type": "structure_model",
        "project_id": project_id,
        "source": {"type": "structure", "structure": kind, "files": files},
        "coordinate_system": {"unit": "mm", "origin": "structure origin", "x_axis": "right", "y_axis": "plan down",
                              "z_axis": "up (levels in mm)"},
        "summary": {
            "placed_plans": 1, "plans_in_model": 1, "walls_served": len(elements), "walls_withheld": 0,
            "walls_superseded": 0, "walls_joined": 0, "automatic_elements": 0, "columns_to_review": 0,
            "doors": 0, "windows": 0, "openings": 0, "rooms": 0, "plans_excluded": 0,
            "structure": kind, "components": counts,
        },
        "storeys": [storey] if elements else [],
        "elements": elements,
        "slabs": [],
        "rooms": [],
        "withheld_elements": [],
        "superseded_elements": [],
        "excluded_plans": [],
        "parameters": facts.parameters(),
        "lod_notes": lod_notes,
        "limitations": notes,
    }


def level_mm(value_m: float) -> float:
    return float(value_m) * 1000.0
