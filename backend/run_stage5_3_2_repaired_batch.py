
import json
import hashlib
import ijson

from pathlib import Path
from datetime import datetime, timezone

from app.services.bim_geometry_builder import build_wall_mesh


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"
DRAWING_ID = "011cfd18-d896-4852-90aa-13ee043ecacf"

ARTIFACTS = Path("data/projects") / PROJECT_ID / "artifacts"

REPAIRED = Path(
    "data/validation_stage5_3/drawing_geometry_stage5_repaired_test.json"
)

RECOGNITION = (
    ARTIFACTS / "engineering_element_candidates_stage5_1_6.json"
)

CALIBRATION = (
    ARTIFACTS / "drawing_scale_calibration_stage5_2.json"
)

OUTPUT = Path(
    "data/validation_stage5_3/"
    "drawing_1002_wall_meshes_height_evidence_batch.json"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


print("CHECKING INPUT FILES...")

for path in [REPAIRED, RECOGNITION, CALIBRATION]:
    if not path.is_file():
        raise FileNotFoundError(path)

    print("FOUND:", path)


recognition_data = json.loads(
    RECOGNITION.read_text(encoding="utf-8")
)

recognition_matches = [
    drawing
    for drawing in recognition_data["drawings"]
    if drawing.get("drawing_id") == DRAWING_ID
]

if len(recognition_matches) != 1:
    raise RuntimeError(
        "Expected exactly one matching recognition drawing."
    )

recognition = recognition_matches[0]


calibration_data = json.loads(
    CALIBRATION.read_text(encoding="utf-8")
)

calibration = (
    calibration_data["calibrations"]
    [DRAWING_ID]["1"]
)


if recognition.get("source_sha256") != calibration.get("source_sha256"):
    raise RuntimeError(
        "Recognition/calibration source hash mismatch."
    )

if calibration.get("status") != "calibrated":
    raise RuntimeError("Drawing calibration is not valid.")

if calibration.get("verification_required") is not True:
    raise RuntimeError(
        "Calibration verification flag is missing."
    )

print("RECOGNITION/CALIBRATION HASH MATCH: PASS")
print(
    "CALIBRATION:",
    calibration["millimetres_per_pdf_point"],
    "mm/point",
)


candidates = [
    candidate
    for page in recognition.get("pages", [])
    for candidate in page.get("candidates", [])
    if candidate.get("element_type")
    == "wall_geometry_supported_candidate"
]

print("WALL CANDIDATES:", len(candidates))

if not candidates:
    raise RuntimeError("No wall candidates found.")


print("\nLOADING REPAIRED DRAWING GEOMETRY...")

target = None

with REPAIRED.open("rb") as file:
    for drawing in ijson.items(file, "drawings.item"):
        if drawing.get("drawing_id") == DRAWING_ID:
            target = drawing
            break

if target is None:
    raise RuntimeError(
        "Target drawing missing from repaired geometry."
    )

if target.get("source_sha256") != recognition.get("source_sha256"):
    raise RuntimeError(
        "Repaired geometry source hash mismatch."
    )


pages = {
    int(page.get("page_number", 0)): page
    for page in target.get("pages", [])
}

print("REPAIRED DRAWING FOUND: PASS")


elements = []
rejected = []

print(
    "\nCHECKING DRAWING-DERIVED HEIGHT EVIDENCE "
    "AND BUILDING PROVISIONAL WALL MESHES..."
)


for index, candidate in enumerate(candidates, 1):

    page_number = int(candidate.get("page_number", 0))
    page = pages.get(page_number)

    if page is None:
        rejected.append({
            "candidate_number": index,
            "source": candidate.get("source"),
            "reason": f"Source page {page_number} not found",
        })

        print(
            f"Candidate {index}/{len(candidates)}: "
            "REJECTED - source page missing"
        )
        continue


    height_evidence = candidate.get("height_evidence")

    valid_height_evidence = False

    if isinstance(height_evidence, dict):
        try:
            parsed_height = float(
                height_evidence.get("height_mm")
            )
        except (TypeError, ValueError):
            parsed_height = None

        valid_height_evidence = (
            parsed_height is not None
            and parsed_height > 0
            and height_evidence.get("source_reference")
            and height_evidence.get("status") == "drawing_derived"
        )


    if not valid_height_evidence:
        rejected.append({
            "candidate_number": index,
            "source": candidate.get("source"),
            "reason": (
                "Drawing-derived wall height evidence is missing "
                "or invalid. Candidate retained; mesh generation skipped."
            ),
        })

        print(
            f"Candidate {index}/{len(candidates)}: "
            "REJECTED - valid drawing-derived height evidence missing"
        )
        continue


    try:
        mesh = build_wall_mesh(
            recognition_report=recognition,
            candidate=candidate,
            source_primitives=page.get("primitives", []),
            calibration_record=calibration,
            height_evidence=height_evidence,
        )

        elements.append(mesh)

        print(
            f"Candidate {index}/{len(candidates)}: BUILT",
            mesh["element_id"],
        )

    except Exception as exc:
        rejected.append({
            "candidate_number": index,
            "source": candidate.get("source"),
            "reason": str(exc),
        })

        print(
            f"Candidate {index}/{len(candidates)}: "
            f"REJECTED - {exc}"
        )


# Validate every generated mesh.
for element in elements:

    if element["mesh"]["vertex_count"] != 8:
        raise RuntimeError(
            "Unexpected mesh vertex count."
        )

    if element["mesh"]["face_count"] != 12:
        raise RuntimeError(
            "Unexpected mesh face count."
        )

    if element["mesh"]["closed_manifold"] is not True:
        raise RuntimeError(
            "Non-manifold mesh detected."
        )

    if element["approval_status"] != "not_approved":
        raise RuntimeError(
            "Unexpected approval status."
        )

    if element["verification_required"] is not True:
        raise RuntimeError(
            "Verification requirement was removed."
        )

    if (
        element.get("height_status")
        != "drawing_derived_requires_verification"
    ):
        raise RuntimeError(
            "Generated mesh has an invalid height status."
        )

    if not element.get("provenance", {}).get("height_evidence"):
        raise RuntimeError(
            "Generated mesh is missing height provenance."
        )


has_generated_meshes = len(elements) > 0

all_meshes_closed = (
    has_generated_meshes
    and all(
        element["mesh"]["closed_manifold"] is True
        for element in elements
    )
)

all_meshes_provisional = (
    has_generated_meshes
    and all(
        element["geometry_status"] == "provisional_mesh"
        for element in elements
    )
)

all_meshes_unapproved = (
    has_generated_meshes
    and all(
        element["approval_status"] == "not_approved"
        for element in elements
    )
)

all_heights_have_evidence = (
    has_generated_meshes
    and all(
        element.get("height_status")
        == "drawing_derived_requires_verification"
        and bool(
            element.get("provenance", {}).get("height_evidence")
        )
        for element in elements
    )
)


report = {
    "schema_version": "0.2.0",
    "stage": "5.3.2",
    "project_id": PROJECT_ID,
    "drawing_id": DRAWING_ID,
    "generated_at": datetime.now(timezone.utc).isoformat(),

    "source_geometry": str(REPAIRED),
    "source_geometry_sha256": sha256(REPAIRED),

    "recognition_source_sha256": recognition["source_sha256"],
    "calibration_source_sha256": calibration["source_sha256"],

    "summary": {
        "candidate_count": len(candidates),
        "generated_mesh_count": len(elements),
        "rejected_candidate_count": len(rejected),

        "all_meshes_closed_manifold": all_meshes_closed,
        "all_meshes_provisional": all_meshes_provisional,
        "all_meshes_unapproved": all_meshes_unapproved,

        "all_generated_heights_have_drawing_evidence": (
            all_heights_have_evidence
        ),

        "mesh_generation_status": (
            "completed_with_meshes"
            if has_generated_meshes
            else "blocked_missing_verified_wall_heights"
        ),
    },

    "elements": elements,
    "rejected": rejected,

    "limitations": [
        "Wall candidates are geometric hypotheses, not confirmed physical walls.",
        "Wall mesh generation is skipped when drawing-derived height evidence is missing.",
        "Drawing-derived height evidence remains subject to human verification.",
        "Coordinates remain page-local and are not shared building coordinates.",
        "Meshes are not native Revit elements.",
        "Human verification and approval are required.",
    ],
}


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

if OUTPUT.exists():
    raise FileExistsError(
        f"Refusing to overwrite existing output: {OUTPUT}"
    )

OUTPUT.write_text(
    json.dumps(report, indent=2),
    encoding="utf-8",
)


print("\n========== STAGE 5.3.2 RESULT ==========")

print("CANDIDATES:", len(candidates))
print("GENERATED MESHES:", len(elements))
print("REJECTED:", len(rejected))

print("ALL GENERATED MESHES CLOSED:", all_meshes_closed)
print("ALL PROVISIONAL:", all_meshes_provisional)
print("ALL UNAPPROVED:", all_meshes_unapproved)

print(
    "ALL GENERATED HEIGHTS HAVE DRAWING EVIDENCE:",
    all_heights_have_evidence,
)

print(
    "MESH GENERATION STATUS:",
    report["summary"]["mesh_generation_status"],
)

print("OUTPUT:", OUTPUT)
print("OUTPUT SIZE:", OUTPUT.stat().st_size)


batch_pass = (
    has_generated_meshes
    and all_meshes_closed
    and all_meshes_provisional
    and all_meshes_unapproved
    and all_heights_have_evidence
)

print(
    "\nBATCH RESULT:",
    "PASS" if batch_pass else "NEEDS INVESTIGATION",
)
