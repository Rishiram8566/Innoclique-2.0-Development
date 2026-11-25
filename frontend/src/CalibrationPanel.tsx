import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { Line, OrbitControls } from '@react-three/drei';
import { PdfPage } from './SceneViewer';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const PRINT_MM_PER_PDF_POINT = 25.4 / 72;
const UNIT_MM: Record<string, number> = { mm: 1, cm: 10, m: 1000, in: 25.4, ft: 304.8 };
const MIN_TEXT = 20;

type CalibrationRecord = {
  status: string;
  millimetres_per_pdf_point: number;
  source_reference: string;
  known_distance: number;
  known_distance_unit: string;
};

type SnapResult = {
  snapped: boolean;
  point_pdf: number[];
  distance_pdf_points: number | null;
  source_line: { path_index: number; item_index: number } | null;
};

type Level = {
  level_key: string;
  label: string;
  elevation_mm: number;
  printed_forms: string[];
  occurrence_count: number;
  occurrences_on_this_drawing: number;
};

type Placement = {
  placement_id: string;
  status: 'active' | 'revoked';
  reviewer: string;
  created_at: string;
  base_level: Level;
  top_level: Level | null;
  storey_height_mm: number | null;
  reference_point: { point_pdf: number[]; label: string };
  revocation: { reviewer: string; reason: string } | null;
};

type PlacementOptions = {
  height_report_available: boolean;
  levels: Level[];
  suggested_base_level_key: string | null;
  suggested_top_level_key: string | null;
  active_placement: Placement | null;
  placements_on_page: Placement[];
};

type Mode = 'scale' | 'reference';

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => (d as { msg?: string })?.msg ?? String(d)).join('; ');
  return '';
}

async function apiJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
  return body as T;
}

function printScale(mmPerPoint: number) {
  return `≈ 1:${Math.round(mmPerPoint / PRINT_MM_PER_PDF_POINT).toLocaleString()} at the PDF's print size`;
}

function levelText(level: Level) {
  const sign = level.elevation_mm >= 0 ? '+' : '−';
  const local = level.occurrences_on_this_drawing
    ? ` · ${level.occurrences_on_this_drawing} on this drawing`
    : ' · not on this drawing';
  return `${level.label} ${sign}${Math.abs(level.elevation_mm / 1000).toFixed(2)} m${local}`;
}

const MARKER_COLORS = ['#22c55e', '#f97316'];
const REFERENCE_COLOR = '#38bdf8';

