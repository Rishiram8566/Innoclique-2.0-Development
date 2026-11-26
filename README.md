# Innoclique 2.0: drawings to 3D BIM

Upload PDF drawings, CAD files (DWG, DXF) or an IFC model (Revit export) and Innoclique builds a 3D building
model, from LOD 100 to LOD 500: walls, floors, doors, windows, stairs, lifts, roofs, furniture and equipment.

The latest work is on the branch **`stage-5.3.4-5.3.5-height-verification`**.

## What you need

Install these once:

| Program | Version | Download |
|---|---|---|
| Git | any recent | https://git-scm.com/downloads |
| Python | **3.11** | https://www.python.org/downloads/ (tick "Add python.exe to PATH" in the installer) |
| Node.js | 20 or newer (LTS) | https://nodejs.org/ |
| ODA File Converter | optional: only to upload **.dwg** files | https://www.opendesign.com/guestfiles/oda_file_converter |

PDF, DXF and IFC uploads work without the ODA File Converter. Install it in its default folder
(`C:\Program Files\ODA\...`) and the app finds it by itself.

## First-time setup

Open **PowerShell** and run these one after another.

1. Get the code and switch to the latest branch:

   ```powershell
   git clone https://github.com/Rishiram8566/Innoclique-2.0-Development.git
   cd Innoclique-2.0-Development
   git checkout stage-5.3.4-5.3.5-height-verification
   ```

2. Install the server (backend):

   ```powershell
   cd backend
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements-dev.txt
   cd ..
   ```

3. Install the website (frontend):

   ```powershell
   cd frontend
   npm install
   cd ..
   ```

## Run it

You need **two PowerShell windows**, both opened in the `Innoclique-2.0-Development` folder.

Window 1, the server:

```powershell
cd backend
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Window 2, the website:

```powershell
cd frontend
npm run dev
```

Then open **http://localhost:5173** in your browser. The green **Online** dot at the top right means the
website is talking to the server. Keep both windows open while you use the app; press `Ctrl+C` in each to stop.

Use the `localhost` address above, not the "Network" address Vite prints: the server only accepts the website
from `localhost:5173`.

## Using it

1. Drop your drawings on **"Drop your drawings here"** (or click *browse your computer*). Any number of files.
2. Optionally type a project name and pick the type (Building, Water dam, Power station ...).
3. Press **Generate 3D model**. The progress bar shows each step; IFC and DXF files take seconds, a large PDF
   set can take several minutes.
4. The model opens below at LOD 500. Use the toolbar to change the level of detail, the view and the floor,
   to hide the roof, and to show the **room names**. Click a room name to walk into the room and look around
   360° (Esc to come back out).
5. Open any earlier project from **My projects** (top right).

Which files give the best model: **IFC** (from Revit: *File > Export > IFC*) first, then **DWG/DXF**, then PDF.
A Revit `.rvt` file cannot be read directly; export it to IFC.

## Sample files to try

Your project list starts empty: projects are stored on each computer (in `backend/data/`) and are not part of
the repository. To try the app without client drawings, make the sample files (they are written into the
`test_drawings` folder):

```powershell
backend\.venv\Scripts\python test_drawings\make_office_floor_dxf.py
```

| Script | Makes | What it shows |
|---|---|---|
| `make_office_floor_dxf.py` | `OFFICE_GROUND_FLOOR_PLAN.dxf`, `OFFICE_FIRST_FLOOR_PLAN.dxf` | two-storey office: glass facade, workstations, stairs, lift (upload **both** files together) |
| `make_office_ifc.py` | `OFFICE_MODEL.ifc` | the six-storey office shown on the front page |
| `make_g1_house.py` | `G1_HOUSE_GROUND_FLOOR_PLAN.pdf`, `G1_HOUSE_FIRST_FLOOR_PLAN.pdf` | two-storey house from PDFs: stairs, gable roof, furniture |
| `make_2bhk_dxf.py` / `make_2bhk.py` | `2BHK_GROUND_FLOOR_PLAN.dxf` / `.pdf` | one-storey flat |
| `make_house_ifc.py` | `HOUSE_MODEL.ifc` | small house from IFC |
| `make_cell_tower.py`, `make_water_dam.py`, `make_power_plant.py` | PDFs | structures: create the project with the matching type |

## Tests

```powershell
cd backend
.venv\Scripts\python -m pytest -q
```

## If something goes wrong

- **"Offline" at the top right / "Could not load your projects"**: the server window (window 1) is not running
  or has stopped. Start it again.
- **`python` is not recognised**: Python is not on PATH. Re-run the Python installer and tick
  "Add python.exe to PATH", then open a new PowerShell window.
- **A `.dwg` upload is refused**: install the ODA File Converter (see above), or save the drawing as DXF.
- **Port already in use**: another copy is still running. Close the old PowerShell windows and start again.
