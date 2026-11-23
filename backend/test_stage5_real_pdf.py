
import hashlib
import json
import shutil
from pathlib import Path

from app.services.geometry_pipeline import extract_project_geometry


PROJECT_ID = "5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845"
DRAWING_ID = "011cfd18-d896-4852-90aa-13ee043ecacf"

SOURCE_PDF = (
    Path("data/projects")
    / PROJECT_ID
    / "drawings"
    / f"{DRAWING_ID}.pdf"
)

# Use one consistent variable name throughout the script.
TEST_ROOT = Path(
    "data/validation_stage5/real_pdf_pipeline_test_rerun_20261108"
)

TEST_PDF_NAME = f"{DRAWING_ID}.pdf"
REPORT_PATH = TEST_ROOT / "artifacts" / "drawing_geometry_stage5.json"

EXPECTED_SHA256 = (
    "960f5eb94b56676a4683bff760ae4286"
    "c99eaa1dce5213c06943125259252500"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    # 1. Verify the original source PDF exists.
    if not SOURCE_PDF.is_file():
        raise FileNotFoundError(
            f"Source PDF not found: {SOURCE_PDF}"
        )

    # 2. Never overwrite an existing validation folder.
    if TEST_ROOT.exists():
        raise FileExistsError(
            f"Test folder already exists: {TEST_ROOT}\n"
            "Do not overwrite it. Choose a new test folder."
        )

    # 3. Verify the original PDF hash before copying.
    source_hash = sha256_file(SOURCE_PDF)

    if source_hash != EXPECTED_SHA256:
        raise RuntimeError(
            "Source PDF SHA256 does not match the expected original.\n"
            f"Expected: {EXPECTED_SHA256}\n"
            f"Actual:   {source_hash}"
        )

    # 4. Create an isolated validation project.
    drawings_dir = TEST_ROOT / "drawings"
    drawings_dir.mkdir(parents=True)

    test_pdf = drawings_dir / TEST_PDF_NAME
    shutil.copy2(SOURCE_PDF, test_pdf)

    # 5. Verify the copied PDF is identical.
    copied_hash = sha256_file(test_pdf)

    if copied_hash != source_hash:
        raise RuntimeError(
            "Copied PDF SHA256 verification failed."
        )

    # 6. Build the isolated project metadata.
    project = {
        "id": "stage5-real-pdf-validation",
        "name": "Stage 5 Real PDF Isolated Validation",
        "drawings": [
            {
                "id": DRAWING_ID,
                "filename": (
                    "DSP-DCP-DWG-ARC-1002_BASEMENT FLOOR - "
                    "UPPER LEVEL.pdf"
                ),
                "stored_filename": TEST_PDF_NAME,
            }
        ],
    }

    print("SOURCE PDF SHA256: PASS")
    print("ISOLATED PDF COPY: PASS")
    print("Starting actual Stage 5.0 extraction...")

    # 7. Run extraction only in the isolated test folder.
    result = extract_project_geometry(
        TEST_ROOT,
        project,
        batch_size=1,
    )

    # 8. Verify that the report was generated.
    if not REPORT_PATH.is_file():
        raise RuntimeError(
            f"Stage 5 report was not created: {REPORT_PATH}"
        )

    with REPORT_PATH.open("r", encoding="utf-8") as stream:
        report = json.load(stream)

    drawings = report.get("drawings", [])

    if len(drawings) != 1:
        raise AssertionError(
            f"Expected 1 drawing, found {len(drawings)}"
        )

    drawing = drawings[0]
    pages = drawing.get("pages", [])

    primitive_count = sum(
        len(page.get("primitives", []))
        for page in pages
    )

    text_count = sum(
        len(page.get("text_spans", []))
        for page in pages
    )

    extracted_hash = (
        drawing.get("source_sha256")
        or drawing.get("source", {}).get("sha256")
    )

    # 9. Verify source integrity and extracted geometry.
    if extracted_hash != source_hash:
        raise AssertionError(
            "Report source SHA256 mismatch."
        )

    if primitive_count != 282190:
        raise AssertionError(
            f"Expected 282190 primitives, found {primitive_count}"
        )

    if text_count != 316:
        raise AssertionError(
            f"Expected 316 text spans, found {text_count}"
        )

    # 10. Print final results.
    print("\nREAL PDF PIPELINE RESULTS")
    print("STATUS:", result.get("status"))
    print("PAGE COUNT:", len(pages))
    print("PRIMITIVES:", primitive_count)
    print("TEXT SPANS:", text_count)
    print("SOURCE SHA256: MATCH")
    print("REPORT:", REPORT_PATH)
    print("\nREAL PDF END-TO-END TEST: PASS")


if __name__ == "__main__":
    main()
