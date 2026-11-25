from pathlib import Path

from uuid import uuid4, UUID

from datetime import datetime, timezone

import json

import math

import re

from collections import Counter



from fastapi import FastAPI, UploadFile, File, HTTPException

from fastapi.responses import FileResponse, JSONResponse

from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel, Field

from app.services.coordination_engine import coordinate_project

from app.services.pdf_page_intelligence import extract_pdf_pages

from app.services.geometry_pipeline import extract_project_geometry

from app.services.engineering_element_recognition import analyze_project_elements

from app.services.scale_calibration import (

    build_calibration_preview, calibrate_page, get_calibration_report,

    transform_primitive_for_page, transform_primitive_batch,

)
from app.services.bim_geometry_preview import router as bim_geometry_preview_router
from app.services.phase6_scene import router as phase6_scene_router
from app.services.height_association_router import router as height_association_router
from app.services.height_verification_router import router as height_verification_router





# Storage stays inside backend/data. Do not delete this folder:

# it contains the uploaded project PDFs and project metadata.

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"

PROJECTS_DIR = DATA_DIR / "projects"

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)



app = FastAPI(

    title="Innoclique 2.0 API",

    version="0.4.3",

    description="Universal PDF-first engineering drawing intelligence and project coordination."

)

app.include_router(bim_geometry_preview_router)
app.include_router(phase6_scene_router)
app.include_router(height_association_router)
app.include_router(height_verification_router)

app.add_middleware(

    CORSMiddleware,

    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

)





class ProjectCreate(BaseModel):

    name: str = Field(min_length=2, max_length=120)

    project_type: str = Field(default="building", max_length=60)

    description: str = Field(default="", max_length=1000)





def utc_now():

    return datetime.now(timezone.utc).isoformat()





def project_dir(project_id: str) -> Path:

    try:

        safe_id = str(UUID(project_id))

    except (ValueError, TypeError, AttributeError):

        raise HTTPException(status_code=404, detail="Project not found")



    path = PROJECTS_DIR / safe_id

    if not path.is_dir():

        raise HTTPException(status_code=404, detail="Project not found")

    return path





def read_json(path: Path, default):

    if not path.exists():

        return default

    try:

        return json.loads(path.read_text(encoding="utf-8"))

    except (json.JSONDecodeError, OSError):

        return default





def write_json(path: Path, data):

    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")







