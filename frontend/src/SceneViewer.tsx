import { Suspense, useEffect, useMemo, useState } from 'react';
import { Canvas, useLoader } from '@react-three/fiber';
import { Bounds, OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

const API = 'http://127.0.0.1:8000/api';
export const PDF_PREVIEW_SCALE = 1.5;

type SceneElement = {
  element_id: string;
  element_type: string;
  geometry_status: string;
  verification_required: boolean;
  approval_status?: string;
  confidence?: number;
  bbox_pdf_points: number[];
  plan_geometry: { center: number[]; width: number; height: number };
  source?: {
    line_a?: number[];
    line_b?: number[];
    evidence?: string[];
    semantic_text_hints?: string[];
  };
};

type SceneData = {
  scene_type: string;
  drawing_filename: string;
  page_number: number;
  coordinate_system: { unit: string; frame: string; z_axis_defined: boolean };
  summary: {
    candidate_count: number;
    renderable_footprint_count: number;
    rejected_invalid_bbox_count: number;
    approved_bim_element_count: number;
  };
  elements: SceneElement[];
  limitations: string[];
};

type DisplayElement = {
  id: string;
  element_type: string;
  bbox: number[];
  center: number[];
  width: number;
  height: number;
  memberCount: number;
  verificationRequired: boolean;
};

type LOD = '100' | '200' | '300' | '400' | '500';

function elementColor(type: string) {
  if (type.includes('wall')) return '#60a5fa';
  if (type.includes('stair')) return '#f59e0b';
  if (type.includes('equipment')) return '#a78bfa';
  if (type.includes('tank')) return '#22c1c3';
  return '#94a3b8';
}

function sourceKey(line: number[] | undefined) {
  return Array.isArray(line) ? JSON.stringify(line) : null;
}

function buildGroupedElements(elements: SceneElement[]): DisplayElement[] {
  const parent = new Map<number, number>();

  const find = (value: number): number => {
    if (!parent.has(value)) parent.set(value, value);
    const p = parent.get(value)!;
    if (p !== value) parent.set(value, find(p));
    return parent.get(value)!;
  };

  const lineOwner = new Map<string, number>();
  const eligible = new Set<number>();

  const union = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(rb, ra);
  };

  elements.forEach((element, index) => {
    const a = sourceKey(element.source?.line_a);
    const b = sourceKey(element.source?.line_b);
    if (!a || !b) return;
    eligible.add(index);

    for (const key of [a, b]) {
      const previous = lineOwner.get(key);
      if (previous !== undefined) union(index, previous);
      else lineOwner.set(key, index);
    }
  });

  const components = new Map<number, number[]>();
  eligible.forEach(index => {
    const root = find(index);
    const members = components.get(root) ?? [];
    members.push(index);
    components.set(root, members);
  });

  elements.forEach((_, index) => {
    if (!eligible.has(index)) components.set(-(index + 1), [index]);
  });

  return Array.from(components.values()).map((indices, groupIndex) => {
    const members = indices.map(index => elements[index]);
    const x0 = Math.min(...members.map(e => e.bbox_pdf_points[0]));
    const y0 = Math.min(...members.map(e => e.bbox_pdf_points[1]));
    const x1 = Math.max(...members.map(e => e.bbox_pdf_points[2]));
    const y1 = Math.max(...members.map(e => e.bbox_pdf_points[3]));

    const typeCounts = new Map<string, number>();
    members.forEach(element => {
      const type = element.element_type || 'unclassified_candidate';
      typeCounts.set(type, (typeCounts.get(type) ?? 0) + 1);
    });

    const representativeType = Array.from(typeCounts.entries())
      .sort((a, b) => b[1] - a[1])[0][0];

    return {
      id: `group-${groupIndex}`,
      element_type: representativeType,
      bbox: [x0, y0, x1, y1],
      center: [(x0 + x1) / 2, (y0 + y1) / 2],
      width: x1 - x0,
      height: y1 - y0,
      memberCount: members.length,
      verificationRequired: members.some(e => e.verification_required),
    };
  });
}

