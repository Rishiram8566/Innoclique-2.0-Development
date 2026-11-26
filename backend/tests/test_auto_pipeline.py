import pytest

from app.services import auto_pipeline as ap


@pytest.mark.parametrize("title, expected", [
    ("2BHK_GROUND_FLOOR_PLAN.pdf", True),
    ("DSP-DCP-DWG-ARC-1002_BASEMENT FLOOR - UPPER LEVEL.pdf", True),
    ("DSP-DCP-DWG-ARC-1007_ROOF FLOOR - TOP ROOF PLAN.pdf", True),
    ("DSP-DCP-DWG-ARC-11001_FLOOR FINISHES LAYOUT - 1.pdf", False),
    ("DSP-DCP-DWG-ARC-12001_REFLECTED CEILING PLAN - 1.pdf", False),
    ("DSP-DCP-DWG-ARC-2001_SECTION A - A.pdf", False),
    ("DSP-DCP-DWG-ARC-1601_PARTIAL DRAWINGS - RECEPTION.pdf", False),
    ("DSP-DCP-DWG-GEN-1000_SITE SETTING OUT PLAN.pdf", False),
    ("DOOR SCHEDULE.pdf", False),
    (None, False),
])
def test_floor_plans_are_chosen_by_title(title, expected):
    assert ap.is_floor_plan(title) is expected


def test_printed_scale_prefers_the_title_block_and_refuses_ambiguity():
    assert ap.printed_scale({"titled_scales": {"1:100"}, "scales": {"1:100", "1:20"}}) == 100
    assert ap.printed_scale({"titled_scales": set(), "scales": {"1:200"}}) == 200
    assert ap.printed_scale({"titled_scales": {"1:100", "1:50"}, "scales": {"1:100"}}) is None
    assert ap.printed_scale({"scales": {"1:100", "1:50"}}) is None
    assert ap.printed_scale(None) is None


def test_printed_scale_at_true_paper_size():
    # A3 width 1190.55 pt = 420 mm on paper; at 1:100 that is 42 m.
    assert ap.millimetres_per_point(100) * 1190.55 == pytest.approx(42000, rel=1e-4)
    assert ap.millimetres_per_point(200) == pytest.approx(70.5556, rel=1e-4)


def test_shared_grid_reference_is_the_first_grid_every_plan_has():
    grids = {
        "basement": {"vertical": [("B", 700.0), ("A", 576.7), ("C", 763.1)], "horizontal": [("1", 264.6), ("2", 372.5)]},
        "ground": {"vertical": [("A", 576.7), ("B", 716.7)], "horizontal": [("2", 372.5), ("1", 264.6)]},
    }
    assert ap.shared_grid_reference(grids) == ("A", "1")
    assert ap.grid_point(grids["ground"], "A", "1") == (576.7, 264.6)
    grids["roof"] = {"vertical": [("C", 763.1)], "horizontal": [("2", 372.5)]}
    assert ap.shared_grid_reference(grids) is None          # no grid line common to all three
    assert ap.shared_grid_reference({}) is None


def test_status_reports_not_started_and_interrupted(tmp_path):
    (tmp_path / "artifacts").mkdir()
    assert ap.read_status(tmp_path) is None
    progress = ap.Progress(tmp_path, "p-1")
    progress.step("read", "running")
    status = ap.read_status(tmp_path)
    assert status["state"] == "interrupted"                 # not in the running set: the server restarted
    read = next(step for step in status["steps"] if step["key"] == "read")
    assert read["status"] == "running" and status["reviewer"] == ap.AUTO_REVIEWER


def test_atomic_save_retries_when_windows_denies_access(tmp_path, monkeypatch):
    from app.services import height_verification_ledger as ledger_service

    real_replace = ledger_service.os.replace
    calls = {"n": 0}

    def flaky_replace(source, target):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied")
        real_replace(source, target)

    monkeypatch.setattr(ledger_service.os, "replace", flaky_replace)
    monkeypatch.setattr(ledger_service, "REPLACE_WAIT_S", 0.0)
    target = ledger_service.save_json_atomic(tmp_path / "ledger.json", {"ok": True})
    assert calls["n"] == 3 and target.read_text("utf-8").strip().endswith("}")


def test_a_locked_status_file_does_not_stop_the_run(tmp_path, monkeypatch):
    (tmp_path / "artifacts").mkdir()
    progress = ap.Progress(tmp_path, "p-locked")

    def locked(*_args, **_kwargs):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(ap.ledger_service, "save_json_atomic", locked)
    progress.step("read", "done", "1 PDF(s) read")            # must not raise
    live = ap.read_status(tmp_path, "p-locked")
    read = next(step for step in live["steps"] if step["key"] == "read")
    assert read == {"key": "read", "label": "Read the PDFs", "status": "done", "detail": "1 PDF(s) read"}
    with pytest.raises(PermissionError):
        progress.save(final=True)