def classify_drawing(filename: str, extracted_text: str):

    """

    Stage 3 rule-based drawing classifier.

    Uses filename/title signals first, then corroborates with extracted text.

    Returns category, discipline, confidence and human-readable evidence.

    """

    name = Path(filename).stem.lower().replace("_", " ")

    text = re.sub(r"\s+", " ", extracted_text[:24000]).lower()

    combined = f"{name} {text}"



    # Ordered, specific drawing-type rules. First strong filename match wins.

    filename_rules = [

        ("perspective", [r"\bperspective(?:s)?\b", r"\b3d view\b", r"\brender(?:ing)?\b"]),

        ("cover", [r"\bcover(?: page)?\b", r"\btitle page\b", r"\bindex sheet\b"]),

        ("area_calculation", [r"\barea calculation\b", r"\barea statement\b", r"\barea schedule\b"]),

        ("landscape", [r"\blandscape\b", r"\bplanting plan\b", r"\birrigation layout\b"]),

        ("signage", [r"\bsignage\b", r"\bway[\s-]?finding\b"]),

        ("site_plan", [r"\bsite plan\b", r"\bsite layout\b", r"\bsetting.?out plan\b",

                       r"\bhardscape\b", r"\bgrading plan\b", r"\broad layout\b"]),

        ("structural", [r"\bstructural\b", r"\bfoundation\b", r"\bfooting\b",

                        r"\bcolumn layout\b", r"\bbeam layout\b", r"\breinforcement\b",

                        r"\bslab layout\b", r"\bformwork\b", r"\bbar bending\b"]),

        ("mep_electrical", [r"\belectrical\b", r"\blighting\b", r"\bsingle.?line\b",

                            r"\bcable tray\b", r"\bcable routing\b", r"\bswitchgear\b",

                            r"\bpower layout\b", r"\bschematic diagram\b"]),

        ("fire_safety", [r"\bfire alarm\b", r"\bfire.?fighting\b", r"\bsprinkler\b",

                         r"\bfire protection\b", r"\bfire hydrant\b", r"\bsmoke detection\b"]),

        ("mep_plumbing", [r"\bplumbing\b", r"\bwater supply\b", r"\bdrainage\b",

                          r"\bsanitary\b", r"\bsewer\b", r"\bstorm water\b",

                          r"\bcondensate drain\b"]),

        ("mep_mechanical", [r"\bhvac\b", r"\bmechanical\b", r"\bduct\b",

                            r"\bventilation\b", r"\bchiller\b", r"\bcooling tower\b",

                            r"\bpipework\b", r"\bpiping layout\b"]),

        ("ceiling_plan", [r"\breflected ceiling\b", r"\brcp\b", r"\bceiling layout\b"]),

        ("finishes_layout", [r"\bfloor finishes\b", r"\bwall finishes\b",

                             r"\bfinishes(?: layout| plan| schedule)?\b"]),

        ("schedule", [r"\bschedule\b", r"\bdoor.?window schedule\b"]),

        ("section", [r"\bsections?\b", r"\bwall section\b", r"\bthermal section\b"]),

        ("elevation", [r"\belevations?\b"]),

        ("room_detail", [r"\bpartial drawings?\b", r"\broom details?\b",

                         r"\breception\b", r"\bmeeting room\b", r"\boffice room\b",

                         r"\btoilet details?\b", r"\bpantry details?\b"]),

        ("detail", [r"\bdetails?\b", r"\bmock.?up\b", r"\bcladding\b",

                    r"\benvelope\b", r"\bstaircase details?\b", r"\blift details?\b",

                    r"\bramp details?\b", r"\bdoor details?\b", r"\bwindow details?\b"]),

        ("floor_plan", [r"\bfloor plan\b", r"\blevel plan\b", r"\bbasement plan\b",

                        r"\bbasement floor\b", r"\bground floor\b", r"\broof floor\b",

                        r"\bmezzanine plan\b", r"\broof plan\b"]),

    ]



    category = "unclassified"

    evidence = []

    confidence = 0.25



    for candidate, patterns in filename_rules:

        hits = [p for p in patterns if re.search(p, name, re.I)]

        if hits:

            category = candidate

            evidence.append("filename:" + hits[0].replace(r"\b", "").replace("\\\\", ""))

            confidence = 0.90

            break



    # If the filename did not identify a type, use the first-page/title text.

    if category == "unclassified":

        text_rules = [

            ("perspective", [r"\bperspective view\b", r"\b3d perspective\b"]),

            ("landscape", [r"\blandscape layout\b", r"\bplanting layout\b"]),

            ("site_plan", [r"\bsite plan\b", r"\bsite layout\b"]),

            ("structural", [r"\bstructural drawing\b", r"\breinforcement detail\b",

                            r"\bcolumn layout\b", r"\bbeam layout\b", r"\bfoundation plan\b"]),

            ("mep_electrical", [r"\bsingle line diagram\b", r"\belectrical layout\b",

                                r"\blighting layout\b", r"\bcable tray layout\b"]),

            ("fire_safety", [r"\bfire alarm layout\b", r"\bfire fighting layout\b",

                             r"\bsprinkler layout\b"]),

            ("mep_plumbing", [r"\bplumbing layout\b", r"\bdrainage layout\b",

                              r"\bwater supply layout\b"]),

            ("mep_mechanical", [r"\bhvac layout\b", r"\bduct layout\b",

                                r"\bmechanical layout\b", r"\bpiping layout\b"]),

            ("ceiling_plan", [r"\breflected ceiling plan\b", r"\brcp plan\b"]),

            ("finishes_layout", [r"\bfinishes layout\b", r"\bfloor finish plan\b"]),

            ("schedule", [r"\bdoor schedule\b", r"\bwindow schedule\b",

                          r"\bfinish(?:es)? schedule\b"]),

            ("section", [r"\bsection\s+[a-z0-9-]+\b", r"\bwall section\b"]),

            ("elevation", [r"\belevation\s+[a-z0-9& -]+\b"]),

            ("floor_plan", [r"\bfloor plan\b", r"\bground floor plan\b",

                            r"\bbasement floor plan\b"]),

            ("detail", [r"\bdetail\s+[a-z0-9-]+\b", r"\btypical detail\b"]),

        ]

        for candidate, patterns in text_rules:

            hit = next((p for p in patterns if re.search(p, text, re.I)), None)

            if hit:

                category = candidate

                evidence.append("title_text:" + hit.replace(r"\b", "").replace("\\\\", ""))

                confidence = 0.72

                break



    # Discipline is independent from sheet type.

    discipline_rules = [

        ("structural", [r"\bstructural\b", r"\breinforcement\b", r"\brebar\b",

                        r"\bcolumn\b", r"\bbeam\b", r"\bfooting\b", r"\bfoundation\b"]),

        ("electrical", [r"\belectrical\b", r"\bswitchgear\b", r"\btransformer\b",

                        r"\bpower\b", r"\blighting\b", r"\b11kv\b", r"\blv room\b"]),

        ("mechanical", [r"\bhvac\b", r"\bchiller\b", r"\bcooling tower\b",

                        r"\bair handling\b", r"\bduct\b", r"\bventilation\b"]),

        ("plumbing", [r"\bplumbing\b", r"\bwater supply\b", r"\bdrainage\b",

                      r"\bsump pit\b", r"\bwater tank\b", r"\bpump room\b"]),

        ("fire_protection", [r"\bfire alarm\b", r"\bfire water\b", r"\bsprinkler\b",

                             r"\bfire pump\b", r"\bfire fighting\b"]),

        ("landscape", [r"\blandscape\b", r"\bplanting\b", r"\birrigation\b"]),

        ("architecture", [r"\barchitectural\b", r"\broom\b", r"\bdoor\b", r"\bwindow\b",

                          r"\bwall\b", r"\bfloor finish\b", r"\belevation\b"]),

    ]

    discipline = "general"

    discipline_scores = []

    for label, patterns in discipline_rules:

        score = sum(len(re.findall(p, combined, re.I)) for p in patterns)

        if score:

            discipline_scores.append((score, label))

    if discipline_scores:

        discipline = max(discipline_scores)[1]



    # Drawing series prefix can clarify discipline when title text is sparse.

    code_match = re.search(r"\b[A-Z]{2,6}-[A-Z]{2,6}-DWG-([A-Z]{2,5})-\d{3,5}\b",

                           Path(filename).stem, re.I)

    if code_match:

        series = code_match.group(1).upper()

        series_map = {"STR": "structural", "ELE": "electrical", "EL": "electrical",

                      "MEC": "mechanical", "M": "mechanical", "PLB": "plumbing",

                      "PL": "plumbing", "FIR": "fire_protection", "ARC": "architecture"}

        if series in series_map and discipline == "general":

            discipline = series_map[series]

            evidence.append(f"drawing_series:{series}")



    if category != "unclassified" and discipline != "general" and confidence < 0.95:

        confidence = min(0.95, confidence + 0.05)



    return {

        "category": category,

        "discipline": discipline,

        "classification_confidence": round(confidence, 2),

        "classification_evidence": evidence

    }