function Marker({ point, color }: { point: number[]; color: string }) {
  return (
    <group position={[point[0], -point[1], 2]}>
      <mesh>
        <ringGeometry args={[5, 8, 32]} />
        <meshBasicMaterial color={color} />
      </mesh>
      <mesh>
        <circleGeometry args={[1.2, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>
    </group>
  );
}

export default function CalibrationPanel({
  projectId,
  drawingId,
  onCalibrated,
}: {
  projectId: string;
  drawingId: string;
  onCalibrated: () => void;
}) {
  const [existing, setExisting] = useState<CalibrationRecord | null>(null);
  const [options, setOptions] = useState<PlacementOptions | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [mode, setMode] = useState<Mode>('scale');
  const [snapping, setSnapping] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Scale calibration
  const [points, setPoints] = useState<SnapResult[]>([]);
  const [knownDistance, setKnownDistance] = useState('');
  const [unit, setUnit] = useState('mm');
  const [reference, setReference] = useState('');
  const [replaceOk, setReplaceOk] = useState(false);

  // Plan placement
  const [refPoint, setRefPoint] = useState<SnapResult | null>(null);
  const [refLabel, setRefLabel] = useState('');
  const [baseKey, setBaseKey] = useState('');
  const [topKey, setTopKey] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [justification, setJustification] = useState('');
  const [crossJustification, setCrossJustification] = useState('');
  const [revokeReason, setRevokeReason] = useState('');

  const base = `${API}/projects/${projectId}`;

  const load = useCallback(async () => {
    setLoaded(false);
    try {
      const response = await fetch(`${base}/scale-calibration`);
      if (response.status === 404) {
        setExisting(null);
      } else {
        const body = await response.json();
        if (!response.ok) throw new Error(formatDetail(body?.detail) || 'Could not read calibration.');
        const record = body?.calibrations?.[drawingId]?.[String(PAGE_NUMBER)];
        setExisting(record?.status === 'calibrated' ? record : null);
      }
      const opts = await apiJson<PlacementOptions>(
        `${base}/geometry/stage5-4/placement-options?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`,
      );
      setOptions(opts);
      setBaseKey(opts.suggested_base_level_key ?? '');
      setTopKey(opts.suggested_top_level_key ?? '');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load page data.');
    } finally {
      setLoaded(true);
    }
  }, [base, drawingId]);

  useEffect(() => {
    setPoints([]);
    setRefPoint(null);
    setMessage('');
    setError('');
    setReplaceOk(false);
    setMode('scale');
    load();
  }, [load]);

  async function snap(x: number, y: number): Promise<SnapResult | null> {
    if (snapping) return null;
    setSnapping(true);
    setError('');
    try {
      const query = `x=${x.toFixed(3)}&y=${y.toFixed(3)}&page_number=${PAGE_NUMBER}&radius=8`;
      return await apiJson<SnapResult>(`${base}/drawings/${drawingId}/snap-point?${query}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Snapping failed.');
      return null;
    } finally {
      setSnapping(false);
    }
  }

  async function onPagePoint(x: number, y: number) {
    const result = await snap(x, y);
    if (!result) return;
    if (mode === 'scale') {
      setPoints(previous => (previous.length >= 2 ? [result] : [...previous, result]));
    } else {
      setRefPoint(result);
    }
  }

  // ---- scale ------------------------------------------------------------------
  const [a, b] = points;
  const distancePdf = a && b
    ? Math.hypot(b.point_pdf[0] - a.point_pdf[0], b.point_pdf[1] - a.point_pdf[1])
    : 0;
  const knownMm = Number(knownDistance) * (UNIT_MM[unit] ?? NaN);
  const mmPerPoint = distancePdf > 0 && knownMm > 0 ? knownMm / distancePdf : null;

  const scaleMissing: string[] = [];
  if (points.length < 2) scaleMissing.push(`click point ${points.length === 0 ? 'A and B' : 'B'} on the drawing`);
  if (points.length === 2 && distancePdf <= 0) scaleMissing.push('pick two different points');
  if (!(knownMm > 0)) scaleMissing.push('enter the real distance between A and B');
  if (reference.trim().length < 3) scaleMissing.push('say where the distance comes from');
  if (existing && !replaceOk) scaleMissing.push('tick "replace the existing calibration"');

  async function saveCalibration() {
    if (!a || !b) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await apiJson(`${base}/scale-calibration/calibrate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          drawing_id: drawingId,
          page_number: PAGE_NUMBER,
          point_a_pdf: a.point_pdf,
          point_b_pdf: b.point_pdf,
          known_distance: Number(knownDistance),
          unit,
          source_reference: reference.trim(),
        }),
      });
      setMessage(`Page calibrated: ${mmPerPoint?.toFixed(3)} mm per PDF point (${mmPerPoint ? printScale(mmPerPoint) : ''}).`);
      setPoints([]);
      setReplaceOk(false);
      await load();
      onCalibrated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Calibration failed.');
    } finally {
      setBusy(false);
    }
  }

  // ---- placement ----------------------------------------------------------------
  const levels = options?.levels ?? [];
  const baseLevel = levels.find(l => l.level_key === baseKey) ?? null;
  const topChoices = useMemo(
    () => (baseLevel ? levels.filter(l => l.label === baseLevel.label && l.elevation_mm > baseLevel.elevation_mm) : []),
    [levels, baseLevel],
  );
  const topLevel = topChoices.find(l => l.level_key === topKey) ?? null;
  const storeyMm = baseLevel && topLevel ? topLevel.elevation_mm - baseLevel.elevation_mm : null;
  const baseOnDrawing = (baseLevel?.occurrences_on_this_drawing ?? 0) > 0;
  const active = options?.active_placement ?? null;

  const orderedLevels = useMemo(
    () => [...levels].sort((x, y) =>
      (y.occurrences_on_this_drawing > 0 ? 1 : 0) - (x.occurrences_on_this_drawing > 0 ? 1 : 0)
      || x.label.localeCompare(y.label) || x.elevation_mm - y.elevation_mm),
    [levels],
  );

  const placementMissing: string[] = [];
  if (active) placementMissing.push('revoke the current placement first');
  if (!baseLevel) placementMissing.push('choose the base level');
  if (!refPoint) placementMissing.push('click the shared reference point (switch to "Reference point")');
  if (refLabel.trim().length < 2) placementMissing.push('name the reference point (e.g. Grid A/1)');
  if (reviewer.trim().length < 2) placementMissing.push('enter your name');
  if (justification.trim().length < MIN_TEXT) placementMissing.push(`write a justification of at least ${MIN_TEXT} characters`);
  if (baseLevel && !baseOnDrawing && crossJustification.trim().length < MIN_TEXT) {
    placementMissing.push('explain why a level not shown on this drawing applies');
  }

  async function savePlacement() {
    if (!refPoint || !baseLevel) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await apiJson(`${base}/geometry/stage5-4/placements`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          drawing_id: drawingId,
          page_number: PAGE_NUMBER,
          base_level_key: baseLevel.level_key,
          top_level_key: topLevel?.level_key ?? null,
          reference_point_pdf: refPoint.point_pdf,
          reference_label: refLabel.trim(),
          reference_snapped_line: refPoint.source_line,
          reviewer: reviewer.trim(),
          justification: justification.trim(),
          cross_drawing_justification: baseOnDrawing ? null : crossJustification.trim(),
        }),
      });
      setMessage(
        `Plan placed at ${levelText(baseLevel)}` +
        (storeyMm ? `; storey height ${Math.round(storeyMm).toLocaleString()} mm.` : '.'),
      );
      setRefPoint(null);
      setJustification('');
      setCrossJustification('');
      await load();
      onCalibrated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Placement failed.');
    } finally {
      setBusy(false);
    }
  }

  async function revokeActive() {
    if (!active) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await apiJson(`${base}/geometry/stage5-4/placements/${active.placement_id}/revoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: reviewer.trim(), reason: revokeReason.trim() }),
      });
      setMessage('Placement revoked. Full-height walls on this plan are withheld until it is placed again.');
      setRevokeReason('');
      await load();
      onCalibrated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Revoke failed.');
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return <div className="message">Loading calibration...</div>;

  const previewUrl = `${base}/phase6/preview/${drawingId}?page_number=${PAGE_NUMBER}`;
  const shownReference = refPoint?.point_pdf ?? active?.reference_point.point_pdf ?? null;

  return (
    <div style={{ display: 'grid', gap: 14, color: '#cbd5e1', fontSize: 13 }}>
      <div style={banner(existing ? '#475569' : '#b45309')}>
        {existing ? (
          <>
            <strong>Scale:</strong> {existing.millimetres_per_pdf_point.toFixed(3)} mm per PDF point
            ({printScale(existing.millimetres_per_pdf_point)}) from “{existing.source_reference}”
            ({existing.known_distance} {existing.known_distance_unit}). Still requires verification.
          </>
        ) : (
          <>
            <strong>No scale yet.</strong> Walls on this page can't be built in millimetres until two
            points and the real distance between them are given.
          </>
        )}
      </div>
      <div style={banner(active ? '#475569' : '#b45309')}>
        {active ? (
          <>
            <strong>Placed:</strong> base {levelText(active.base_level)}
            {active.top_level
              ? `; top ${levelText(active.top_level)}; storey height ${Math.round(active.storey_height_mm ?? 0).toLocaleString()} mm`
              : '; no top level (full-height walls unavailable)'}
            ; reference point “{active.reference_point.label}”; by {active.reviewer}.
          </>
        ) : (
          <>
            <strong>Not placed.</strong> To stack this plan into the building, choose its level and click a
            reference point that appears on every plan (for example a grid intersection).
          </>
        )}
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: '#94a3b8', fontSize: 12 }}>Clicks on the drawing set:</span>
        <button onClick={() => setMode('scale')} style={modeButton(mode === 'scale')}>Scale points A / B</button>
        <button onClick={() => setMode('reference')} style={modeButton(mode === 'reference')}>Reference point</button>
        <span style={{ color: '#94a3b8', fontSize: 12 }}>
          Clicks snap to the nearest drawn line end within 8 PDF points. Scroll to zoom, right-drag to pan.
        </span>
      </div>

      <div style={{
        height: 520, width: '100%', overflow: 'hidden', borderRadius: 14,
        border: '1px solid #334155', background: '#0b1220',
        cursor: snapping ? 'progress' : 'crosshair',
      }}>
        <Canvas orthographic camera={{ position: [0, 0, 1500], zoom: 1, near: 0.1, far: 5000 }}>
          <color attach="background" args={['#0b1220']} />
          <Suspense fallback={null}>
            <PdfPage url={previewUrl} onPagePoint={onPagePoint} />
            {points.map((point, index) => (
              <Marker key={index} point={point.point_pdf} color={MARKER_COLORS[index]} />
            ))}
            {a && b && (
              <Line
                points={[[a.point_pdf[0], -a.point_pdf[1], 1.5], [b.point_pdf[0], -b.point_pdf[1], 1.5]]}
                color="#f97316"
                lineWidth={2}
              />
            )}
            {shownReference && <Marker point={shownReference} color={REFERENCE_COLOR} />}
          </Suspense>
          <OrbitControls makeDefault enableRotate={false} enablePan enableZoom />
        </Canvas>
      </div>

      {mode === 'scale' ? (
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gap: 4, fontSize: 12 }}>
            {[0, 1].map(index => {
              const point = points[index];
              return (
                <span key={index}>
                  <strong style={{ color: MARKER_COLORS[index] }}>{index === 0 ? 'A' : 'B'}:</strong>{' '}
                  {point ? describePoint(point) : 'not picked yet'}
                </span>
              );
            })}
            {distancePdf > 0 && <span>Distance A–B on the page: {distancePdf.toFixed(3)} PDF points</span>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: 12 }}>
            <label>
              Real distance between A and B
              <input type="number" min="0" step="any" value={knownDistance}
                onChange={e => setKnownDistance(e.target.value)} placeholder="e.g. 8400" />
            </label>
            <label>
              Unit
              <select value={unit} onChange={e => setUnit(e.target.value)}>
                {Object.keys(UNIT_MM).map(u => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
          </div>
          <label>
            Where the distance comes from
            <input value={reference} onChange={e => setReference(e.target.value)}
              placeholder="e.g. dimension text 8400 between grid lines 1 and 2" />
          </label>
          {mmPerPoint && (
            <div style={banner('#475569')}>
              Result: {mmPerPoint.toFixed(3)} mm per PDF point, {printScale(mmPerPoint)}. Compare this with the
              scale in the title block before saving.
            </div>
          )}
          {existing && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input type="checkbox" checked={replaceOk} onChange={e => setReplaceOk(e.target.checked)}
                style={{ width: 'auto', padding: 0 }} />
              Replace the existing calibration for this page (walls already generated must be regenerated)
            </label>
          )}
          <div>
            <button className="primary" disabled={busy || scaleMissing.length > 0} onClick={saveCalibration}>
              {busy ? 'Saving…' : 'Save page calibration'}
            </button>
          </div>
          {scaleMissing.length > 0 && <small style={{ color: '#fbbf24' }}>To save: {scaleMissing.join('; ')}.</small>}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12 }}>
          {!options?.height_report_available && (
            <div style={banner('#b45309')}>The Stage 5.3.4 level register has not been generated for this project.</div>
          )}
          <span style={{ fontSize: 12 }}>
            <strong style={{ color: REFERENCE_COLOR }}>Reference point:</strong>{' '}
            {refPoint ? describePoint(refPoint) : active ? `current: “${active.reference_point.label}”` : 'click it on the drawing'}
          </span>
          <label>
            Reference point name (use the same point on every plan)
            <input value={refLabel} onChange={e => setRefLabel(e.target.value)} placeholder="e.g. Grid A/1 intersection" />
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <label>
              Base level (the slab this plan sits on)
              <select value={baseKey} onChange={e => { setBaseKey(e.target.value); setTopKey(''); }}>
                <option value="">Choose a level</option>
                {orderedLevels.map(level => (
                  <option key={level.level_key} value={level.level_key}>{levelText(level)}</option>
                ))}
              </select>
            </label>
            <label>
              Top level (next slab up; optional)
              <select value={topKey} onChange={e => setTopKey(e.target.value)} disabled={!baseLevel}>
                <option value="">None — no full-height walls on this plan</option>
                {topChoices.map(level => (
                  <option key={level.level_key} value={level.level_key}>{levelText(level)}</option>
                ))}
              </select>
            </label>
          </div>
          {storeyMm !== null && (
            <div style={banner('#475569')}>
              Storey height {Math.round(storeyMm).toLocaleString()} mm. Walls you later confirm as full-height on
              this plan get this height. Levels were suggested from this drawing's annotations; check them.
            </div>
          )}
          {baseLevel && !baseOnDrawing && (
            <label>
              Why this level applies (it is not annotated on this drawing)
              <textarea value={crossJustification} onChange={e => setCrossJustification(e.target.value)}
                rows={2} style={textareaStyle} />
            </label>
          )}
          <label>
            Reviewer name
            <input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="Name of the engineer placing the plan" />
          </label>
          <label>
            Justification ({justification.trim().length}/{MIN_TEXT} characters minimum)
            <textarea value={justification} onChange={e => setJustification(e.target.value)} rows={2}
              placeholder="e.g. Plan title and SSL annotations show this floor at SSL −7.60; next slab SSL −3.40."
              style={textareaStyle} />
          </label>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button className="primary" disabled={busy || placementMissing.length > 0} onClick={savePlacement}>
              {busy ? 'Saving…' : 'Save plan placement'}
            </button>
          </div>
          {placementMissing.length > 0 && (
            <small style={{ color: '#fbbf24' }}>To save: {placementMissing.join('; ')}.</small>
          )}
          {active && (
            <div style={{ display: 'grid', gap: 8, padding: 12, border: '1px solid #29384a', borderRadius: 10 }}>
              <span>Revoke the current placement (record kept for audit; uses the reviewer name above)</span>
              <textarea value={revokeReason} onChange={e => setRevokeReason(e.target.value)} rows={2}
                placeholder={`Reason (${MIN_TEXT}+ characters)`} style={textareaStyle} />
              <div>
                <button
                  disabled={busy || reviewer.trim().length < 2 || revokeReason.trim().length < MIN_TEXT}
                  onClick={revokeActive}
                  style={secondaryButton(busy || reviewer.trim().length < 2 || revokeReason.trim().length < MIN_TEXT)}
                >
                  Revoke placement
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {message && <div className="message">{message}</div>}
      {error && <div className="message" style={{ color: '#fbbf24' }}>{error}</div>}
    </div>
  );
}

function describePoint(point: SnapResult) {
  const position = `(${point.point_pdf[0].toFixed(2)}, ${point.point_pdf[1].toFixed(2)}) pt`;
  return point.snapped
    ? `${position} · snapped to drawn line (${point.source_line?.path_index}, ${point.source_line?.item_index}), moved ${point.distance_pdf_points} pt`
    : `${position} · no line end nearby — raw click position`;
}

const textareaStyle: React.CSSProperties = {
  width: '100%',
  background: '#0d1723',
  color: '#eaf0f8',
  border: '1px solid #344357',
  borderRadius: 9,
  padding: '10px 12px',
  font: 'inherit',
  resize: 'vertical',
};

function banner(border: string): React.CSSProperties {
  return {
    padding: '10px 12px',
    border: `1px solid ${border}`,
    borderRadius: 8,
    background: '#172033',
    fontSize: 12,
    lineHeight: 1.6,
  };
}

function modeButton(active: boolean): React.CSSProperties {
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

function secondaryButton(disabled: boolean): React.CSSProperties {
  return {
    padding: '10px 14px',
    borderRadius: 9,
    border: '1px solid #475569',
    background: '#172033',
    color: '#e2e8f0',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.45 : 1,
    fontWeight: 700,
  };
}
