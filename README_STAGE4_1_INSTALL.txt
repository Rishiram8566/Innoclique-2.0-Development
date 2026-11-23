INNOCLIQUE 2.0 — STAGE 4.1 CORRECTION ENGINE
==============================================

This update improves the existing Stage 4 text-first intelligence modules.
It preserves the uploaded Stage 4.1 main.py API routes and project storage logic.

INSTALL
1. Stop the backend server with CTRL+C.
2. Make a backup copy of backend/app (for example backend/app_backup_before_4_1).
3. Extract this ZIP.
4. Copy the included backend/app folder into:
   C:\Users\rishi\OneDrive\Desktop\innoclique-2.0-starter\backend\
   Allow Windows to merge/replace files.
5. DO NOT replace, delete, rename or edit backend/data.
6. DO NOT replace .venv or requirements.txt.
7. Restart:
   cd C:\Users\rishi\OneDrive\Desktop\innoclique-2.0-starter\backend
   .\.venv\Scripts\Activate.ps1
   python -m uvicorn app.main:app --reload --port 8000

TEST
1. Open http://127.0.0.1:8000/docs
2. Run POST /api/projects/{project_id}/coordinate with the EXISTING project ID:
   5e9627a7-1b4b-41ea-8ac9-c2e2c5baf845
3. Then run GET /api/projects/{project_id}/intelligence with the same ID.
4. Inspect quality_summary, project_profile, discipline_summary,
   relationship_graph and sample drawing records.

SAFETY
- This update does not upload or delete PDFs.
- It does not create a new project.
- It writes a new Stage 4.1 report to the existing project's
  artifacts/project_intelligence.json, replacing the previous Stage 4 report.
  If you want to preserve the old report, copy that JSON file elsewhere first.
- Stage 3 drawing_intelligence.json remains untouched.
- Text-based extraction is not OCR, CAD vector reconstruction, clash detection,
  or BIM generation. Do not use unverified candidates as final geometry.
- Classification and extraction remain heuristic and must be checked against
  drawing graphics and engineering review.

CHANGE SUMMARY
- Filename/title-weighted sheet classification.
- More conservative multi-discipline classification and drawing-series support.
- Stricter equipment tag and grid patterns to reduce obvious false positives.
- Dimension candidates include contextual confidence.
- Project profiling uses weighted signals across distinct drawings.
- Relationship graph detects explicit callouts and narrowly defined schedule rows.
- Quality summary exposes low-confidence and unclassified records.
