"""
Automatic PDF-to-model pipeline: upload PDFs, press one button, get the LOD model.

Runs the existing stages in order and skips whatever is already done:

  1. read PDFs (Stage 3)            6. text evidence (5.3.3)
  2. vector geometry (5.0)          7. level register (5.3.4)
  3. geometry index + sidecars      8. scale from the printed scale
  4. element recognition (5.1)      9. place floor plans (level + grid reference)
  5. (nothing to do here)          10. confirm detected columns and hatch walls
                                    11. generate the model

Every decision the pipeline takes is recorded in the same ledger a person
would use, under the reviewer AUTO_REVIEWER, with the rule it applied - so
the model says which elements nobody has reviewed yet, and any of them can be
revoked in the advanced review screens. Nothing is guessed without evidence:

* scale: the scale printed beside the word SCALE on the sheet (e.g. "1:100"),
  at true paper size (1 pt = 25.4/72 mm on paper); a sheet with no single
  printed scale is left uncalibrated;
* levels: the placement suggestion from the drawing's own level annotations,
  used only when it gives a base and a top level 2-8 m apart;
* reference point: the intersection of the first grid lines every plan shares
  (e.g. A/1), so plans stack; no grid, no placement;
* elements: detected columns and suggested hatch walls (or, on sheets without
  a wall hatch legend, the suggested line-pair walls), all at storey height.

Floor plans are chosen by title: "PLAN" or "FLOOR" and none of the words that
mark other sheet types (ceiling, finishes, landscape, sections, details ...).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.services import height_verification_ledger as ledger_service

AUTO_REVIEWER = "Automatic pipeline (not reviewed)"
STATUS_ARTIFACT = "auto_model_status.json"
INPUTS_ARTIFACT = "auto_model_inputs.json"     # drawings the derived text evidence was built from
BACKEND_DIR = Path(__file__).resolve().parents[2]
MIN_STOREY_MM, MAX_STOREY_MM = 2000.0, 8000.0
MM_PER_PT_ON_PAPER = 25.4 / 72.0

PLAN_WORDS = re.compile(r"\b(PLAN|FLOOR)\b", re.IGNORECASE)
NOT_A_FLOOR_PLAN = re.compile(
    r"CEILING|\bRCP\b|FINISH|LANDSCAPE|HARDSCAPE|\bSITE\b|SETTING OUT|SIGNAGE|WAY ?FINDING|PARTIAL|DETAIL|"
    r"SCHEDULE|SECTION|ELEVATION|PERSPECTIVE|COVER|AREA CALCULATION|KEY PLAN|PARKING|FURNITURE|LAYOUT",
    re.IGNORECASE,
)

STEPS = [
    ("models", "Import CAD / BIM models (IFC, DXF)"),
    ("read", "Read the PDFs"),
    ("geometry", "Extract vector geometry"),
    ("sidecars", "Index drawing primitives"),
    ("recognise", "Recognise elements"),
    ("evidence", "Read text evidence (levels, scales, legends)"),
    ("levels", "Build the level register"),
    ("scale", "Set each floor plan's scale"),
    ("place", "Place floor plans (level + grid reference)"),
    ("confirm", "Detect columns and walls"),
    ("generate", "Generate the 3D model"),
]

_RUNNING: set[str] = set()
_RUNNING_LOCK = threading.Lock()
# Live progress by project, served while a run is active so the page never
# reads the status file while it is being replaced (Windows / OneDrive locks).
_LIVE: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pure rules (unit-tested)
# ---------------------------------------------------------------------------

def is_floor_plan(title: str | None) -> bool:
    # File names use _ and - as separators ("2BHK_GROUND_FLOOR_PLAN.pdf"); treat them as spaces.
    text = re.sub(r"[_\-.]+", " ", title or "")
    return bool(PLAN_WORDS.search(text)) and not NOT_A_FLOOR_PLAN.search(text)


def printed_scale(sheet_entry: dict[str, Any] | None) -> int | None:
    """The sheet's single printed scale denominator (title-block scale first), else None."""
    entry = sheet_entry or {}
    for key in ("titled_scales", "scales"):
        values = {int(s.split(":")[1]) for s in entry.get(key) or [] if re.fullmatch(r"1:\d+", s)}
        if len(values) == 1:
            return values.pop()
        if len(values) > 1:
            return None
    return None


