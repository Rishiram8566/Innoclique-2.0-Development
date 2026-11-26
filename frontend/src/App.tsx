
import { useEffect, useRef, useState } from 'react';
import ModelViewer3D from './ModelViewer3D';
import AutoModelPanel from './AutoModelPanel';
import {
  Building2,
  FileUp,
  FolderOpen,
  Layers3,
  ScanLine,
  ShieldCheck
} from 'lucide-react';

const API = 'http://127.0.0.1:8000/api';

type Project = {
  id: string;
  name: string;
  project_type: string;
  status: string;
  drawings: {
    id: string;
    filename: string;
    size_bytes: number;
    processing_status: string;
  }[];
  models?: { id: string; filename: string; model_type: string }[];
};

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [active, setActive] = useState<Project | null>(null);
  const [name, setName] = useState('New Civil Project');
  const [projectType, setProjectType] = useState('building');
  const [files, setFiles] = useState<FileList | null>(null);
  const [message, setMessage] = useState(
    'Create a project workspace to begin.'
  );
  const [busy, setBusy] = useState(false);
  // Bumped when the automatic pipeline finishes so the 3D model reloads.
  const [modelVersion, setModelVersion] = useState(0);

  // Set when the project list could not be loaded, so the library never claims there are no projects
  // just because the server was busy or down; the list is retried until it loads.
  const [listError, setListError] = useState<string | null>(null);
  const retryTimer = useRef<number | null>(null);

  async function refresh() {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${API}/projects`, { signal: controller.signal });

      if (!response.ok) {
        throw new Error('API unavailable');
      }

      setProjects(await response.json());
      setListError(null);
    } catch {
      setListError(
        'Could not load your projects: the server is busy or not running. Retrying automatically...'
      );
      setMessage(
        'Backend not connected. Start the FastAPI server on port 8000.'
      );
      if (retryTimer.current === null) {
        retryTimer.current = window.setTimeout(() => {
          retryTimer.current = null;
          refresh();
        }, 5000);
      }
    } finally {
      window.clearTimeout(timeout);
    }
  }

  useEffect(() => {
    refresh();
    return () => {
      if (retryTimer.current !== null) window.clearTimeout(retryTimer.current);
    };
  }, []);

  async function createProject() {
    setBusy(true);

    try {
      const response = await fetch(`${API}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          project_type: projectType
        })
      });

      if (!response.ok) {
        throw new Error('Could not create project');
      }

      const project = await response.json();

      setActive(project);
      setMessage(
        'Project workspace created. Add its related PDF drawing sheets.'
      );

      await refresh();
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : 'Project creation failed.'
      );
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!active || !files?.length) {
      setMessage('Choose one or more PDF drawing sheets first.');
      return;
    }

    setBusy(true);

    const body = new FormData();

    Array.from(files).forEach(file => {
      body.append('files', file);
    });

    try {
      const response = await fetch(
        `${API}/projects/${active.id}/drawings`,
        {
          method: 'POST',
          body
        }
      );

      const result = await response.json();

      if (!response.ok) {
        throw new Error(JSON.stringify(result.detail ?? result));
      }

      setMessage(
        `${result.accepted.length} file(s) added to this project.`
        + (result.rejected.length
          ? ` Not added: ${result.rejected.map((r: { filename: string; reason: string }) => `${r.filename} — ${r.reason}`).join(' | ')}`
          : '')
      );

      const refreshed = await fetch(`${API}/projects`);
      const all: Project[] = await refreshed.json();

      setProjects(all);
      setActive(all.find(p => p.id === active.id) ?? active);

      setFiles(null);

      const input = document.getElementById(
        'pdf-input'
      ) as HTMLInputElement | null;

      if (input) {
        input.value = '';
      }
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : 'Upload failed.'
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            <Building2 size={22} />
          </div>
          <div>
            <strong>INNOCLIQUE</strong>
            <span>ENGINEERING INTELLIGENCE</span>
          </div>
        </div>

        <div className="top-status">
          <span className="pulse" />
          DEVELOPMENT WORKSPACE
        </div>
      </header>

      <section className="hero">
        <div className="eyebrow">
          PDF-FIRST CIVIL ENGINEERING PLATFORM
        </div>

        <h1>
          From drawings to
          <br />
          <em>intelligent 3D infrastructure.</em>
        </h1>

        <p>
          Bring related engineering PDFs into one coordinated project.
          Build toward structured BIM, progressive LOD and interactive
          systems inspection.
        </p>
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">01 / PROJECT SETUP</span>
            <h2>Project workspace</h2>
          </div>

          <span className="tag">
            <ShieldCheck size={14} /> PDF input · CAD not required
          </span>
        </div>

        <div className="create-grid">
          <label>
            Project name
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Dubai Science Park"
            />
          </label>

          <label>
            Project category
            <select
              value={projectType}
              onChange={e => setProjectType(e.target.value)}
            >
              <option value="building">Building</option>
              <option value="industrial_plant">Industrial plant</option>
              <option value="power_station">Power station</option>
              <option value="water_dam">Water dam</option>
              <option value="telecom_tower">Cell / telecom tower</option>
              <option value="bridge">Bridge / infrastructure</option>
              <option value="water_treatment">
                Water treatment plant
              </option>
              <option value="other_civil">Other civil project</option>
            </select>
          </label>

          <button
            className="primary"
            onClick={createProject}
            disabled={busy || name.trim().length < 2}
          >
            Create project <span>→</span>
          </button>
        </div>
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">02 / DRAWING INGESTION</span>
            <h2>Upload drawing sheets</h2>
          </div>

          <span className="tag">
            <FolderOpen size={14} /> One project · Multiple PDFs
          </span>
        </div>

        <div className="upload-grid">
          <div className="dropzone">
            <div className="upload-icon">
              <FileUp size={25} />
            </div>

            <h3>Add drawings and models (any number of files)</h3>

            <p>
              IFC models (from Revit: File &gt; Export &gt; IFC), CAD files (.dwg, .dxf) and PDF drawings.
              IFC gives the most exact model, then CAD: real dimensions, levels, doors, windows, equipment and materials.
              A Revit .rvt file cannot be read directly; export it to IFC first.
              Floor plans, elevations, sections, structural and MEP
              sheets can be grouped under the same project.
            </p>

            <input
              id="pdf-input"
              type="file"
              accept="application/pdf,.pdf,.dxf,.dwg,.ifc,.rvt"
              multiple
              onChange={e => setFiles(e.target.files)}
            />

            <label htmlFor="pdf-input" className="file-button">
              Choose files (PDF, DXF, IFC)
            </label>

            <small>
              {files?.length
                ? `${files.length} file(s) selected`
                : 'PDF · DWG · DXF · IFC (Revit export) · up to 1000 files per upload'}
            </small>
          </div>

          <div className="upload-side">
            <label>
              Target project
              <select
                value={active?.id ?? ''}
                onChange={e =>
                  setActive(
                    projects.find(p => p.id === e.target.value) ?? null
                  )
                }
              >
                <option value="">Select a project</option>

                {projects.map(p => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <button
              className="primary wide"
              onClick={upload}
              disabled={busy || !active || !files?.length}
            >
              Upload drawings <span>↑</span>
            </button>

            <div className="notice">
              <ShieldCheck size={17} />
              <span>
                Files are grouped into one project workspace. After uploading,
                press “Generate 3D model” below.
              </span>
            </div>
          </div>
        </div>

        <div className="message">{message}</div>
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">03 / GENERATE</span>
            <h2>Generate the 3D model</h2>
          </div>
          <span className="tag">Upload PDFs · one click · LOD 100–500</span>
        </div>
        {active ? (
          <AutoModelPanel
            key={active.id}
            projectId={active.id}
            drawingCount={(active.drawings?.length ?? 0) + (active.models?.length ?? 0)}
            onFinished={() => setModelVersion(version => version + 1)}
          />
        ) : (
          <div className="message">Create or select a project, then upload its PDF drawings.</div>
        )}
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">04 / 3D MODEL</span>
            <h2>3D model by level of detail</h2>
          </div>
          <span className="tag">LOD 100–500 · whole building or one drawing</span>
        </div>
        {active ? (
          <ModelViewer3D
            key={`${active.id}-${modelVersion}`}
            projectId={active.id}
            drawings={active.drawings ?? []}
          />
        ) : (
          <div className="message">Select a project to view its 3D model.</div>
        )}
      </section>


      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">05 / PROJECT LIBRARY</span>
            <h2>Your projects</h2>
          </div>

          <span className="tag">
            {projects.length} workspace(s)
          </span>
        </div>

        <div className="project-list">
          {projects.length === 0 ? (
            <div className="empty">
              <FolderOpen size={24} />
              <span>
                {listError ?? 'No projects yet. Create your first workspace above.'}
              </span>
            </div>
          ) : (
            projects.map(project => (
              <button
                className={`project-row ${
                  active?.id === project.id ? 'selected' : ''
                }`}
                key={project.id}
                onClick={() => setActive(project)}
              >
                <div className="project-icon">
                  <Building2 size={19} />
                </div>

                <div className="project-meta">
                  <strong>{project.name}</strong>

                  <small>
                    {project.project_type.replace(/_/g, ' ')} ·{' '}
                    {project.drawings?.length ?? 0} PDF(s)
                    {project.models?.length ? ` · ${project.models.length} CAD/BIM model(s)` : ''}
                  </small>
                </div>

                <span className="row-arrow">→</span>
              </button>
            ))
          )}
        </div>
      </section>

      <section className="future">
        <div className="future-head">
          <Layers3 size={18} />
          <span>ENGINEERING PIPELINE — DEVELOPMENT ROADMAP</span>
        </div>

        <div className="roadmap">
          <div className="road-item active">
            <b>01</b>
            <strong>PDF intake</strong>
            <small>Foundation</small>
          </div>

          <div className="road-item">
            <b>02</b>
            <strong>Drawing AI</strong>
            <small>OCR · symbols · dimensions</small>
          </div>

          <div className="road-item">
            <b>03</b>
            <strong>3D / BIM</strong>
            <small>Parametric geometry</small>
          </div>

          <div className="road-item">
            <b>04</b>
            <strong>LOD 100–500</strong>
            <small>Progressive detail</small>
          </div>

          <div className="road-item">
            <b>05</b>
            <strong>Scan mode</strong>
            <small>MEP inspection</small>
          </div>
        </div>
      </section>

      <footer>
        <span>INNOCLIQUE 2.0</span>
        <span>
          <ScanLine size={13} /> BUILT FOR CIVIL ENGINEERING
        </span>
      </footer>
    </main>
  );
}

