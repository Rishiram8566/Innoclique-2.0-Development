
import { useEffect, useState } from 'react';
import SceneViewer from './SceneViewer';
import HeightVerificationPanel from './HeightVerificationPanel';
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
  const [selectedDrawingId, setSelectedDrawingId] = useState('');

  async function refresh() {
    try {
      const response = await fetch(`${API}/projects`);

      if (!response.ok) {
        throw new Error('API unavailable');
      }

      setProjects(await response.json());
    } catch {
      setMessage(
        'Backend not connected. Start the FastAPI server on port 8000.'
      );
    }
  }

  useEffect(() => {
    refresh();
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
      setSelectedDrawingId(project.drawings?.[0]?.id ?? '');
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
        `${result.accepted.length} PDF(s) added to this project. ${result.rejected.length} rejected.`
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
            <ShieldCheck size={14} /> PDF input Â· CAD not required
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
            Create project <span>â†’</span>
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
            <FolderOpen size={14} /> One project Â· Multiple PDFs
          </span>
        </div>

        <div className="upload-grid">
          <div className="dropzone">
            <div className="upload-icon">
              <FileUp size={25} />
            </div>

            <h3>Add related PDF drawings (up to 100 per batch)</h3>

            <p>
              Floor plans, elevations, sections, structural and MEP
              sheets can be grouped under the same project.
            </p>

            <input
              id="pdf-input"
              type="file"
              accept="application/pdf,.pdf"
              multiple
              onChange={e => setFiles(e.target.files)}
            />

            <label htmlFor="pdf-input" className="file-button">
              Choose PDF files
            </label>

            <small>
              {files?.length
                ? `${files.length} file(s) selected`
                : 'Up to 100 PDFs per batch Â· No fixed project PDF-count limit'}
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
              Upload drawings <span>â†‘</span>
            </button>

            <div className="notice">
              <ShieldCheck size={17} />
              <span>
                Files are grouped into one project workspace.
                Drawing interpretation and BIM generation are the
                next engineering stages.
              </span>
            </div>
          </div>
        </div>

        <div className="message">{message}</div>
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">03 / PHASE 6.0 SCENE VIEWER</span>
            <h2>Drawing geometry preview</h2>
          </div>
          <span className="tag">Source-grounded · Page-local</span>
        </div>

        {!active ? (
          <div className="message">Select a project to view its drawing geometry.</div>
        ) : (
          <>
            <label>
              Drawing sheet
              <select value={selectedDrawingId} onChange={e => setSelectedDrawingId(e.target.value)}>
                <option value="">Select a drawing</option>
                {active.drawings?.map(drawing => (
                  <option key={drawing.id} value={drawing.id}>
                    {drawing.filename}
                  </option>
                ))}
              </select>
            </label>
            {selectedDrawingId ? (
              <SceneViewer projectId={active.id} drawingId={selectedDrawingId} />
            ) : (
              <div className="message">Upload a PDF drawing to this project to preview its geometry.</div>
            )}
          </>
        )}
      </section>

      <section className="workspace">
        <div className="section-heading">
          <div>
            <span className="eyebrow">04 / STAGE 5.3.5 HEIGHT VERIFICATION</span>
            <h2>Wall-height verification</h2>
          </div>
          <span className="tag">Human decision · Audited · No auto-approval</span>
        </div>

        {active && selectedDrawingId ? (
          <HeightVerificationPanel projectId={active.id} drawingId={selectedDrawingId} />
        ) : (
          <div className="message">
            Select a project and a drawing sheet above to verify wall heights.
          </div>
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
                No projects yet. Create your first workspace above.
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
                    {project.project_type.replace(/_/g, ' ')} Â·{' '}
                    {project.drawings?.length ?? 0} PDF(s)
                  </small>
                </div>

                <span className="row-arrow">â†’</span>
              </button>
            ))
          )}
        </div>
      </section>

      <section className="future">
        <div className="future-head">
          <Layers3 size={18} />
          <span>ENGINEERING PIPELINE â€” DEVELOPMENT ROADMAP</span>
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
            <small>OCR Â· symbols Â· dimensions</small>
          </div>

          <div className="road-item">
            <b>03</b>
            <strong>3D / BIM</strong>
            <small>Parametric geometry</small>
          </div>

          <div className="road-item">
            <b>04</b>
            <strong>LOD 100â€“500</strong>
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