def millimetres_per_point(scale_denominator: int) -> float:
    return MM_PER_PT_ON_PAPER * scale_denominator


def shared_grid_reference(grids: dict[str, dict[str, list[tuple[str, float]]]]) -> tuple[str, str] | None:
    """(vertical, horizontal) grid labels every plan has; the first ones on the first plan."""
    if not grids:
        return None
    first = next(iter(grids.values()))
    vertical_sets = [{label for label, _ in g["vertical"]} for g in grids.values()]
    horizontal_sets = [{label for label, _ in g["horizontal"]} for g in grids.values()]
    common_v = set.intersection(*vertical_sets) if vertical_sets else set()
    common_h = set.intersection(*horizontal_sets) if horizontal_sets else set()
    v = next((label for label, _ in sorted(first["vertical"], key=lambda item: item[1]) if label in common_v), None)
    h = next((label for label, _ in sorted(first["horizontal"], key=lambda item: item[1]) if label in common_h), None)
    return (v, h) if v and h else None


def grid_point(grid: dict[str, list[tuple[str, float]]], vertical: str, horizontal: str) -> tuple[float, float] | None:
    x = next((pos for label, pos in sorted(grid["vertical"], key=lambda i: i[1]) if label == vertical), None)
    y = next((pos for label, pos in sorted(grid["horizontal"], key=lambda i: i[1]) if label == horizontal), None)
    return (x, y) if x is not None and y is not None else None


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

class Progress:
    def __init__(self, folder: Path, project_id: str):
        self.path = folder / "artifacts" / STATUS_ARTIFACT
        self.status: dict[str, Any] = {
            "project_id": project_id,
            "state": "running",
            "started_at": _now(),
            "finished_at": None,
            "steps": [{"key": key, "label": label, "status": "pending", "detail": ""} for key, label in STEPS],
            "plans": [],
            "summary": None,
            "error": None,
            "reviewer": AUTO_REVIEWER,
        }
        self.save()

    def step(self, key: str, status: str, detail: str = "") -> None:
        for step in self.status["steps"]:
            if step["key"] == key:
                step["status"], step["detail"] = status, detail
        self.save()

    def save(self, *, final: bool = False) -> None:
        """Publish progress in memory; persist to disk best-effort (a locked file must not stop the run)."""
        _LIVE[self.status["project_id"]] = json.loads(json.dumps(self.status))
        try:
            ledger_service.save_json_atomic(self.path, self.status)
        except OSError:
            if final:
                raise


def read_status(folder: Path, project_id: str | None = None) -> dict[str, Any] | None:
    if project_id and project_id in _LIVE:
        return json.loads(json.dumps(_LIVE[project_id]))
    path = Path(folder) / "artifacts" / STATUS_ARTIFACT
    if not path.is_file():
        return None
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if status.get("state") == "running" and status.get("project_id") not in _RUNNING:
        status["state"] = "interrupted"   # the server restarted while it ran
    return status


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def _run_script(args: list[str]) -> str:
    result = subprocess.run([sys.executable, *args], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip().splitlines()[-1] if (result.stderr or result.stdout) else "script failed")
    return result.stdout


# PDFs per call of a 'process the next pending PDFs' route. Each call rewrites the project's geometry artifact
# (gigabytes for a large set), so fewer, larger batches are much faster: 71 PDFs take 3 rewrites, not 15.
ROUTE_BATCH_SIZE = 25


def _loop_route(route: Callable[..., dict], project_id: str, limit: int) -> dict:
    """Call a 'process the next pending PDFs' route until nothing remains."""
    report: dict = {}
    for _ in range(limit):
        report = route(project_id, filename=None, batch_size=ROUTE_BATCH_SIZE)
        summary = report.get("summary") or {}
        if not summary.get("remaining_count"):
            break
    return report