def extract_drawing_metadata(filename: str, extracted_text: str):

    """Extract candidate metadata only; values remain unverified until checked."""

    text = re.sub(r"\s+", " ", extracted_text).strip()

    upper = text.upper()



    level_patterns = [

        r"\b(?:B\d+|BASEMENT\s*\d*|GROUND FLOOR|MEZZANINE|ROOF(?: FLOOR)?|"

        r"LEVEL\s*[-+]?\d+(?:\.\d+)?|L\d+|PODIUM)\b"

    ]

    levels = sorted(set(m.group(0).strip() for m in re.finditer(

        level_patterns[0], upper, re.I

    )))[:40]



    elevation_values = sorted(set(re.findall(

        r"\b(?:F\.?F\.?L|S\.?S\.?L|T\.?O\.?S|T\.?R\.?L)\s*[:=]?\s*[-+]?\s*\d+(?:\.\d+)?\s*(?:M|MM)?",

        upper, re.I

    )))[:100]



    grid_refs = sorted(set(re.findall(

        r"\b(?:GRID\s*)?[A-Z]{1,2}\s*[-/]\s*\d{1,3}\b", upper

    )))[:100]



    equipment_tags = sorted(set(re.findall(

        r"\b(?:AHU|FAHU|VAHU|CH|CHWP|CWP|PCHWP|FP|TX|TR|MVSG|LVMDB|SMDB|"

        r"DB|MCC|MCCB|UPS|VFD|CT|CP|SSU|FCU|EF|SF|PUMP|FACP)"

        r"[- ]?[A-Z0-9]{1,}(?:[-/][A-Z0-9]+)*\b",

        upper

    )))[:200]



    room_names = sorted(set(re.findall(

        r"\b(?:PUMP ROOM|FIRE PUMP ROOM|GENERATOR ROOM|ELECTRICAL ROOM|"

        r"TRANSFORMER ROOM|MECHANICAL ROOM|CHILLER HALL|CONTROL ROOM|"

        r"TELECOM ROOM|BATTERY ROOM|OFFICE(?:-\d+)?|MEETING ROOM|"

        r"PRAYER ROOM|PANTRY|TOILET|WORKSHOP|STORE ROOM|SERVER ROOM)\b",

        upper

    )))[:100]



    return {

        "level_candidates": levels,

        "level_elevation_candidates": elevation_values,

        "grid_reference_candidates": grid_refs,

        "equipment_tag_candidates": equipment_tags,

        "room_name_candidates": room_names

    }