function Footprint({ element }: { element: DisplayElement }) {
  const [cx, cy] = element.center;
  return (
    <mesh position={[cx, -cy, 0.5]}>
      <planeGeometry args={[element.width, element.height]} />
      <meshBasicMaterial
        color={elementColor(element.element_type)}
        transparent
        opacity={element.memberCount > 1 ? 0.58 : 0.72}
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}

export function PdfPage({ url }: { url: string }) {
  const texture = useLoader(THREE.TextureLoader, url);
  const image = texture.image as HTMLImageElement;
  const pageWidth = image.width / PDF_PREVIEW_SCALE;
  const pageHeight = image.height / PDF_PREVIEW_SCALE;

  return (
    <mesh position={[pageWidth / 2, -pageHeight / 2, -1]} renderOrder={-1}>
      <planeGeometry args={[pageWidth, pageHeight]} />
      <meshBasicMaterial map={texture} toneMapped={false} />
    </mesh>
  );
}

function ViewerContents({
  previewUrl,
  elements,
}: {
  previewUrl: string;
  elements: DisplayElement[];
}) {
  return (
    <Suspense fallback={null}>
      <Bounds fit clip observe margin={1.04}>
        <group>
          <PdfPage url={previewUrl} />
          {elements.map(element => (
            <Footprint key={element.id} element={element} />
          ))}
        </group>
      </Bounds>
    </Suspense>
  );
}

export default function SceneViewer({
  projectId,
  drawingId,
}: {
  projectId: string;
  drawingId: string;
}) {
  const [scene, setScene] = useState<SceneData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [previewError, setPreviewError] = useState('');
  const [groupedView, setGroupedView] = useState(true);
  const [lod, setLod] = useState<LOD>('100');

  useEffect(() => {
    if (!projectId || !drawingId) return;
    const controller = new AbortController();

    async function loadScene() {
      setLoading(true);
      setError('');
      setPreviewError('');
      setScene(null);
      try {
        const response = await fetch(
          `${API}/projects/${projectId}/phase6/scene/${drawingId}?page_number=1`,
          { signal: controller.signal },
        );
        const result = await response.json();
        if (!response.ok) {
          throw new Error(
            typeof result.detail === 'string'
              ? result.detail
              : 'Could not load the Phase 6 scene.',
          );
        }
        setScene(result);
      } catch (err) {
        if (err instanceof Error && err.name !== 'AbortError') setError(err.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    loadScene();
    return () => controller.abort();
  }, [projectId, drawingId]);

  const groupedElements = useMemo(
    () => buildGroupedElements(scene?.elements ?? []),
    [scene],
  );

  const rawElements = useMemo(
    () => (scene?.elements ?? []).map((element, index): DisplayElement => ({
      id: element.element_id || `candidate-${index}`,
      element_type: element.element_type,
      bbox: element.bbox_pdf_points,
      center: element.plan_geometry.center,
      width: element.plan_geometry.width,
      height: element.plan_geometry.height,
      memberCount: 1,
      verificationRequired: element.verification_required,
    })),
    [scene],
  );

  const displayedElements = groupedView ? groupedElements : rawElements;
  const previewUrl = scene
    ? `${API}/projects/${projectId}/phase6/preview/${drawingId}?page_number=${scene.page_number}`
    : '';

  if (loading) return <div className="message">Loading drawing scene...</div>;
  if (error) return <div className="message">{error}</div>;
  if (!scene) return <div className="message">Select a drawing to view its scene.</div>;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 18,
        color: '#cbd5e1', fontSize: 13,
      }}>
        <span>Original candidates: {scene.summary.candidate_count}</span>
        <span>Grouped footprints: {groupedElements.length}</span>
        <span>Displayed: {displayedElements.length}</span>
        <span>Approved BIM: {scene.summary.approved_bim_element_count}</span>
      </div>

      <div style={{
        display: 'flex', flexWrap: 'wrap', alignItems: 'center',
        justifyContent: 'space-between', gap: 12,
      }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setGroupedView(true)} style={controlStyle(groupedView)}>
            Grouped geometry
          </button>
          <button onClick={() => setGroupedView(false)} style={controlStyle(!groupedView)}>
            Individual candidates
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: '#94a3b8', fontSize: 12 }}>LOD target</span>
          {(['100', '200', '300', '400', '500'] as LOD[]).map(level => (
            <button key={level} onClick={() => setLod(level)} style={controlStyle(lod === level)}>
              {level}
            </button>
          ))}
        </div>
      </div>

      <div style={{
        padding: '12px 14px', border: '1px solid #475569',
        borderRadius: 10, background: '#172033',
        color: '#cbd5e1', fontSize: 12,
      }}>
        <strong>LOD {lod} target selected.</strong>{' '}
        This drawing currently contains 2D candidate footprints only.
        Actual 3D geometry for this LOD has not yet been generated.
      </div>

      {previewError && (
        <div style={{
          padding: '10px 12px', border: '1px solid #b45309',
          borderRadius: 8, color: '#fbbf24', fontSize: 12,
        }}>
          PDF background could not be loaded: {previewError}
        </div>
      )}

      <div style={{
        height: 560, width: '100%', overflow: 'hidden',
        borderRadius: 14, border: '1px solid #334155',
        background: '#0b1220',
      }}>
        <Canvas
          orthographic
          camera={{ position: [0, 0, 1500], zoom: 1, near: 0.1, far: 5000 }}
        >
          <color attach="background" args={['#0b1220']} />
          <ViewerContents
            previewUrl={previewUrl}
            elements={displayedElements}
          />
          <OrbitControls makeDefault enableRotate={false} enablePan enableZoom />
        </Canvas>
      </div>

      <div style={{ color: '#94a3b8', fontSize: 12 }}>
        {scene.drawing_filename} · Page {scene.page_number} ·
        Page-local PDF coordinates · PDF underlay + 2D candidate overlay
      </div>

      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: 16,
        color: '#cbd5e1', fontSize: 12,
      }}>
        {[
          ['#60a5fa', 'Walls'],
          ['#f59e0b', 'Stairs'],
          ['#a78bfa', 'Equipment'],
          ['#22c1c3', 'Tank candidates'],
          ['#94a3b8', 'Unclassified'],
        ].map(([color, label]) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{
              width: 10, height: 10, borderRadius: 3, background: color,
            }} />
            {label}
          </span>
        ))}
      </div>

      <details style={{ color: '#cbd5e1', fontSize: 13 }}>
        <summary>Scene limitations and verification</summary>
        <ul>
          {scene.limitations.map((item, index) => <li key={index}>{item}</li>)}
        </ul>
        <p>
          Source-line grouping consolidates related candidate pairs.
          A group is not proof of one physical object or a closed boundary.
          All candidates still require engineering verification.
        </p>
      </details>
    </div>
  );
}

function controlStyle(active: boolean): React.CSSProperties {
  return {
    padding: '8px 12px',
    borderRadius: 7,
    border: `1px solid ${active ? '#38bdf8' : '#475569'}`,
    background: active ? '#164e63' : '#172033',
    color: '#e2e8f0',
    cursor: 'pointer',
    fontSize: 12,
  };
}
