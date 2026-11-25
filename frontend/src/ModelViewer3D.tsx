import { Component, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Canvas, useLoader } from '@react-three/fiber';
import { Bounds, Html, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const MM_PER_M = 1000;

type LOD = '100' | '200' | '300' | '400' | '500';
const LODS: LOD[] = ['100', '200', '300', '400', '500'];

type MeshElement = {
  element_id: string;
  component?: string;
  dimensions_mm: { length: number; thickness: number; height: number; volume: number };
  mesh: { vertices: number[][]; faces: number[][] };
  lod?: { minimum_lod: number };
  height_verification?: {
    verification_id: string;
    reviewer?: string;
    verified_at?: string;
    printed_measurements?: string[];
  };
  provenance?: {
    source_lines?: Record<string, { path_index: number; item_index: number }>;
    millimetres_per_pdf_point?: number;
  };
};

type MeshResponse = {
  stage: string;
  drawing_title?: string;
  page?: { size_mm: number[] | null };
  calibration?: { millimetres_per_pdf_point: number; verification_required?: boolean };
  elements: MeshElement[];
  withheld_elements: { element_id: string | null; reasons: string[] }[];
  serving_gate: { served: number; withheld: number; elements_in_artifact: number };
  limitations?: string[];
};

const LOD_NOTES: Record<LOD, string> = {
  '100': 'LOD 100 shows the structural frame (columns and beams). None are source-verified on this drawing yet, so only the plan is shown.',
  '200': 'LOD 200 adds floor slabs to the frame. No slab is source-verified yet, so only the plan is shown.',
  '300': 'LOD 300: reviewer-confirmed walls at their verified heights.',
  '400': 'LOD 400: walls with construction edges. No construction-level details are in the verified evidence yet.',
  '500': 'LOD 500: walls coloured by component with labels. No roof, rooms or scan data are verified yet.',
};

const COMPONENT_COLORS: Record<string, string> = {
  parapet: '#e2c799',
  wall: '#cfcac0',
};
const CONCRETE = '#c9c5bb';
const SELECTED = '#f97316';

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => (d as { msg?: string })?.msg ?? String(d)).join('; ');
  return '';
}

function mm(value: number | undefined) {
  return value == null ? '—' : `${Math.round(value).toLocaleString()} mm`;
}

/** Page-local mm (x right, y down, z up) -> three.js metres (x, up, toward viewer). */
function toThree([x, y, z]: number[]): [number, number, number] {
  return [x / MM_PER_M, z / MM_PER_M, y / MM_PER_M];
}

function useWallGeometry(element: MeshElement) {
  return useMemo(() => {
    const positions = new Float32Array(element.mesh.vertices.flatMap(v => toThree(v)));
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    indexed.setIndex(element.mesh.faces.flat());
    const flat = indexed.toNonIndexed();
    flat.computeVertexNormals();
    indexed.dispose();
    return flat;
  }, [element]);
}

function Wall({
  element,
  lod,
  selected,
  onSelect,
}: {
  element: MeshElement;
  lod: LOD;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const geometry = useWallGeometry(element);
  const edges = useMemo(() => new THREE.EdgesGeometry(geometry), [geometry]);
  const component = element.component ?? 'wall';
  const color = selected
    ? SELECTED
    : lod === '500'
      ? COMPONENT_COLORS[component] ?? COMPONENT_COLORS.wall
      : CONCRETE;

  const top = useMemo(() => {
    const box = new THREE.Box3().setFromBufferAttribute(
      geometry.getAttribute('position') as THREE.BufferAttribute,
    );
    const center = box.getCenter(new THREE.Vector3());
    return [center.x, box.max.y + 0.6, center.z] as [number, number, number];
  }, [geometry]);

  return (
    <group>
      <mesh
        geometry={geometry}
        castShadow
        receiveShadow
        onClick={event => {
          event.stopPropagation();
          onSelect(element.element_id);
        }}
        onPointerOver={() => { document.body.style.cursor = 'pointer'; }}
        onPointerOut={() => { document.body.style.cursor = ''; }}
      >
        <meshStandardMaterial color={color} roughness={0.85} metalness={0.02} side={THREE.DoubleSide} />
      </mesh>
      {(lod === '400' || lod === '500') && (
        <lineSegments geometry={edges}>
          <lineBasicMaterial color="#1f2937" />
        </lineSegments>
      )}
      {lod === '500' && (
        <Html position={top} center style={{ pointerEvents: 'none' }}>
          <div style={{
            background: 'rgba(15,23,42,.85)', color: '#f8fafc', fontSize: 10,
            padding: '2px 6px', borderRadius: 4, whiteSpace: 'nowrap',
          }}>
            {component} · {mm(element.dimensions_mm.height)}
          </div>
        </Html>
      )}
    </group>
  );
}

/** If the plan image cannot load, show a grid instead of crashing the page. */
class GroundFallback extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed
      ? <gridHelper args={[200, 40, '#94a3b8', '#cbd5e1']} />
      : this.props.children;
  }
}