def analyze_pdf(pdf_path: Path, filename: str):

    """Extract basic PDF facts and text. Does not create BIM geometry."""

    try:

        import fitz  # PyMuPDF

    except ImportError:

        raise HTTPException(

            status_code=500,

            detail="PyMuPDF is missing. Run: pip install pymupdf"

        )



    try:

        document = fitz.open(pdf_path)

        page_texts = []

        page_data = []



        for page_number, page in enumerate(document, start=1):

            text = page.get_text("text") or ""

            page_texts.append(text)

            rect = page.rect

            page_data.append({

                "page_number": page_number,

                "width_points": round(float(rect.width), 2),

                "height_points": round(float(rect.height), 2),

                "text_characters": len(text.strip())

            })



        full_text = "\n".join(page_texts)

        clean_text = re.sub(r"\s+", " ", full_text).strip()

        dimensions = sorted(set(re.findall(

            r"(?\<!\w)\d+(?:\.\d+)?\s*(?:mm|cm|m|ft|feet|inches|inch|')\b",

            clean_text,

            flags=re.IGNORECASE

        )))

        drawing_numbers = sorted(set(re.findall(

            r"\b[A-Z]{2,6}-[A-Z]{2,6}-DWG-[A-Z]{2,5}-\d{3,5}\b",

            clean_text,

            flags=re.IGNORECASE

        )))

        classification = classify_drawing(filename, full_text)

        metadata = extract_drawing_metadata(filename, full_text)



        result = {

            "filename": filename,

            **classification,

            **metadata,

            "page_count": len(document),

            "pages": page_data,

            "text_extracted": bool(clean_text),

            "extracted_text_characters": len(full_text),

            "dimension_candidates": dimensions[:200],

            "drawing_number_candidates": drawing_numbers[:100],

            "text_preview": clean_text[:2500],

            "analysis_notes": [

                "This is an automated first-pass PDF inspection.",

                "Detected dimensions and drawing numbers are candidates and require engineering verification.",

                "Scanned/image-only pages may need OCR; no OCR is performed by this endpoint.",

                "This analysis does not generate 3D geometry or a BIM model."

            ],

            "analyzed_at": utc_now()

        }

        document.close()

        return result

    except HTTPException:

        raise

    except Exception as exc:

        raise ValueError(f"Could not read PDF: {exc}") from exc





@app.get("/api/health")

def health():

    return {

        "status": "ok",

        "service": "innoclique-api",

        "version": "0.4.3"

    }





@app.post("/api/projects", status_code=201)

def create_project(payload: ProjectCreate):

    project_id = str(uuid4())

    folder = PROJECTS_DIR / project_id

    (folder / "drawings").mkdir(parents=True)

    (folder / "artifacts").mkdir()

    project = {

        "id": project_id,

        "name": payload.name,

        "project_type": payload.project_type,

        "description": payload.description,

        "created_at": utc_now(),

        "status": "created",

        "drawings": []

    }

    write_json(folder / "project.json", project)

    return project





@app.get("/api/projects")

def list_projects():

    result = []

    for folder in PROJECTS_DIR.iterdir():

        metadata = folder / "project.json"

        if folder.is_dir() and metadata.exists():

            project = read_json(metadata, {})

            if project:

                result.append(project)

    return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)





@app.post("/api/projects/{project_id}/drawings", status_code=201)

async def upload_drawings(project_id: str, files: list[UploadFile] = File(...)):

    folder = project_dir(project_id)

    drawing_dir = folder / "drawings"

    drawing_dir.mkdir(parents=True, exist_ok=True)

    project_file = folder / "project.json"

    project = read_json(project_file, {})

    accepted = []

    rejected = []



    if not files:

        raise HTTPException(status_code=400, detail="Upload at least one PDF.")

    if len(files) > 100:

        raise HTTPException(

            status_code=413,

            detail="Maximum 100 PDFs per upload batch. Upload another batch to continue; projects have no fixed PDF-count limit."

        )



    for upload in files:

        filename = Path(upload.filename or "").name

        if not filename.lower().endswith(".pdf"):

            rejected.append({"filename": filename, "reason": "Only PDF drawings are accepted."})

            await upload.close()

            continue



        drawing_id = str(uuid4())

        destination = drawing_dir / f"{drawing_id}.pdf"

        size = 0



        try:

            with destination.open("wb") as output:

                while chunk := await upload.read(1024 * 1024):

                    size += len(chunk)

                    if size > 100 * 1024 * 1024:

                        raise ValueError("File exceeds 100 MB limit.")

                    output.write(chunk)



            if size == 0:

                raise ValueError("Empty file.")



            with destination.open("rb") as stream:

                if not stream.read(5).startswith(b"%PDF-"):

                    raise ValueError("File does not have a valid PDF signature.")



            record = {

                "id": drawing_id,

                "filename": filename,

                "stored_filename": destination.name,

                "size_bytes": size,

                "uploaded_at": utc_now(),

                "processing_status": "uploaded",

                "analysis_status": "not_started"

            }

            project.setdefault("drawings", []).append(record)

            accepted.append(record)



        except ValueError as exc:

            destination.unlink(missing_ok=True)

            rejected.append({"filename": filename, "reason": str(exc)})

        finally:

            await upload.close()



    write_json(project_file, project)

    if not accepted and rejected:

        raise HTTPException(status_code=400, detail={"accepted": [], "rejected": rejected})



    return {

        "project_id": project_id,

        "accepted": accepted,

        "rejected": rejected,

        "total_drawings_in_project": len(project.get("drawings", []))

    }





