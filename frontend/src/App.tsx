
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import ModelViewer3D, { ShowcaseModel } from './ModelViewer3D';
import type { LOD, ShowcaseSummary } from './ModelViewer3D';
import AutoModelPanel from './AutoModelPanel';
import {
  ArrowRight,
  Boxes,
  Building2,
  Check,
  ChevronDown,
  CloudUpload,
  Droplets,
  Factory,
  FolderOpen,
  Landmark,
  Plus,
  RadioTower,
  Search,
  Waves,
  X,
  Zap
} from 'lucide-react';

const API = 'http://127.0.0.1:8000/api';
// Innoclique's own sample: an office building it generated from an IFC file (test_drawings/make_office_ifc.py).
const SHOWCASE_URL = '/showcase/sample-building.json';

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

const PROJECT_TYPES: { value: string; label: string }[] = [
  { value: 'building', label: 'Building' },
  { value: 'industrial_plant', label: 'Industrial plant' },
  { value: 'power_station', label: 'Power station' },
  { value: 'water_dam', label: 'Water dam' },
  { value: 'telecom_tower', label: 'Cell / telecom tower' },
  { value: 'bridge', label: 'Bridge / infrastructure' },
  { value: 'water_treatment', label: 'Water treatment plant' },
  { value: 'other_civil', label: 'Other civil project' },
];

const typeIcon = (type: string, size = 18) => {
  const Icon = { industrial_plant: Factory, power_station: Zap, water_dam: Waves, telecom_tower: RadioTower,
    bridge: Landmark, water_treatment: Droplets, other_civil: Boxes }[type] ?? Building2;
  return <Icon size={size} />;
};

const fileCount = (p: Project) => (p.drawings?.length ?? 0) + (p.models?.length ?? 0);
const describe = (p: Project) =>
  `${p.project_type.replace(/_/g, ' ')} · ${p.drawings?.length ?? 0} PDF(s)`
  + (p.models?.length ? ` · ${p.models.length} CAD/BIM file(s)` : '');

const LODS: LOD[] = ['100', '200', '300', '400', '500'];
const LOD_CAPTION: Record<LOD, string> = {
  '100': 'Structure: the column grid of every floor.',
  '200': 'Floor slabs and the roof are added.',
  '300': 'Walls, glazing and doors: the building as drawn.',
  '400': 'Facade detail: mullions, fins and trims.',
  '500': 'Fit-out: desks, roof-top plant and real materials.',
};

// The Innoclique mark: an "i" built from three floor slabs, with the orange dot.
function LogoMark({ className = 'brand-mark' }: { className?: string }) {
  const gradient = `innoclique-blue-${useId().replace(/:/g, '')}`;
  return (
    <svg className={className} viewBox="0 0 40 40" aria-hidden="true">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3b7bff" />
          <stop offset="1" stopColor="#1640b8" />
        </linearGradient>
      </defs>
      <rect width="40" height="40" rx="11" fill={`url(#${gradient})`} />
      <circle cx="20" cy="10.2" r="3.7" fill="#ff7a1a" />
      <rect x="14.2" y="16.4" width="11.6" height="4.4" rx="1.4" fill="#fff" />
      <rect x="14.2" y="22.4" width="11.6" height="4.4" rx="1.4" fill="#fff" fillOpacity=".86" />
      <rect x="14.2" y="28.4" width="11.6" height="4.4" rx="1.4" fill="#fff" fillOpacity=".72" />
    </svg>
  );
}