def run_auto_model(folder: Path, project_id: str, progress: Progress) -> dict[str, Any]:
    from app import main as app_main
    from app.services import column_detection
    from app.services import column_router
    from app.services import hatch_wall_detection
    from app.services import height_verification_router
    from app.services import plan_placement_router
    from app.services import scale_calibration
    from app.services import wall_mesh_generation as generation
    from app.services.building_model import build_building_model
    from app.services.recognition_supplement import read_recognition
    from app.services.sheet_registration import sheet_index
    from app.services.wall_height_evidence_association import generate_project_height_association

    artifacts = folder / "artifacts"
    project = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    drawings = project.get("drawings") or []
    from app.services import imported_models

    model_files = imported_models.model_records(project)
    if not drawings and not model_files:
        raise ValueError("Upload at least one PDF, DXF or IFC file first.")

    from app.services import structure_model

    if structure_model.is_structure(project):
        # Towers, dams and power plants have no floor plans to trace: their model comes from the printed dimensions.
        for key, _ in STEPS[:-1]:
            progress.step(key, "skipped", "Not needed: this structure is built from its drawings' printed dimensions")
        progress.step("generate", "running")
        model = build_building_model(folder, project_id)
        progress.status["summary"] = {**model["summary"], "slabs": 0,
                                      "automatic_confirmations": {"columns": 0, "hatch_walls": 0, "line_pair_walls": 0}}
        kind = model["summary"].get("structure", "structure")
        if model["elements"]:
            progress.step("generate", "done", f"{len(model['elements'])} element(s) of the {kind} from "
                          f"{len(model.get('parameters') or [])} printed value(s)")
        else:
            progress.step("generate", "failed", (model.get("limitations") or ["Nothing could be built."])[0])
        return progress.status

    # 0: CAD / BIM models come first: they carry real geometry, levels, doors and windows.
    progress.step("models", "running")
    if model_files:
        results = imported_models.import_project_models(folder, project)
        progress.status["models"] = results
        progress.step("models", "done" if any(r["status"] != "failed" for r in results) else "failed",
                      "; ".join(f"{r['title']}: {r['status']}" + (f" - {r['notes'][0]}" if r["notes"] else "")
                                for r in results))
    else:
        progress.step("models", "skipped", "No CAD/BIM models uploaded")
    if imported_models.load_imported(folder, project):
        for key, _ in STEPS[1:-1]:
            progress.step(key, "skipped", "Not needed: the uploaded CAD/BIM model is used")
        progress.step("generate", "running")
        model = build_building_model(folder, project_id)
        progress.status["summary"] = {**model["summary"], "slabs": len(model.get("slabs") or []),
                                      "automatic_confirmations": {"columns": 0, "hatch_walls": 0, "line_pair_walls": 0}}
        progress.step("generate", "done", f"{model['summary']['walls_served']} element(s) in "
                      f"{model['summary']['plans_in_model']} storey(s) from the {model['source']['type'].upper()} model")
        return progress.status
    titles = {d["id"]: d.get("filename") or d["id"] for d in drawings}
    plan_ids = [d["id"] for d in drawings if is_floor_plan(titles[d["id"]])]

    # 1-4: reading, geometry, primitives, recognition (each skips finished PDFs)
    progress.step("read", "running")
    report = app_main.analyze_project_drawings(project_id)
    progress.step("read", "done", f"{report.get('analyzed_count', 0)} PDF(s) read, {report.get('failed_count', 0)} failed")

    progress.step("geometry", "running")
    report = _loop_route(app_main.extract_project_geometry_route, project_id, len(drawings) + 2)
    summary = report.get("summary") or {}
    progress.step("geometry", "done", f"{summary.get('extracted_count', '?')} of {summary.get('drawing_count', '?')} PDF(s) extracted")

    progress.step("sidecars", "running")
    scale_calibration._load_geometry_metadata_index(folder)
    created, failed, first_failure = 0, 0, None
    for drawing_id in plan_ids:          # only floor plans need per-primitive sidecars
        out = _run_script(["-m", "app.services.primitive_sidecar_batch", "--project-id", project_id,
                           "--drawing-id", drawing_id])
        match = re.search(r"Pages created:\s*(\d+)", out)
        created += int(match.group(1)) if match else 0
        match = re.search(r"Pages failed:\s*(\d+)", out)
        failed += int(match.group(1)) if match else 0
        line = next((ln for ln in out.splitlines() if ln.startswith("FAILED")), None)
        first_failure = first_failure or line
    if failed:
        progress.step("sidecars", "failed", f"{failed} of {len(plan_ids)} floor plan page(s) could not be indexed "
                      f"({first_failure or 'see the server log'}); those plans cannot be modelled.")
    else:
        progress.step("sidecars", "done", f"{len(plan_ids)} floor plan(s); {created} page(s) newly indexed")

    progress.step("recognise", "running")
    report = _loop_route(app_main.recognize_project_elements_route, project_id, len(drawings) + 2)
    summary = report.get("summary") or {}
    progress.step("recognise", "done", f"{summary.get('recognized_drawing_count', '?')} PDF(s) recognised")

    # 5-6: derived text evidence and the level register
    progress.step("evidence", "running")
    evidence = artifacts / "geometry_source_evidence_stage5_3_3_v2.json"
    inputs_file = artifacts / INPUTS_ARTIFACT
    recognised = sorted(d.get("drawing_id") for d in (read_recognition(folder).get("drawings") or []))
    built_from = set(json.loads(inputs_file.read_text("utf-8")).get("drawing_ids", [])) if inputs_file.is_file() else None
    if evidence.is_file() and built_from is None:
        built_from = set(recognised)      # evidence predates this pipeline; take it as covering today's drawings
        ledger_service.save_json_atomic(inputs_file, {"drawing_ids": recognised, "recorded_at": _now()})
    rebuilt_evidence = not evidence.is_file() or not set(recognised) <= (built_from or set())
    if rebuilt_evidence:
        if evidence.is_file():
            shutil.move(str(evidence), str(evidence.with_suffix(f".{datetime.now():%Y%m%d%H%M%S}.bak.json")))
        _run_script(["run_stage5_3_3_source_evidence_project.py", "--project-id", project_id])
        ledger_service.save_json_atomic(inputs_file, {"drawing_ids": recognised, "recorded_at": _now()})
        progress.step("evidence", "done", f"Text evidence read from {len(recognised)} PDF(s)")
    else:
        progress.step("evidence", "skipped", "Already up to date")

    progress.step("levels", "running")
    levels_file = artifacts / "wall_height_association_stage5_3_4.json"
    if rebuilt_evidence or not levels_file.is_file():
        generate_project_height_association(folder, overwrite=True)
    height_report = ledger_service.read_optional_artifact(folder, "wall_height_association_stage5_3_4.json") or {}
    level_count = len(height_report.get("level_register") or [])
    progress.step("levels", "done", f"{level_count} level(s) found; unit {(height_report.get('level_unit_convention') or {}).get('unit') or 'unresolved'}")

    # 7: scale
    progress.step("scale", "running")
    recognition = read_recognition(folder)
    index = sheet_index(folder)
    calibration_report = ledger_service.read_optional_artifact(folder, ledger_service.CALIBRATION_ARTIFACT_NAME)
    plans: list[dict[str, Any]] = []
    for drawing in recognition.get("drawings") or []:
        drawing_id = drawing.get("drawing_id")
        title = titles.get(drawing_id, drawing.get("filename"))
        if not is_floor_plan(title):
            continue
        page = (drawing.get("pages") or [{}])[0]
        size = ledger_service._page_size_pdf(page)
        plan = {"drawing_id": drawing_id, "title": title, "page_size": size, "notes": []}
        current = ledger_service._page_calibration(calibration_report, drawing_id, 1)
        if current.get("status") == "calibrated":
            plan["scale"] = float(current["millimetres_per_pdf_point"])
            plan["notes"].append("scale kept (already calibrated)")
        else:
            denominator = printed_scale(index.get(drawing_id))
            if denominator and size:
                width = float(size[0])
                record = scale_calibration.calibrate_page(
                    folder, project_id, drawing_id=drawing_id, page_number=1,
                    point_a_pdf=[0.0, 0.0], point_b_pdf=[width, 0.0],
                    known_distance=round(width * millimetres_per_point(denominator), 3), unit="mm",
                    source_reference=(f"Printed scale 1:{denominator} at true paper size ({width:.0f} pt sheet width). "
                                      f"{AUTO_REVIEWER}; requires verification."),
                )
                plan["scale"] = float(record.get("millimetres_per_pdf_point") or (record.get("calibration") or {}).get("millimetres_per_pdf_point"))
                plan["notes"].append(f"scale 1:{denominator} from the sheet")
            else:
                plan["notes"].append("no single printed scale - not calibrated")
        plans.append(plan)
    progress.status["plans"] = plans
    calibrated = [p for p in plans if p.get("scale")]
    progress.step("scale", "done", f"{len(plans)} floor plan(s) found, {len(calibrated)} with a scale")

    # 8: placement on a shared grid reference
    progress.step("place", "running")
    ledger = ledger_service.load_ledger(folder, project_id)
    grids = {}
    for plan in calibrated:
        grid = column_detection.grid_lines(index.get(plan["drawing_id"]), plan["page_size"] or (0.0, 0.0))
        if grid["vertical"] and grid["horizontal"]:
            grids[plan["drawing_id"]] = grid
        else:
            plan["notes"].append("no grid found - not placed")
    reference = shared_grid_reference(grids)
    placed = 0
    for plan in calibrated:
        existing = ledger_service.find_active_placement(ledger, plan["drawing_id"], 1)
        if existing:
            plan["placement_id"] = existing["placement_id"]
            plan["storey_height_mm"] = existing.get("storey_height_mm")
            plan["notes"].append("placement kept")
            placed += 1
            continue
        if plan["drawing_id"] not in grids or not reference:
            continue
        options = ledger_service.placement_options(height_report=height_report, ledger=ledger,
                                                   drawing_id=plan["drawing_id"], page_number=1)
        base, top = options.get("suggested_base_level_key"), options.get("suggested_top_level_key")
        levels = {item["level_key"]: item for item in height_report.get("level_register") or []}
        if not base or not top or base not in levels or top not in levels:
            plan["notes"].append("no base and top level on this plan - not placed")
            continue
        storey = float(levels[top]["elevation_mm"]) - float(levels[base]["elevation_mm"])
        if not MIN_STOREY_MM <= storey <= MAX_STOREY_MM:
            plan["notes"].append(f"suggested storey height {storey:.0f} mm is implausible - not placed")
            continue
        point = grid_point(grids[plan["drawing_id"]], *reference)
        try:
            record = plan_placement_router.create_placement(project_id, plan_placement_router.PlacementRequest(
                drawing_id=plan["drawing_id"], page_number=1, base_level_key=base, top_level_key=top,
                reference_point_pdf=[point[0], point[1]], reference_label=f"Grid {reference[0]}/{reference[1]}",
                reviewer=AUTO_REVIEWER,
                justification=(f"Automatic: base {base} and top {top} suggested from this plan's level annotations; "
                               f"reference grid {reference[0]}/{reference[1]} shared by every placed plan."),
            ))["placement"]
        except Exception as exc:   # an HTTPException from the route carries the reason
            plan["notes"].append(f"placement refused: {getattr(exc, 'detail', exc)}")
            continue
        ledger = ledger_service.load_ledger(folder, project_id)
        plan["placement_id"] = record["placement_id"]
        plan["storey_height_mm"] = record.get("storey_height_mm")
        plan["notes"].append(f"placed {base} to {top}")
        placed += 1
    progress.save()
    progress.step("place", "done", f"{placed} plan(s) placed" + (f" on grid {reference[0]}/{reference[1]}" if reference else ""))

    # 9: detect and confirm columns and walls at storey height
    progress.step("confirm", "running")
    totals = {"columns": 0, "hatch_walls": 0, "line_pair_walls": 0}
    for plan in calibrated:
        if not plan.get("placement_id") or not plan.get("storey_height_mm"):
            continue
        drawing_id = plan["drawing_id"]
        columns = column_router.get_page_columns(project_id, drawing_id=drawing_id, page_number=1)
        new_columns = [c["column_id"] for c in columns["candidates"] if c["confirmation"]["status"] == "unconfirmed"]
        if new_columns:
            column_router.confirm_columns(project_id, column_router.ColumnConfirmRequest(
                drawing_id=drawing_id, page_number=1, column_ids=new_columns, reviewer=AUTO_REVIEWER,
                justification="Automatic: column outlines on grid intersections, full storey height."))
            totals["columns"] += len(new_columns)
        walls = column_router.get_page_hatch_walls(project_id, drawing_id=drawing_id, page_number=1)
        new_walls = [w["wall_id"] for w in walls["candidates"]
                     if w.get("suggested") and w["confirmation"]["status"] == "unconfirmed"]
        if new_walls:
            column_router.confirm_hatch_walls(project_id, column_router.HatchWallConfirmRequest(
                drawing_id=drawing_id, page_number=1, element_ids=new_walls, reviewer=AUTO_REVIEWER,
                justification="Automatic: suggested walls whose hatch matches the sheet's wall legend, full storey height."))
            totals["hatch_walls"] += len(new_walls)
        if not walls["candidates"]:
            # No wall hatch legend on this sheet: fall back to the suggested line-pair walls.
            view = height_verification_router.get_page_wall_candidates(project_id, drawing_id=drawing_id, page_number=1)
            ids = [c["element_id"] for c in view.get("candidates") or []
                   if c.get("suggested") and (c.get("verification") or {}).get("status", "unverified") == "unverified"]
            if ids:
                try:
                    height_verification_router.create_height_verification(project_id, height_verification_router.HeightVerificationRequest(
                        height_source="storey_height", drawing_id=drawing_id, page_number=1, element_ids=ids[:500],
                        decision="confirmed", reviewer=AUTO_REVIEWER,
                        justification="Automatic: suggested wall line pairs (no hatch legend on this sheet), full storey height."))
                    totals["line_pair_walls"] += len(ids[:500])
                except Exception as exc:
                    plan["notes"].append(f"line-pair walls not confirmed: {getattr(exc, 'detail', exc)}")
    progress.save()
    progress.step("confirm", "done", f"{totals['columns']} column(s), {totals['hatch_walls'] + totals['line_pair_walls']} wall(s) added")

    # 10: generate
    progress.step("generate", "running")
    ledger = ledger_service.load_ledger(folder, project_id)
    for placement in ledger.get("plan_placements", []):
        if placement.get("status") != "active":
            continue
        try:
            generation.generate_page_model(folder, project_id, placement["drawing_id"], placement["page_number"])
        except Exception as exc:
            for plan in plans:
                if plan["drawing_id"] == placement["drawing_id"]:
                    plan["notes"].append(f"not generated: {exc}")
    model = build_building_model(folder, project_id)
    progress.status["summary"] = {**model["summary"], "slabs": len(model.get("slabs") or []),
                                  "automatic_confirmations": totals}
    if model["elements"]:
        progress.step("generate", "done", f"{model['summary']['walls_served']} element(s) in {model['summary']['plans_in_model']} storey(s)")
    else:
        progress.step("generate", "failed", nothing_built_reason(plans, len(drawings)))
    return progress.status


