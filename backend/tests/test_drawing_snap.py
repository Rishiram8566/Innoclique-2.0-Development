import json

import pytest
from fastapi import HTTPException

from app.services import drawing_snap


PROJECT = "11111111-1111-4111-8111-111111111111"
DRAWING = "22222222-2222-4222-8222-222222222222"


def line(path, start, end, kind="line"):
    return {"page_number": 1, "path_index": path, "item_index": 0, "type": kind, "start": start, "end": end}


@pytest.fixture
def sidecar(tmp_path, monkeypatch):
    folder = tmp_path / PROJECT / "artifacts" / "primitive_sidecars"
    folder.mkdir(parents=True)
    path = folder / f"{DRAWING}_page_1.jsonl"
    rows = [line(1, [100.0, 100.0], [300.0, 100.0]), line(2, [500.0, 500.0], [500.0, 700.0]),
            line(3, [101.0, 101.0], [120.0, 120.0], kind="bezier")]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(drawing_snap, "PROJECTS_DIR", tmp_path)
    drawing_snap._endpoint_index.cache_clear()
    return path


def test_snaps_to_nearest_line_endpoint(sidecar):
    result = drawing_snap.snap_point(sidecar, 103.0, 98.0, 8.0)
    assert result["snapped"] is True
    assert result["point_pdf"] == [100.0, 100.0]
    assert result["source_line"] == {"path_index": 1, "item_index": 0}


def test_ignores_non_line_primitives_and_far_points(sidecar):
    assert drawing_snap.snap_point(sidecar, 119.0, 119.0, 5.0)["snapped"] is False
    result = drawing_snap.snap_point(sidecar, 400.0, 400.0, 8.0)
    assert result == {"snapped": False, "point_pdf": [400.0, 400.0], "distance_pdf_points": None, "source_line": None}


def test_snaps_across_grid_cells(sidecar):
    result = drawing_snap.snap_point(sidecar, 497.0, 703.0, 10.0)
    assert result["point_pdf"] == [500.0, 700.0]


def test_route_validates_ids_and_missing_sidecar(sidecar):
    assert drawing_snap.get_snap_point(PROJECT, DRAWING, x=299.0, y=101.0, page_number=1, radius=8.0)["point_pdf"] == [300.0, 100.0]
    with pytest.raises(HTTPException) as error:
        drawing_snap.get_snap_point("nope", DRAWING, x=1.0, y=1.0, page_number=1, radius=8.0)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        drawing_snap.get_snap_point(PROJECT, DRAWING, x=1.0, y=1.0, page_number=2, radius=8.0)
    assert error.value.status_code == 404