const formatDetail = (detail: unknown) =>
  typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : '';

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [active, setActive] = useState<Project | null>(null);
  const [mode, setMode] = useState<'new' | 'existing'>('new');
  const [name, setName] = useState('');
  const [projectType, setProjectType] = useState('building');
  const [files, setFiles] = useState<FileList | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState('');
  // Bumped when the automatic pipeline finishes so the 3D model reloads.
  const [modelVersion, setModelVersion] = useState(0);
  // Bumped when this page starts a run, so the progress panel picks it up.
  const [runNonce, setRunNonce] = useState(0);
  const [engine, setEngine] = useState<'checking' | 'online' | 'offline'>('checking');
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [dragging, setDragging] = useState(false);
  const [showcaseLod, setShowcaseLod] = useState<LOD>('500');
  const [showcase, setShowcase] = useState<ShowcaseSummary | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // Set when the project list could not be loaded, so the list never claims there are no projects
  // just because the server was busy or down; the list is retried until it loads.
  const [listError, setListError] = useState<string | null>(null);
  const retryTimer = useRef<number | null>(null);

  async function refresh(): Promise<Project[] | null> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${API}/projects`, { signal: controller.signal });
      if (!response.ok) throw new Error('API unavailable');
      const all: Project[] = await response.json();
      setProjects(all);
      setListError(null);
      setEngine('online');
      return all;
    } catch {
      setEngine('offline');
      setListError('Could not load your projects: the server is busy or not running. Retrying automatically…');
      if (retryTimer.current === null) {
        retryTimer.current = window.setTimeout(() => {
          retryTimer.current = null;
          refresh();
        }, 5000);
      }
      return null;
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

  // Close the projects menu on a click outside it or on Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey); };
  }, [menuOpen]);

  const scrollToWorkspace = () =>
    window.setTimeout(() => document.getElementById('workspace')?.scrollIntoView({ behavior: 'smooth' }), 120);

  function openProject(project: Project) {
    setActive(project);
    setMode('existing');
    setMenuOpen(false);
    scrollToWorkspace();
  }

  // The one action: create the project (or use the chosen one), upload the files, and start building the model.
  async function uploadAndGenerate() {
    if (!files?.length) {
      setMessage('Choose or drop your drawings first.');
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      let project = mode === 'existing' ? active : null;
      if (mode === 'existing' && !project) throw new Error('Pick the project to add these files to.');
      if (!project) {
        setStage('Creating the project…');
        const first = files[0].name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim();
        const response = await fetch(`${API}/projects`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim() || first || 'New project', project_type: projectType })
        });
        if (!response.ok) throw new Error('Could not create the project.');
        project = (await response.json()) as Project;
      }

      setStage(`Uploading ${files.length} file(s)…`);
      const body = new FormData();
      Array.from(files).forEach(file => body.append('files', file));
      const uploaded = await fetch(`${API}/projects/${project.id}/drawings`, { method: 'POST', body });
      const result = await uploaded.json();
      if (!uploaded.ok) throw new Error(formatDetail(result.detail ?? result) || 'Upload failed.');
      const rejected = result.rejected?.length
        ? ` Not added: ${result.rejected.map((r: { filename: string; reason: string }) => `${r.filename} (${r.reason})`).join('; ')}`
        : '';
      if (!result.accepted?.length) throw new Error(`No file was added.${rejected}`);

      setStage('Starting the 3D model…');
      const started = await fetch(`${API}/projects/${project.id}/auto-model`, { method: 'POST' });
      if (!started.ok && started.status !== 409) {
        const detail = await started.json().catch(() => ({}));
        throw new Error(formatDetail(detail?.detail) || 'Could not start the model.');
      }

      const all = await refresh();
      setActive(all?.find(p => p.id === project!.id) ?? project);
      setMode('existing');
      setRunNonce(n => n + 1);
      setFiles(null);
      setName('');
      const input = document.getElementById('file-input') as HTMLInputElement | null;
      if (input) input.value = '';
      setMessage(`${result.accepted.length} file(s) added.${rejected}`);
      scrollToWorkspace();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Something went wrong.');
    } finally {
      setBusy(false);
      setStage('');
    }
  }

  // Files dropped on the upload area are taken like files chosen with the button.
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files?.length) setFiles(event.dataTransfer.files);
  };
  const pickedTypes = useMemo(() => {
    const counts = new Map<string, number>();
    Array.from(files ?? []).forEach(file => {
      const ext = (file.name.split('.').pop() ?? 'file').toUpperCase();
      counts.set(ext, (counts.get(ext) ?? 0) + 1);
    });
    return [...counts.entries()];
  }, [files]);

  const shown = useMemo(() => {
    const words = search.trim().toLowerCase();
    return words
      ? projects.filter(p => `${p.name} ${p.project_type.replace(/_/g, ' ')}`.toLowerCase().includes(words))
      : projects;
  }, [projects, search]);

  return (
    <>
      <header className="site-header">
        <div className="shell header-inner">
          <a className="brand" href="#top" aria-label="Innoclique home" onClick={() => setActive(null)}>
            <LogoMark />
            <span className="brand-name">innoclique<span className="dot">.</span></span>
          </a>

          <div className="header-right">
            <span className={`engine ${engine}`} title={engine === 'offline'
              ? 'The model engine (server on port 8000) is not answering' : 'Connected to the model engine'}>
              <i /> <span>{engine === 'offline' ? 'Offline' : engine === 'online' ? 'Online' : 'Connecting'}</span>
            </span>

            <div className="menu" ref={menuRef}>
              <button className="btn-quiet" onClick={() => setMenuOpen(open => !open)} aria-expanded={menuOpen}>
                <FolderOpen size={16} /> My projects <b>{projects.length}</b> <ChevronDown size={15} />
              </button>
              {menuOpen && (
                <div className="menu-panel" role="dialog" aria-label="Your projects">
                  <div className="search">
                    <Search size={15} />
                    <input autoFocus value={search} onChange={e => setSearch(e.target.value)} placeholder="Search projects" />
                  </div>
                  <div className="menu-list">
                    {projects.length === 0 ? (
                      <div className="empty small">{listError ?? 'No projects yet. Upload drawings to create one.'}</div>
                    ) : shown.length === 0 ? (
                      <div className="empty small">No project matches “{search}”.</div>
                    ) : shown.map(project => (
                      <button key={project.id} className={`menu-item ${active?.id === project.id ? 'on' : ''}`}
                        onClick={() => openProject(project)}>
                        <span className="menu-icon">{typeIcon(project.project_type, 16)}</span>
                        <span className="menu-text"><strong>{project.name}</strong><small>{describe(project)}</small></span>
                        {active?.id === project.id && <Check size={16} />}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>

      <main className="shell" id="top">
        <section className="hero">
          <div className="hero-copy">
            <span className="kicker">PDF · DWG · DXF · IFC <ArrowRight size={13} /> 3D BIM</span>
            <h1>
              Upload your drawings.
              <br />
              <span className="accent">Get a 3D model.</span>
            </h1>
            <p className="lead">
              Innoclique reads floor plans, CAD files and Revit exports and builds the BIM model for you,
              from LOD 100 to LOD 500.
            </p>

            <div className="upload-card">
              <div className="tabs" role="tablist">
                <button role="tab" aria-selected={mode === 'new'} className={mode === 'new' ? 'on' : ''}
                  onClick={() => setMode('new')}><Plus size={15} /> New project</button>
                <button role="tab" aria-selected={mode === 'existing'} className={mode === 'existing' ? 'on' : ''}
                  onClick={() => setMode('existing')} disabled={!projects.length}><FolderOpen size={15} /> Add to a project</button>
              </div>

              <div
                className={`dropzone ${dragging ? 'drag' : ''} ${files?.length ? 'has-files' : ''}`}
                onDragOver={e => { e.preventDefault(); setDragging(true); }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                <input id="file-input" type="file" multiple accept="application/pdf,.pdf,.dxf,.dwg,.ifc,.rvt"
                  onChange={e => setFiles(e.target.files)} />
                <span className="drop-icon"><CloudUpload size={24} /></span>
                {files?.length ? (
                  <div className="drop-text">
                    <strong>{files.length} file(s) ready</strong>
                    <span className="picked">{pickedTypes.map(([ext, n]) => <em key={ext}>{n} × {ext}</em>)}</span>
                  </div>
                ) : (
                  <div className="drop-text">
                    <strong>{dragging ? 'Drop the files here' : 'Drop your drawings here'}</strong>
                    <span>or <label htmlFor="file-input" className="link">browse your computer</label> · PDF, DWG, DXF, IFC</span>
                  </div>
                )}
                {files?.length ? (
                  <button className="icon-btn" onClick={() => setFiles(null)} aria-label="Clear the chosen files"><X size={16} /></button>
                ) : null}
              </div>

              {mode === 'new' ? (
                <div className="fields">
                  <input value={name} onChange={e => setName(e.target.value)}
                    placeholder="Project name (optional)" aria-label="Project name" />
                  <select value={projectType} onChange={e => setProjectType(e.target.value)} aria-label="Project type">
                    {PROJECT_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
              ) : (
                <div className="fields one">
                  <select value={active?.id ?? ''} aria-label="Project"
                    onChange={e => setActive(projects.find(p => p.id === e.target.value) ?? null)}>
                    <option value="">Choose a project</option>
                    {projects.map(p => <option key={p.id} value={p.id}>{p.name} · {fileCount(p)} file(s)</option>)}
                  </select>
                </div>
              )}

              <button className="cta" onClick={uploadAndGenerate} disabled={busy || !files?.length}>
                {busy ? <><span className="loader light" /> {stage || 'Working…'}</> : <>Generate 3D model <ArrowRight size={18} /></>}
              </button>
              {message && <div className="message">{message}</div>}
              <p className="fine">A Revit .rvt file cannot be read directly: export it to IFC first (File › Export › IFC).</p>
            </div>

            <ul className="checks">
              <li><Check size={15} /> PDF, DWG, DXF and IFC</li>
              <li><Check size={15} /> Any number of files</li>
              <li><Check size={15} /> LOD 100 to 500</li>
            </ul>
          </div>

          <figure className="showcase">
            <div className="showcase-stage">
              <span className="showcase-tag">Sample · office building · generated from IFC</span>
              <ShowcaseModel url={SHOWCASE_URL} lod={showcaseLod} onLoaded={setShowcase} />
            </div>
            <div className="showcase-bar">
              <div className="seg" role="group" aria-label="Level of detail">
                <span className="seg-label">LOD</span>
                {LODS.map(level => (
                  <button key={level} className={showcaseLod === level ? 'on' : ''} aria-pressed={showcaseLod === level}
                    onClick={() => setShowcaseLod(level)}>{level}</button>
                ))}
              </div>
              <figcaption>
                {LOD_CAPTION[showcaseLod]}
                {showcase ? <span className="count"> · {showcase.elements} elements</span> : null}
              </figcaption>
            </div>
          </figure>
        </section>

        {active && (
          <section className="workspace" id="workspace">
            <div className="project-bar">
              <span className="project-icon">{typeIcon(active.project_type, 20)}</span>
              <div className="project-title">
                <h2>{active.name}</h2>
                <small>{describe(active)}</small>
              </div>
              <div className="project-actions">
                <button className="btn-quiet" onClick={() => {
                  setMode('existing');
                  document.getElementById('top')?.scrollIntoView({ behavior: 'smooth' });
                }}><Plus size={15} /> Add files</button>
                <button className="icon-btn" onClick={() => setActive(null)} aria-label="Close this project"><X size={17} /></button>
              </div>
            </div>

            <AutoModelPanel
              key={`${active.id}-${runNonce}`}
              projectId={active.id}
              drawingCount={fileCount(active)}
              onFinished={() => setModelVersion(version => version + 1)}
            />

            <div className="viewer-card">
              <ModelViewer3D
                key={`${active.id}-${modelVersion}`}
                projectId={active.id}
                drawings={active.drawings ?? []}
              />
            </div>
          </section>
        )}
      </main>

      <footer>
        <div className="shell footer-inner">
          <span className="brand small"><LogoMark /> <span className="brand-name">innoclique<span className="dot">.</span></span></span>
          <p>© {new Date().getFullYear()} Innoclique · Drawings to 3D BIM</p>
        </div>
      </footer>
    </>
  );
}