function PlanGround({ url, sizeMm }: { url: string; sizeMm: number[] }) {
  const texture = useLoader(THREE.TextureLoader, url);
  useEffect(() => {
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 8;
    texture.needsUpdate = true;
  }, [texture]);
  const width = sizeMm[0] / MM_PER_M;
  const depth = sizeMm[1] / MM_PER_M;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[width / 2, -0.01, depth / 2]} receiveShadow>
      <planeGeometry args={[width, depth]} />
      <meshStandardMaterial map={texture} roughness={1} />
    </mesh>
  );
}

export default function ModelViewer3D({
  projectId,
  drawingId,
}: {
  projectId: string;
  drawingId: string;
}) {
  const [model, setModel] = useState<MeshResponse | null>(null);
  const [status, setStatus] = useState<'loading' | 'none' | 'ready' | 'error'>('loading');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [lod, setLod] = useState<LOD>('300');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setStatus('loading');
    setSelectedId(null);
    try {
      const response = await fetch(`${API}/projects/${projectId}/drawings/${drawingId}/meshes`);
      const body = await response.json().catch(() => ({}));
      if (response.status === 404) {
        setModel(null);
        setStatus('none');
        return;
      }
      if (!response.ok) throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
      setModel(body as MeshResponse);
      setStatus('ready');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Could not load the model.');
      setStatus('error');
    }
  }, [projectId, drawingId]);

  useEffect(() => {
    setMessage('');
    load();
  }, [load]);

  async function generate() {
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch(
        `${API}/projects/${projectId}/geometry/stage5-4/generate?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`,
        { method: 'POST' },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatDetail(body?.detail) || `Generation failed (${response.status})`);
      const { meshes_built, failed } = body.summary;
      setMessage(`Generated ${meshes_built} wall mesh(es)${failed ? `; ${failed} failed checks` : ''}.`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Generation failed.');
    } finally {
      setBusy(false);
    }
  }

  const visible = useMemo(
    () => (model?.elements ?? []).filter(e => Number(lod) >= (e.lod?.minimum_lod ?? 300)),
    [model, lod],
  );
  const selected = model?.elements.find(e => e.element_id === selectedId) ?? null;
  const sizeMm = model?.page?.size_mm ?? null;
  const previewUrl = `${API}/projects/${projectId}/phase6/preview/${drawingId}?page_number=${PAGE_NUMBER}`;

  const generateButton = (
    <button className="primary" disabled={busy} onClick={generate}>
      {busy ? 'Generating…' : model ? 'Regenerate from confirmed walls' : 'Generate 3D model from confirmed walls'}
    </button>
  );

  if (status === 'loading') return <div className="message">Loading 3D model...</div>;

  if (status !== 'ready' || !model) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <div className="message">
          {status === 'error'
            ? message
            : 'No 3D model has been generated for this drawing yet. Confirm walls in section 04, then generate.'}
        </div>
        <div>{generateButton}</div>
        {status === 'none' && message && <div className="message" style={{ color: '#fbbf24' }}>{message}</div>}
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 14, color: '#cbd5e1', fontSize: 13 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'center' }}>
        <span>Walls in model: {model.serving_gate.served}</span>
        <span style={{ color: model.serving_gate.withheld ? '#fbbf24' : undefined }}>
          Withheld: {model.serving_gate.withheld}
        </span>
        <span>Stage {model.stage}</span>
        <div style={{ marginLeft: 'auto' }}>{generateButton}</div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ color: '#94a3b8', fontSize: 12 }}>Level of detail</span>
        {LODS.map(level => (
          <button key={level} onClick={() => setLod(level)} style={lodButton(lod === level)}>
            LOD {level}
          </button>
        ))}
      </div>

      <div style={noteStyle}>{LOD_NOTES[lod]}</div>

      <div style={{
        height: 560, width: '100%', overflow: 'hidden', borderRadius: 14,
        border: '1px solid #334155', background: 'linear-gradient(#dfe7ef, #f4f6f8)',
      }}>
        <Canvas shadows camera={{ position: [60, 90, 140], fov: 45, near: 0.1, far: 5000 }}>
          <hemisphereLight args={['#ffffff', '#b0a998', 0.6]} />
          <ambientLight intensity={0.35} />
          <directionalLight
            position={[120, 180, 60]}
            intensity={1.4}
            castShadow
            shadow-mapSize-width={2048}
            shadow-mapSize-height={2048}
            shadow-camera-left={-200}
            shadow-camera-right={200}
            shadow-camera-top={200}
            shadow-camera-bottom={-200}
            shadow-camera-far={600}
          />
          <Suspense fallback={null}>
            <Bounds fit clip observe margin={1.15}>
              <group onPointerMissed={() => setSelectedId(null)}>
                {sizeMm ? (
                  <GroundFallback>
                    <Suspense fallback={null}>
                      <PlanGround url={previewUrl} sizeMm={sizeMm} />
                    </Suspense>
                  </GroundFallback>
                ) : (
                  <gridHelper args={[200, 40, '#94a3b8', '#cbd5e1']} />
                )}
                {visible.map(element => (
                  <Wall
                    key={element.element_id}
                    element={element}
                    lod={lod}
                    selected={element.element_id === selectedId}
                    onSelect={setSelectedId}
                  />
                ))}
              </group>
            </Bounds>
          </Suspense>
          <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.05} />
        </Canvas>
      </div>

      <small style={{ color: '#8fa1b5' }}>
        {model.drawing_title} · Page {PAGE_NUMBER} · page-local metres from a calibration that still
        requires verification
        {model.calibration ? ` (${model.calibration.millimetres_per_pdf_point.toFixed(3)} mm per PDF point)` : ''}
        {' '}· wall bases at page-local zero; floor levels not yet associated · drag to orbit, right-drag to pan,
        scroll to zoom, click a wall to inspect.
      </small>

      {message && <div className="message">{message}</div>}

      {selected && (
        <div style={{ ...noteStyle, display: 'grid', gap: 6 }}>
          <strong style={{ color: '#eaf0f8' }}>
            {(selected.component ?? 'wall').replace(/^./, c => c.toUpperCase())} · {selected.element_id}
          </strong>
          <span>
            Length {mm(selected.dimensions_mm.length)} · Thickness {mm(selected.dimensions_mm.thickness)} ·
            Height {mm(selected.dimensions_mm.height)} · Volume{' '}
            {(selected.dimensions_mm.volume / 1e9).toFixed(2)} m³
          </span>
          {selected.height_verification && (
            <span>
              Height confirmed by {selected.height_verification.reviewer ?? 'reviewer'} from “
              {(selected.height_verification.printed_measurements ?? []).join(', ')}” · verification{' '}
              {selected.height_verification.verification_id}
            </span>
          )}
          {selected.provenance?.source_lines && (
            <span>
              Source lines:{' '}
              {Object.entries(selected.provenance.source_lines)
                .map(([key, ref]) => `${key} (${ref.path_index}, ${ref.item_index})`)
                .join(' · ')}
            </span>
          )}
        </div>
      )}

      {model.withheld_elements.length > 0 && (
        <details>
          <summary>{model.withheld_elements.length} element(s) withheld by the height gate</summary>
          <ul>
            {model.withheld_elements.map((item, index) => (
              <li key={index}>
                {item.element_id ?? 'unknown element'}: {item.reasons.join(' ')}
              </li>
            ))}
          </ul>
        </details>
      )}

      {model.limitations && (
        <details>
          <summary>Model limitations</summary>
          <ul>{model.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

const noteStyle: React.CSSProperties = {
  padding: '10px 12px',
  border: '1px solid #475569',
  borderRadius: 10,
  background: '#172033',
  fontSize: 12,
  lineHeight: 1.6,
};

function lodButton(active: boolean): React.CSSProperties {
  return {
    padding: '8px 12px',
    borderRadius: 7,
    border: `1px solid ${active ? '#b7f36b' : '#475569'}`,
    background: active ? '#24331a' : '#172033',
    color: '#e2e8f0',
    cursor: 'pointer',
    fontSize: 12,
  };
}
