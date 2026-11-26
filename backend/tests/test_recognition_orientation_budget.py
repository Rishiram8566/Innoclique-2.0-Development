from app.services.engineering_element_recognition import _wall_pair_candidates


def line(path_index, a, b):
    (x1, y1), (x2, y2) = a, b
    return {"a": a, "b": b, "length": ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5,
            "path_index": path_index, "item_index": 0}


def dense_horizontal_with_one_vertical_pair():
    # Eight closely spaced horizontal lines (hatching) produce many pairs.
    lines = [line(i, (300.0, 300.0 + 5 * i), (500.0, 300.0 + 5 * i)) for i in range(8)]
    # One clean vertical wall pair, 7 pt apart.
    lines += [line(100, (600.0, 300.0), (600.0, 500.0)), line(101, (607.0, 300.0), (607.0, 500.0))]
    return lines


def test_dense_horizontal_lines_no_longer_starve_vertical_pairs():
    candidates = _wall_pair_candidates(
        dense_horizontal_with_one_vertical_pair(), [], 1, 1000.0, 800.0, max_candidates=3,
    )
    vertical = [c for c in candidates if c["geometry_type"] == "parallel_vertical_lines"]
    assert len(vertical) == 1
    assert vertical[0]["source"] == {"line_a": [100, 0], "line_b": [101, 0]}
    assert vertical[0]["bbox_pdf_points"] == [600.0, 300.0, 607.0, 500.0]


def test_each_orientation_respects_its_own_budget():
    candidates = _wall_pair_candidates(
        dense_horizontal_with_one_vertical_pair(), [], 1, 1000.0, 800.0, max_candidates=3,
    )
    horizontal = [c for c in candidates if c["geometry_type"] == "parallel_horizontal_lines"]
    assert 1 <= len(horizontal) <= 3