@app.get("/api/projects/{project_id}/drawings")

def list_drawings(project_id: str):

    folder = project_dir(project_id)

    project = read_json(folder / "project.json", {})

    return {

        "project_id": project_id,

        "drawings": project.get("drawings", []),

        "total": len(project.get("drawings", []))

    }





@app.post("/api/projects/{project_id}/analyze")

def analyze_project_drawings(project_id: str):

    """Analyze every uploaded PDF in this project and save a JSON report."""

    folder = project_dir(project_id)

    project_file = folder / "project.json"

    project = read_json(project_file, {})

    drawings = project.get("drawings", [])



    if not drawings:

        raise HTTPException(status_code=400, detail="This project has no uploaded PDFs.")



    drawing_dir = folder / "drawings"

    analysis_results = []

    failed = []



    for drawing in drawings:

        stored_name = drawing.get("stored_filename")

        if not stored_name:

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": drawing.get("filename"),

                "reason": "Stored PDF filename is missing from project metadata."

            })

            continue



        pdf_path = drawing_dir / Path(stored_name).name

        if not pdf_path.is_file():

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": drawing.get("filename"),

                "reason": "PDF file was not found in this project's drawings folder."

            })

            drawing["analysis_status"] = "failed"

            continue



        try:

            result = analyze_pdf(pdf_path, drawing.get("filename", stored_name))

            result["drawing_id"] = drawing.get("id")

            analysis_results.append(result)

            drawing["analysis_status"] = "completed"

            drawing["processing_status"] = "analyzed"

            drawing["detected_category"] = result["category"]

        except HTTPException:

            raise

        except Exception as exc:

            drawing["analysis_status"] = "failed"

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": drawing.get("filename"),

                "reason": str(exc)

            })



    category_summary = dict(sorted(Counter(

        item.get("category", "unclassified") for item in analysis_results

    ).items()))

    discipline_summary = dict(sorted(Counter(

        item.get("discipline", "general") for item in analysis_results

    ).items()))



    report = {

        "classification_summary": {

            "by_category": category_summary,

            "by_discipline": discipline_summary

        },

        "project_id": project_id,

        "project_name": project.get("name"),

        "analyzed_at": utc_now(),

        "total_drawings": len(drawings),

        "analyzed_count": len(analysis_results),

        "failed_count": len(failed),

        "results": analysis_results,

        "failed": failed

    }



    report_path = folder / "artifacts" / "drawing_intelligence.json"

    write_json(report_path, report)

    project["status"] = "analysis_completed" if not failed else "analysis_partial"

    write_json(project_file, project)



    return {

        "message": "Stage 3 engineering drawing intelligence finished.",

        "project_id": project_id,

        "total_drawings": len(drawings),

        "analyzed_count": len(analysis_results),

        "failed_count": len(failed),

        "report_file": str(report_path),

        "classification_summary": {

            "by_category": category_summary,

            "by_discipline": discipline_summary

        },

        "results": analysis_results,

        "failed": failed

    }





@app.post("/api/projects/{project_id}/coordinate")