def test_an_empty_result_says_why_in_one_sentence():
    from app.services.auto_pipeline import nothing_built_reason

    assert "scanned" in nothing_built_reason([], 3) and "DWG / IFC" in nothing_built_reason([], 3)
    reason = nothing_built_reason([{"title": "GF PLAN.pdf", "notes": ["scale 1:100 from the sheet", "no grid found - not placed"]}], 1)
    assert "GF PLAN.pdf: no grid found" in reason


def test_structure_project_given_a_house_plan_says_to_choose_building():
    from app.services.structure_model import building_plan_hint

    lines = [{"text": t} for t in ("LIVING ROOM", "BED ROOM 1", "KITCHEN", "TOILET")]
    assert "Building" in building_plan_hint(lines)
    assert "GROUND FLOOR PLAN" in building_plan_hint([{"text": "SAMPLE HOUSE - GROUND FLOOR PLAN"}])
    assert building_plan_hint([{"text": "TOP OF DAM EL 320.00"}, {"text": "SPILLWAY"}]) is None


def test_geometry_from_the_older_extractor_is_extracted_again(tmp_path):
    import json as _json
    from app.services.geometry_pipeline import _previous_indexes

    target = tmp_path / "drawing_geometry_stage5.json"
    target.write_text(_json.dumps({"drawings": [
        {"drawing_id": "old", "source": {"sha256": "aaa"}, "summary": {"vector_primitive_count": 50000}},
        {"drawing_id": "new", "source": {"sha256": "bbb"}, "source_sha256": "bbb", "summary": {}},
        {"drawing_id": "new2", "source_sha256": "ccc", "source": {"sha256": "ccc"}, "summary": {}},
        {"drawing_id": "capped", "source_sha256": "ddd", "summary": {"vector_primitive_count": 50000}},
    ], "failed": []}), "utf-8")
    hashes, _ = _previous_indexes(target)
    # Older records (hash only under "source", or a page cut at the old 50,000 limit) no longer match their PDF.
    assert hashes == {"old": "", "new": "bbb", "new2": "ccc", "capped": ""}


def test_viewer_keeps_the_model_from_before_a_run_while_it_runs(tmp_path, monkeypatch):
    """While Generate runs, the files keep changing: the viewer gets the model built before the run, marked."""
    import json as _json

    from app.services import auto_pipeline
    from app.services import building_model

    (tmp_path / "project.json").write_text(_json.dumps({"name": "Dam", "project_type": "water_dam", "drawings": []}), "utf-8")
    pid = "viewer-run-test"
    body, before_run = building_model.viewer_model_json(tmp_path, pid)
    assert not before_run and _json.loads(body)["artifact_type"] == "structure_model"
    monkeypatch.setattr(auto_pipeline, "_RUNNING", {pid})
    (tmp_path / "progress.txt").write_text("a run is writing files", "utf-8")
    again, before_run = building_model.viewer_model_json(tmp_path, pid)
    assert before_run and again == body
    monkeypatch.setattr(auto_pipeline, "_RUNNING", set())
    assert building_model.viewer_model_json(tmp_path, pid)[1] is False


def test_a_rerun_with_nothing_new_neither_reads_nor_rewrites_the_geometry(tmp_path, monkeypatch):
    """A large project's geometry is gigabytes: a re-run with no changed PDF uses the small index files only."""
    import json

    import fitz

    from app.services import geometry_pipeline as gp

    (tmp_path / "drawings").mkdir()
    doc = fitz.open()
    page = doc.new_page()
    page.draw_line((50, 50), (300, 50))
    page.draw_rect(fitz.Rect(60, 80, 200, 160))
    page.insert_text((60, 200), "GROUND FLOOR PLAN")
    doc.save(tmp_path / "drawings" / "a.pdf")
    project = {"id": "p", "name": "t", "drawings": [{"id": "d1", "filename": "a.pdf", "stored_filename": "a.pdf"}]}

    first = gp.extract_project_geometry(tmp_path, project, batch_size=5)
    assert first["summary"]["extracted_count"] == 1 and first["summary"]["remaining_count"] == 0
    target = tmp_path / "artifacts" / "drawing_geometry_stage5.json"
    stat = target.stat()
    # The metadata index as scale_calibration writes it after a run.
    record = next(gp._iter_drawing_metadata(target))
    (target.parent / gp.METADATA_INDEX_NAME).write_text(json.dumps({
        "source_size_bytes": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
        "drawings": [{"drawing_id": "d1", "source_sha256": record["source_sha256"], "summary": record["summary"]}],
    }), "utf-8")

    def no_big_read(*_args, **_kwargs):
        raise AssertionError("read the whole geometry artifact")

    monkeypatch.setattr(gp, "_iter_drawing_metadata", no_big_read)
    monkeypatch.setattr(gp, "_iter_array_items", no_big_read)
    again = gp.extract_project_geometry(tmp_path, project, batch_size=5)
    assert again["summary"]["extracted_count"] == 1 and again["summary"]["remaining_count"] == 0
    assert again["summary"]["newly_processed_count"] == 0
    assert target.stat().st_mtime_ns == stat.st_mtime_ns          # not rewritten
