from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from app.services.drawing_geometry_extractor import (
    fitz,
    _iter_primitive_batches,
    _sha256_file,
)

BATCH_SIZE = 10000
INDEX_STRIDE = 1000

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "data" / "projects"

# Set from --project-id in main(); there is no default project.
PROJECT = DRAWINGS = ARTIFACTS = METADATA = OUTPUT = None


def _configure(project_id: str) -> None:
    global PROJECT, DRAWINGS, ARTIFACTS, METADATA, OUTPUT
    PROJECT = PROJECTS_DIR / str(UUID(project_id))
    if not PROJECT.is_dir():
        raise SystemExit(f"Project folder not found: {PROJECT}")
    DRAWINGS = PROJECT / "drawings"
    ARTIFACTS = PROJECT / "artifacts"
    METADATA = ARTIFACTS / "drawing_geometry_stage5_metadata_index.json"
    OUTPUT = ARTIFACTS / "primitive_sidecars"


def write_page(drawing, page_meta):
    drawing_id = drawing["drawing_id"]
    page_number = int(page_meta["page_number"])
    expected = int(page_meta["vector_primitive_count"])

    source_name = drawing["source"]["stored_file_name"]
    expected_pdf_hash = drawing.get("source_sha256") or (drawing.get("source") or {}).get("sha256")

    pdf_path = DRAWINGS / source_name

    if not pdf_path.is_file():
        raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

    actual_pdf_hash = _sha256_file(pdf_path)

    if actual_pdf_hash != expected_pdf_hash:
        raise ValueError(
            f"PDF SHA-256 mismatch: {drawing_id}, page {page_number}"
        )

    stem = f"{drawing_id}_page_{page_number}"
    sidecar_name = stem + ".jsonl"
    manifest_name = stem + "_manifest.json"
    index_name = stem + "_offsets.json"

    sidecar_path = OUTPUT / sidecar_name
    manifest_path = OUTPUT / manifest_name
    index_path = OUTPUT / index_name

    # Verify and reuse an existing complete sidecar.
    if manifest_path.is_file() and sidecar_path.is_file() and index_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = _sha256_file(sidecar_path)

            if (
                old.get("drawing_id") == drawing_id
                and old.get("page_number") == page_number
                and old.get("source_pdf_sha256") == actual_pdf_hash
                and old.get("primitive_count") == expected
                and old.get("sidecar_sha256") == digest
                and old.get("sidecar_size_bytes") == sidecar_path.stat().st_size
            ):
                print(f"REUSED {drawing_id} page {page_number}: {expected}")
                return "reused", expected
        except Exception:
            pass

    if fitz is None:
        raise RuntimeError("PyMuPDF is unavailable in this environment.")

    temp_sidecar = sidecar_path.with_suffix(".jsonl.tmp")
    temp_index = index_path.with_suffix(".json.tmp")
    temp_manifest = manifest_path.with_suffix(".json.tmp")

    offsets = []
    count = 0

    try:
        with fitz.open(pdf_path) as document:
            if page_number < 1 or page_number > len(document):
                raise ValueError(
                    f"Invalid page {page_number}; PDF has {len(document)} pages"
                )

            page = document[page_number - 1]
            paths = page.get_drawings()

            with temp_sidecar.open("wb") as stream:
                for batch in _iter_primitive_batches(
                    paths, page_number, BATCH_SIZE
                ):
                    for primitive in batch:
                        if count % INDEX_STRIDE == 0:
                            offsets.append(stream.tell())

                        line = (
                            json.dumps(
                                primitive,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ) + "\n"
                        ).encode("utf-8")

                        stream.write(line)
                        count += 1

                stream.flush()
                os.fsync(stream.fileno())

        if count != expected:
            raise ValueError(
                f"Primitive count mismatch for {drawing_id} page {page_number}: "
                f"expected {expected}, extracted {count}"
            )

        sidecar_hash = _sha256_file(temp_sidecar)
        sidecar_size = temp_sidecar.stat().st_size

        index_data = {
            "schema_version": "1.0.0",
            "drawing_id": drawing_id,
            "page_number": page_number,
            "stride": INDEX_STRIDE,
            "primitive_count": count,
            "sidecar_sha256": sidecar_hash,
            "offsets": offsets,
        }

        manifest_data = {
            "schema_version": "1.0.0",
            "stage": "5.2.4",
            "drawing_id": drawing_id,
            "page_number": page_number,
            "source_pdf": str(pdf_path),
            "source_pdf_sha256": actual_pdf_hash,
            "primitive_count": count,
            "extraction_batch_size": BATCH_SIZE,
            "primitive_order": "original_path_index_then_item_index",
            "sidecar_format": "jsonl",
            "sidecar_file": sidecar_name,
            "sidecar_sha256": sidecar_hash,
            "coordinate_system": "original_pdf_page_points",
            "calibration_applied": False,
            "geometry_semantics_verified": False,
            "sidecar_size_bytes": sidecar_size,
            "index_file": index_name,
            "index_stride": INDEX_STRIDE,
            "index_offset_count": len(offsets),
        }

        temp_index.write_text(
            json.dumps(index_data, indent=2),
            encoding="utf-8",
        )
        temp_manifest.write_text(
            json.dumps(manifest_data, indent=2),
            encoding="utf-8",
        )

        os.replace(temp_sidecar, sidecar_path)
        os.replace(temp_index, index_path)
        os.replace(temp_manifest, manifest_path)

        print(
            f"CREATED {drawing_id} page {page_number}: "
            f"{count:,} primitives | {sidecar_size:,} bytes | "
            f"{len(offsets)} offsets"
        )

        return "created", count

    except Exception:
        for temporary in (temp_sidecar, temp_index, temp_manifest):
            if temporary.exists():
                temporary.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Write per-page primitive sidecars (python -m app.services.primitive_sidecar_batch)"
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--drawing-id", default=None)
    args = parser.parse_args()
    _configure(args.project_id)

    if not METADATA.is_file():
        raise FileNotFoundError(METADATA)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    drawings = metadata["drawings"]

    if args.drawing_id:
        drawings = [
            d for d in drawings if d["drawing_id"] == args.drawing_id
        ]
        if not drawings:
            raise ValueError("Drawing ID not found in metadata index.")

    if args.limit > 0:
        drawings = drawings[:args.limit]

    created = 0
    reused = 0
    failed = 0
    total = 0

    print(f"Drawings selected: {len(drawings)}")

    for drawing in drawings:
        for page in drawing.get("pages", []):
            try:
                status, count = write_page(drawing, page)
                total += count
                if status == "created":
                    created += 1
                else:
                    reused += 1
            except Exception as exc:
                failed += 1
                print(
                    f"FAILED {drawing.get('drawing_id')} "
                    f"page {page.get('page_number')}: {exc}"
                )

    print("\n=== SIDECAR GENERATION SUMMARY ===")
    print("Pages created:", created)
    print("Pages reused:", reused)
    print("Pages failed:", failed)
    print("Validated primitives:", f"{total:,}")


if __name__ == "__main__":
    main()