def coordinate_project_drawings(project_id: str):

    """

    Stage 4.6: preserve drawing-register membership and detect review-required technical-reference candidates.

    The original PDFs and Stage 3 drawing_intelligence.json remain untouched.

    """

    folder = project_dir(project_id)

    project = read_json(folder / "project.json", {})

    drawing_records = project.get("drawings", [])



    if not drawing_records:

        raise HTTPException(status_code=400, detail="This project has no uploaded PDFs.")



    drawing_dir = folder / "drawings"

    records_for_coordination = []

    failed = []



    try:

        import fitz

    except ImportError:

        raise HTTPException(status_code=500, detail="PyMuPDF is missing. Run: pip install pymupdf")



    for drawing in drawing_records:

        stored_name = drawing.get("stored_filename")

        original_name = drawing.get("filename", stored_name or "unknown.pdf")

        if not stored_name:

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": original_name,

                "reason": "Stored PDF filename is missing from project metadata."

            })

            continue



        pdf_path = drawing_dir / Path(stored_name).name

        if not pdf_path.is_file():

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": original_name,

                "reason": "PDF file was not found in this project's drawings folder."

            })

            continue



        try:

            page_intelligence = extract_pdf_pages(pdf_path, enable_ocr=True)

            full_text = page_intelligence["text"]

            records_for_coordination.append({

                "drawing_id": drawing.get("id"),

                "id": drawing.get("id"),

                "filename": original_name,

                "text": full_text,

                "text_preview": full_text[:5000],

                "page_count": page_intelligence["page_count"],

                "pages": page_intelligence["pages"],

                "native_text_page_count": page_intelligence["native_text_page_count"],

                "ocr_page_count": page_intelligence["ocr_page_count"],

                "image_only_or_unreadable_page_count": page_intelligence["image_only_or_unreadable_page_count"],

                "ocr_available": page_intelligence["ocr_available"],

                "ocr_error": page_intelligence["ocr_error"],

                "extraction_mode": page_intelligence["extraction_mode"]

            })

        except Exception as exc:

            failed.append({

                "drawing_id": drawing.get("id"),

                "filename": original_name,

                "reason": f"Could not extract PDF text: {exc}"

            })



    if not records_for_coordination:

        raise HTTPException(

            status_code=422,

            detail={"message": "No PDFs could be read for coordination.", "failed": failed}

        )



    try:

        intelligence = coordinate_project(project, records_for_coordination)

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 4.6 coordination failed: {exc}") from exc



    intelligence["failed_drawings"] = failed

    intelligence["analyzed_at"] = utc_now()

    intelligence["analysis_scope"] = (

        "Project-specific evidence from uploaded PDFs. Filename and text matches are candidates, "

        "not verified engineering facts."

    )



    report_path = folder / "artifacts" / "project_intelligence.json"

    write_json(report_path, intelligence)



    return {

        "message": "Stage 4.6 drawing intelligence and cross-drawing reference analysis completed.",

        "project_id": project_id,

        "project_name": project.get("name"),

        "total_drawings": len(drawing_records),

        "coordinated_drawings": len(records_for_coordination),

        "failed_count": len(failed),

        "inferred_project_type": intelligence["project_profile"]["inferred_project_type"],

        "sheet_type_summary": intelligence["sheet_type_summary"],

        "discipline_summary": intelligence["discipline_summary"],

        "relationship_count": len(intelligence["relationship_graph"]["relationships"]),

        "report_file": str(report_path),

        "quality_notes": intelligence["quality_notes"],

        "extraction_summary": intelligence.get("extraction_summary", {})

    }





@app.get("/api/projects/{project_id}/intelligence")

def get_project_intelligence(project_id: str):

    """Return the Stage 4 project intelligence report."""

    folder = project_dir(project_id)

    report_path = folder / "artifacts" / "project_intelligence.json"

    if not report_path.exists():

        raise HTTPException(

            status_code=404,

            detail="No Stage 4 intelligence report exists yet. Run POST /api/projects/{project_id}/coordinate first."

        )

    return read_json(report_path, {})





@app.get("/api/projects/{project_id}/analysis")

def get_project_analysis(project_id: str):

    folder = project_dir(project_id)

    report_path = folder / "artifacts" / "drawing_intelligence.json"

    if not report_path.exists():

        raise HTTPException(

            status_code=404,

            detail="No drawing analysis report exists yet. Run POST /api/projects/{project_id}/analyze first."

        )

    return read_json(report_path, {})





@app.post("/api/projects/{project_id}/geometry/extract")