def nothing_built_reason(plans: list[dict[str, Any]], drawing_count: int) -> str:
    """Why a PDF project produced no model, in one sentence the user can act on."""
    if not plans:
        return (f"None of the {drawing_count} PDF(s) is a floor plan with vector lines (a scanned drawing has none, and a "
                "floor plan's title names it, e.g. GROUND FLOOR PLAN). Upload vector PDFs exported from CAD or Revit, "
                "or the DWG / IFC files.")
    first = next((p for p in plans if p.get("notes")), plans[0])
    reason = first["notes"][-1] if first.get("notes") else "not placed"
    return (f"No floor plan could be placed ({first.get('title')}: {reason}). Each plan needs a printed scale, grid "
            "lines and its slab levels; the DWG or IFC files give the model directly.")


def start(folder: Path, project_id: str) -> dict[str, Any]:
    """Run in a background thread; returns the initial status. Raises RuntimeError if already running."""
    with _RUNNING_LOCK:
        if project_id in _RUNNING:
            raise RuntimeError("The automatic model is already being generated for this project.")
        _RUNNING.add(project_id)
    progress = Progress(folder, project_id)

    def work():
        try:
            run_auto_model(folder, project_id, progress)
            progress.status["state"] = "done"
        except Exception as exc:
            progress.status["state"] = "failed"
            progress.status["error"] = str(getattr(exc, "detail", exc))
            progress.status["traceback"] = traceback.format_exc()[-2000:]
            for step in progress.status["steps"]:
                if step["status"] == "running":
                    step["status"] = "failed"
        finally:
            progress.status["finished_at"] = _now()
            with _RUNNING_LOCK:
                _RUNNING.discard(project_id)
            try:
                progress.save(final=True)
            except OSError:
                pass          # the in-memory status still reports the result

    threading.Thread(target=work, name=f"auto-model-{project_id}", daemon=True).start()
    return progress.status
