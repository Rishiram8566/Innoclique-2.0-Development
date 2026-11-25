"""
Snap a clicked PDF point to the nearest drawn line endpoint on a page.

Used by the calibration screen so control points sit exactly on vector
geometry (dimension-line ends, grid intersections) instead of wherever a
mouse click landed. Reads the page's primitive sidecar; nothing is written.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(tags=["Drawing snap"])

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"
GRID_PDF_POINTS = 16.0
MAX_RADIUS_PDF_POINTS = 40.0


@lru_cache(maxsize=1)
def _endpoint_index(sidecar: str, mtime_ns: int, size: int) -> dict[tuple[int, int], list[tuple]]:
    """Grid index of line endpoints: (cell) -> [(x, y, path_index, item_index)].

    mtime_ns and size are part of the cache key so a rewritten sidecar is re-read.
    """
    grid: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    with open(sidecar, "r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            primitive = json.loads(line)
            if primitive.get("type") != "line":
                continue
            for point in (primitive.get("start"), primitive.get("end")):
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    continue
                x, y = float(point[0]), float(point[1])
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                cell = (int(x // GRID_PDF_POINTS), int(y // GRID_PDF_POINTS))
                grid[cell].append((x, y, primitive.get("path_index"), primitive.get("item_index")))
    return dict(grid)


def snap_point(sidecar: Path, x: float, y: float, radius: float) -> dict:
    """Nearest line endpoint within ``radius`` PDF points of (x, y)."""
    if not sidecar.is_file():
        raise FileNotFoundError("Primitive sidecar for this page is missing")
    stat = sidecar.stat()
    index = _endpoint_index(str(sidecar), stat.st_mtime_ns, stat.st_size)

    reach = int(radius // GRID_PDF_POINTS) + 1
    cx, cy = int(x // GRID_PDF_POINTS), int(y // GRID_PDF_POINTS)
    best = None
    for gx in range(cx - reach, cx + reach + 1):
        for gy in range(cy - reach, cy + reach + 1):
            for px, py, path_index, item_index in index.get((gx, gy), ()):
                distance = math.hypot(px - x, py - y)
                if distance <= radius and (best is None or distance < best[0]):
                    best = (distance, px, py, path_index, item_index)

    if best is None:
        return {
            "snapped": False,
            "point_pdf": [round(x, 3), round(y, 3)],
            "distance_pdf_points": None,
            "source_line": None,
        }
    distance, px, py, path_index, item_index = best
    return {
        "snapped": True,
        "point_pdf": [px, py],
        "distance_pdf_points": round(distance, 3),
        "source_line": {"path_index": path_index, "item_index": item_index},
    }


@router.get("/api/projects/{project_id}/drawings/{drawing_id}/snap-point")
def get_snap_point(
    project_id: str,
    drawing_id: str,
    x: float = Query(...),
    y: float = Query(...),
    page_number: int = Query(1, ge=1),
    radius: float = Query(8.0, gt=0, le=MAX_RADIUS_PDF_POINTS),
):
    try:
        safe_project = str(UUID(project_id))
        safe_drawing = str(UUID(drawing_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Project or drawing not found") from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise HTTPException(status_code=422, detail="x and y must be finite")
    folder = PROJECTS_DIR / safe_project
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Project not found")
    sidecar = folder / "artifacts" / "primitive_sidecars" / f"{safe_drawing}_page_{page_number}.jsonl"
    try:
        return snap_point(sidecar, x, y, radius)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