def extract_project_geometry_route(

    project_id: str,

    filename: str | None = None,

    batch_size: int = 1,

):

    """

    Stage 5.0: extract source-grounded PDF geometry incrementally.



    Default: process one next pending/changed PDF per request.

    Optional filename: process only the exact matching project PDF.

    """

    folder = project_dir(project_id)

    project = read_json(folder / "project.json", {})

    drawings = project.get("drawings", [])

    if not drawings:

        raise HTTPException(status_code=400, detail="This project has no uploaded PDFs.")



    if not 1 <= batch_size <= 5:

        raise HTTPException(status_code=422, detail="batch_size must be between 1 and 5.")



    if filename:

        matches = [

            item for item in drawings

            if str(item.get("filename", "")).casefold() == filename.strip().casefold()

        ]

        if not matches:

            raise HTTPException(

                status_code=404,

                detail=f"No project PDF exactly matches filename: {filename}",

            )

        if len(matches) > 1:

            raise HTTPException(

                status_code=409,

                detail=f"More than one project PDF matches filename: {filename}. Use a unique filename.",

            )



    try:

        report = extract_project_geometry(

            folder,

            project,

            batch_size=batch_size,

            filename=filename,

        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 5.0 geometry extraction failed: {exc}") from exc



    summary = report.get("summary", {})

    return {

        "message": (

            "Stage 5.0 extraction request finished. "

            "The report shows whether more PDFs remain."

        ),

        "project_id": project_id,

        "status": report.get("status"),

        "summary": summary,

        "processing": report.get("processing", {}),

        "failed": report.get("failed", []),

        "report_file": report.get("report_file"),

        "limitations": report.get("limitations", []),

    }





@app.get("/api/projects/{project_id}/geometry")

def get_project_geometry(project_id: str):

    """Stream the latest Stage 5.0 geometry artifact without loading it into RAM."""

    folder = project_dir(project_id)

    report_path = folder / "artifacts" / "drawing_geometry_stage5.json"



    if not report_path.is_file():

        raise HTTPException(

            status_code=404,

            detail="No Stage 5.0 geometry report exists yet. Run POST /api/projects/{project_id}/geometry/extract first.",

        )



    return FileResponse(

        path=report_path,

        media_type="application/json",

        filename=report_path.name,

    )





@app.post("/api/projects/{project_id}/elements/recognize")

def recognize_project_elements_route(

    project_id: str,

    filename: str | None = None,

    batch_size: int = 1,

):

    """

    Stage 5.1: recognize conservative engineering-element candidates from PDFs.



    Default: process the next pending PDF.

    Use batch_size up to 5 to process multiple pending PDFs.

    Optional filename: process one uniquely named PDF.

    This endpoint does not create BIM geometry.

    """

    folder = project_dir(project_id)

    project = read_json(folder / "project.json", {})

    drawings = project.get("drawings", [])



    if not drawings:

        raise HTTPException(status_code=400, detail="This project has no uploaded PDFs.")



    if not 1 <= batch_size <= 5:

        raise HTTPException(status_code=422, detail="batch_size must be between 1 and 5.")



    if filename:

        matches = [

            item for item in drawings

            if str(item.get("filename", "")).casefold() == filename.strip().casefold()

        ]

        if not matches:

            raise HTTPException(

                status_code=404,

                detail=f"No project PDF exactly matches filename: {filename}",

            )

        if len(matches) > 1:

            raise HTTPException(

                status_code=409,

                detail=f"More than one project PDF matches filename: {filename}. Use a unique filename.",

            )



    try:

        report = analyze_project_elements(

            folder,

            project,

            filename=filename,

            batch_size=batch_size,

        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(

            status_code=500,

            detail=f"Stage 5.1 engineering-element recognition failed: {exc}",

        ) from exc



    return {

        "message": (

            "Stage 5.1 recognition request finished. "

            "The report shows whether more PDFs remain."

        ),

        "project_id": project_id,

        "status": report.get("status"),

        "summary": report.get("summary", {}),

        "failed_or_missing": report.get("failed_or_missing", []),

        "report_file": report.get("report_file"),

        "limitations": report.get("limitations", []),

    }





@app.get("/api/projects/{project_id}/elements")

def get_project_element_candidates(project_id: str):

    """Return the latest Stage 5.1 candidate-recognition artifact."""

    folder = project_dir(project_id)

    report_path = folder / "artifacts" / "engineering_element_candidates_stage5_1_6.json"

    if not report_path.is_file():

        raise HTTPException(

            status_code=404,

            detail=(

                "No Stage 5.1 element-recognition report exists yet. "

                "Run POST /api/projects/{project_id}/elements/recognize first."

            ),

        )



    return read_json(report_path, {})





class CoordinateTransformPreviewRequest(BaseModel):

    drawing_id: str = Field(min_length=1, max_length=200)

    page_number: int = Field(ge=1)

    primitive: dict





class CoordinateTransformBatchRequest(BaseModel):

    drawing_id: str = Field(min_length=1, max_length=200)

    page_number: int = Field(ge=1)

    start_index: int = Field(default=0, ge=0)

    batch_size: int = Field(default=1000, ge=1, le=5000)





class ScaleCalibrationRequest(BaseModel):

    drawing_id: str = Field(min_length=1, max_length=200)

    page_number: int = Field(ge=1)

    point_a_pdf: list[float] = Field(min_length=2, max_length=2)

    point_b_pdf: list[float] = Field(min_length=2, max_length=2)

    known_distance: float = Field(gt=0)

    unit: str = Field(min_length=1, max_length=20)

    source_reference: str = Field(min_length=3, max_length=500)





@app.get("/api/projects/{project_id}/scale-calibration/preview")

def get_scale_calibration_preview(project_id: str):

    """Stage 5.2.1: discover dimension and printed-scale evidence from Stage 5.0."""

    folder = project_dir(project_id)

    try:

        return build_calibration_preview(folder, project_id)

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 5.2 preview failed: {exc}") from exc





@app.post("/api/projects/{project_id}/scale-calibration/calibrate")

def calibrate_project_drawing_page(project_id: str, payload: ScaleCalibrationRequest):

    """Stage 5.2.2: calibrate one page using two explicit PDF control points."""

    folder = project_dir(project_id)

    try:

        return calibrate_page(

            folder, project_id,

            drawing_id=payload.drawing_id,

            page_number=payload.page_number,

            point_a_pdf=payload.point_a_pdf,

            point_b_pdf=payload.point_b_pdf,

            known_distance=payload.known_distance,

            unit=payload.unit,

            source_reference=payload.source_reference,

        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 5.2 calibration failed: {exc}") from exc





@app.get("/api/projects/{project_id}/scale-calibration")

def get_project_scale_calibration(project_id: str):

    """Return saved Stage 5.2 calibration records without reprocessing PDFs."""

    folder = project_dir(project_id)

    try:

        return get_calibration_report(folder)

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc







@app.post("/api/projects/{project_id}/scale-calibration/transform-preview")

def preview_coordinate_transform(project_id: str, payload: CoordinateTransformPreviewRequest):

    """Stage 5.2.3: preview one line/bezier/quad in page-local millimetres."""

    folder = project_dir(project_id)

    try:

        return transform_primitive_for_page(

            folder, drawing_id=payload.drawing_id,

            page_number=payload.page_number, primitive=payload.primitive,

        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 5.2 coordinate transform failed: {exc}") from exc





@app.post("/api/projects/{project_id}/scale-calibration/transform-batch")

def batch_coordinate_transform(project_id: str, payload: CoordinateTransformBatchRequest):

    """Stage 5.2.4: transform a bounded, resumable batch from one calibrated page."""

    folder = project_dir(project_id)

    try:

        return transform_primitive_batch(

            folder, drawing_id=payload.drawing_id, page_number=payload.page_number,

            start_index=payload.start_index, batch_size=payload.batch_size,

        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except FileNotFoundError as exc:

        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:

        raise HTTPException(status_code=500, detail=f"Stage 5.2 batch transform failed: {exc}") from exc





@app.get("/api/projects/{project_id}/drawings/{drawing_id}/meshes")

def get_drawing_meshes(project_id: str, drawing_id: str):

    """

    Return a registered Stage 5.3 mesh artifact for a drawing

    belonging to the requested project.

    """

    folder = project_dir(project_id)



    try:

        safe_project_id = str(UUID(project_id))

        safe_drawing_id = str(UUID(drawing_id))

    except (ValueError, TypeError, AttributeError):

        raise HTTPException(status_code=404, detail="Project or drawing not found")



    project = read_json(folder / "project.json", {})

    drawing = next(

        (

            item for item in project.get("drawings", [])

            if item.get("id") == safe_drawing_id

        ),

        None,

    )



    if drawing is None:

        raise HTTPException(

            status_code=404,

            detail="Drawing does not belong to this project",

        )



    artifact_path = (

        folder

        / "artifacts"

        / "mesh_batches"

        / f"{safe_drawing_id}_stage5_3.json"

    )



    if not artifact_path.is_file():

        raise HTTPException(

            status_code=404,

            detail="No registered mesh artifact exists for this drawing",

        )



    if artifact_path.stat().st_size > 50 * 1024 * 1024:

        raise HTTPException(

            status_code=413,

            detail="Mesh artifact exceeds the 50 MB response limit",

        )



    artifact = read_json(artifact_path, None)



    if not isinstance(artifact, dict):

        raise HTTPException(status_code=422, detail="Invalid mesh artifact")



    if artifact.get("project_id") != safe_project_id:

        raise HTTPException(

            status_code=422,

            detail="Mesh artifact project ownership mismatch",

        )



    if artifact.get("drawing_id") != safe_drawing_id:

        raise HTTPException(

            status_code=422,

            detail="Mesh artifact drawing ownership mismatch",

        )



    elements = artifact.get("elements")

    if not isinstance(elements, list):

        raise HTTPException(status_code=422, detail="Invalid mesh elements")



    for element in elements:

        if not isinstance(element, dict):

            raise HTTPException(status_code=422, detail="Invalid mesh element")



        mesh = element.get("mesh")

        if not isinstance(mesh, dict):

            raise HTTPException(status_code=422, detail="Invalid mesh data")



        vertices = mesh.get("vertices")

        faces = mesh.get("faces")



        if not isinstance(vertices, list) or not isinstance(faces, list):

            raise HTTPException(status_code=422, detail="Invalid mesh geometry")



        for vertex in vertices:

            if (

                not isinstance(vertex, list)

                or len(vertex) != 3

                or any(

                    not isinstance(value, (int, float))

                    or isinstance(value, bool)

                    or not math.isfinite(value)

                    for value in vertex

                )

            ):

                raise HTTPException(status_code=422, detail="Invalid mesh vertex")



        for face in faces:

            if (

                not isinstance(face, list)

                or len(face) != 3

                or any(

                    not isinstance(index, int)

                    or isinstance(index, bool)

                    or index < 0

                    or index >= len(vertices)

                    for index in face

                )

            ):

                raise HTTPException(status_code=422, detail="Invalid mesh face")



    return JSONResponse(content=artifact)
