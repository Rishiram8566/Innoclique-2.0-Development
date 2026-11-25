import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';
import { PdfPage } from './SceneViewer';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const MIN_TEXT = 20;

type VerificationStatus = 'unverified' | 'confirmed' | 'rejected';

type WallCandidate = {
  element_id: string;
  candidate_index: number;
  bbox_pdf_points: number[];
  length_pdf_points: number | null;
  thickness_pdf_points: number | null;
  length_mm_provisional: number | null;
  thickness_mm_provisional: number | null;
  confidence: number | null;
  geometry_validation_status: string;
  shares_source_line_with: string[];
  height_verification: {
    status: VerificationStatus;
    verification_id?: string;
    height_mm?: number | null;
  };
};

type HeightEvidence = {
  evidence_id: string;
  drawing_id: string;
  drawing_title: string | null;
  page_number: number;
  text: string;
  on_this_drawing: boolean;
};

type HeightCandidate = {
  candidate_id: string;
  component_words: string[];
  height_mm: number | null;
  height_range_mm: number[] | null;
  occurrence_count: number;
  occurrences_on_this_drawing: number;
  confirmable: boolean;
  evidence: HeightEvidence[];
};

type Verification = {
  verification_id: string;
  created_at: string;
  status: 'active' | 'revoked';
  decision: 'confirmed' | 'rejected';
  reviewer: string;
  justification: string;
  element_ids: string[];
  height_candidate: { component_words: string[]; height_mm: number | null };
  revocation: { reviewer: string; reason: string; revoked_at: string } | null;
};

type PageView = {
  drawing_title: string | null;
  calibration: {
    status: string;
    millimetres_per_pdf_point: number | null;
    source_reference?: string;
  };
  height_report_available: boolean;
  height_candidates: HeightCandidate[];
  summary: {
    wall_candidates: number;
    height_confirmed: number;
    height_rejected: number;
    unverified: number;
  };
  candidates: WallCandidate[];
  verifications_on_page: Verification[];
  limitations: string[];
};

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map(item => {
        if (item && typeof item === 'object' && 'msg' in item) {
          const loc = (item as { loc?: unknown[] }).loc;
          const field = Array.isArray(loc) ? String(loc[loc.length - 1]) : '';
          return `${field}: ${(item as { msg: string }).msg}`;
        }
        return String(item);
      })
      .join('; ');
  }
  return '';
}

async function apiJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
  }
  return body as T;
}

function mm(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value).toLocaleString()} mm`;
}

const STATUS_COLOR: Record<VerificationStatus, string> = {
  unverified: '#60a5fa',
  confirmed: '#22c55e',
  rejected: '#ef4444',
};
const SELECTED_COLOR = '#f97316';
const OVERLAP_COLOR = '#facc15';

function CandidateMesh({
  candidate,
  color,
  opacity,
  onToggle,
  onHover,
}: {
  candidate: WallCandidate;
  color: string;
  opacity: number;
  onToggle: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  const [x0, y0, x1, y1] = candidate.bbox_pdf_points;
  const width = Math.max(Math.abs(x1 - x0), 3);
  const height = Math.max(Math.abs(y1 - y0), 3);
  return (
    <mesh
      position={[(x0 + x1) / 2, -(y0 + y1) / 2, 0.5]}
      onClick={event => {
        event.stopPropagation();
        onToggle(candidate.element_id);
      }}
      onPointerOver={event => {
        event.stopPropagation();
        document.body.style.cursor = 'pointer';
        onHover(candidate.element_id);
      }}
      onPointerOut={() => {
        document.body.style.cursor = '';
        onHover(null);
      }}
    >
      <planeGeometry args={[width, height]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={THREE.DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}

export default function HeightVerificationPanel({
  projectId,
  drawingId,
}: {
  projectId: string;
  drawingId: string;
}) {
  const [view, setView] = useState<PageView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<string | null>(null);
  const [heightId, setHeightId] = useState('');
  const [cited, setCited] = useState<Set<string>>(new Set());
  const [reviewer, setReviewer] = useState('');
  const [justification, setJustification] = useState('');
  const [crossJustification, setCrossJustification] = useState('');
  const [revokeId, setRevokeId] = useState('');
  const [revokeReason, setRevokeReason] = useState('');

  const base = `${API}/projects/${projectId}/geometry/stage5-3-5`;

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await apiJson<PageView>(
        `${base}/wall-candidates?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`,
      );
      setView(result);
    } catch (err) {
      setView(null);
      setError(err instanceof Error ? err.message : 'Could not load wall candidates.');
    } finally {
      setLoading(false);
    }
  }, [base, drawingId]);

  useEffect(() => {
    setSelected(new Set());
    setNotice('');
    setHeightId('');
    load();
  }, [load]);

  const heightCandidate = useMemo(
    () => view?.height_candidates.find(c => c.candidate_id === heightId) ?? null,
    [view, heightId],
  );

  // Default to the confirmable height candidate best supported on this drawing.
  useEffect(() => {
    if (!view || heightId) return;
    const ranked = [...view.height_candidates]
      .filter(c => c.confirmable)
      .sort((a, b) => b.occurrences_on_this_drawing - a.occurrences_on_this_drawing);
    if (ranked[0]) setHeightId(ranked[0].candidate_id);
  }, [view, heightId]);

  const onDrawingEvidence = heightCandidate?.evidence.filter(e => e.on_this_drawing) ?? [];
  const otherEvidence = heightCandidate?.evidence.filter(e => !e.on_this_drawing) ?? [];
  const citesThisDrawing = onDrawingEvidence.some(e => cited.has(e.evidence_id));

  useEffect(() => {
    if (!heightCandidate) {
      setCited(new Set());
      return;
    }
    const local = heightCandidate.evidence.filter(e => e.on_this_drawing);
    setCited(new Set(
      local.length
        ? local.map(e => e.evidence_id)
        : heightCandidate.evidence.slice(0, 1).map(e => e.evidence_id),
    ));
  }, [heightCandidate]);

  const byId = useMemo(
    () => new Map((view?.candidates ?? []).map(c => [c.element_id, c])),
    [view],
  );

  const confirmedIds = useMemo(
    () => new Set(
      (view?.candidates ?? [])
        .filter(c => c.height_verification.status === 'confirmed')
        .map(c => c.element_id),
    ),
    [view],
  );

  const overlapsSelection = useMemo(() => {
    const result = new Set<string>();
    selected.forEach(id => byId.get(id)?.shares_source_line_with.forEach(o => result.add(o)));
    return result;
  }, [selected, byId]);

  const selectedList = Array.from(selected).map(id => byId.get(id)).filter(Boolean) as WallCandidate[];
  const internalOverlap = selectedList.filter(c =>
    c.shares_source_line_with.some(o => selected.has(o)),
  );
  const overlapConfirmed = selectedList.filter(c =>
    c.shares_source_line_with.some(o => confirmedIds.has(o)),
  );
  const alreadyVerified = selectedList.filter(c => c.height_verification.status !== 'unverified');

  const formReady =
    selected.size > 0 &&
    heightCandidate !== null &&
    cited.size > 0 &&
    reviewer.trim().length >= 2 &&
    justification.trim().length >= MIN_TEXT &&
    (citesThisDrawing || crossJustification.trim().length >= MIN_TEXT) &&
    alreadyVerified.length === 0;

  const canConfirm =
    formReady &&
    heightCandidate?.confirmable === true &&
    internalOverlap.length === 0 &&
    overlapConfirmed.length === 0;

  // Say exactly why Confirm is disabled instead of leaving a greyed-out button.
  const missing: string[] = [];
  if (selected.size === 0) missing.push('select at least one wall candidate');
  if (!heightCandidate) missing.push('choose a height candidate');
  else if (!heightCandidate.confirmable) missing.push('choose a single height (a range cannot be confirmed)');
  if (heightCandidate && cited.size === 0) missing.push('cite at least one source annotation');
  if (reviewer.trim().length < 2) missing.push('enter your name in Reviewer name');
  if (justification.trim().length < MIN_TEXT) {
    missing.push(`write a justification of at least ${MIN_TEXT} characters`);
  }
  if (heightCandidate && cited.size > 0 && !citesThisDrawing && crossJustification.trim().length < MIN_TEXT) {
    missing.push('add a cross-drawing justification');
  }

  function toggle(id: string) {
    setSelected(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleCited(id: string) {
    setCited(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function submit(decision: 'confirmed' | 'rejected') {
    if (!heightCandidate) return;
    setBusy(true);
    setNotice('');
    setError('');
    try {
      const result = await apiJson<{ verification: Verification }>(`${base}/height-verifications`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          height_candidate_id: heightCandidate.candidate_id,
          drawing_id: drawingId,
          page_number: PAGE_NUMBER,
          element_ids: Array.from(selected),
          decision,
          reviewer: reviewer.trim(),
          justification: justification.trim(),
          cited_evidence_ids: Array.from(cited),
          cross_drawing_justification: citesThisDrawing ? null : crossJustification.trim(),
        }),
      });
      setNotice(
        `Recorded ${decision} verification ${result.verification.verification_id} ` +
        `for ${result.verification.element_ids.length} wall candidate(s).`,
      );
      setSelected(new Set());
      setJustification('');
      setCrossJustification('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Verification failed.');
    } finally {
      setBusy(false);
    }
  }

  async function revoke(verificationId: string) {
    setBusy(true);
    setNotice('');
    setError('');
    try {
      await apiJson(`${base}/height-verifications/${verificationId}/revoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: reviewer.trim(), reason: revokeReason.trim() }),
      });
      setNotice(`Revoked verification ${verificationId}. The record is kept for audit.`);
      setRevokeId('');
      setRevokeReason('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Revoke failed.');
    } finally {
      setBusy(false);
    }
  }

  if (loading && !view) return <div className="message">Loading wall candidates...</div>;
  if (!view) return <div className="message">{error || 'Select a drawing to verify.'}</div>;

  const previewUrl = `${API}/projects/${projectId}/phase6/preview/${drawingId}?page_number=${PAGE_NUMBER}`;
  const hoveredCandidate = hovered ? byId.get(hovered) : undefined;
  const heightLabel = heightCandidate
    ? `${heightCandidate.component_words.join(' ').toLowerCase()} ${mm(heightCandidate.height_mm)}`
    : '';

  return (
    <div style={{ display: 'grid', gap: 16, color: '#cbd5e1', fontSize: 13 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18 }}>
        <span>Wall candidates: {view.summary.wall_candidates}</span>
        <span style={{ color: STATUS_COLOR.confirmed }}>Height confirmed: {view.summary.height_confirmed}</span>
        <span style={{ color: STATUS_COLOR.rejected }}>Rejected: {view.summary.height_rejected}</span>
        <span>Unverified: {view.summary.unverified}</span>
      </div>

      <div style={bannerStyle(view.calibration.status === 'calibrated' ? '#475569' : '#b45309')}>
        {view.calibration.status === 'calibrated' ? (
          <>
            <strong>Page calibration:</strong>{' '}
            {view.calibration.millimetres_per_pdf_point?.toFixed(3)} mm per PDF point
            (provisional, requires verification). Lengths and thicknesses below use it.
          </>
        ) : (
          <>
            <strong>This page is not calibrated.</strong> Heights can still be verified,
            but Stage 5.4 cannot build metric meshes for this page until it is calibrated.
          </>
        )}
      </div>

      {!view.height_report_available && (
        <div style={bannerStyle('#b45309')}>
          Stage 5.3.4 height report has not been generated for this project.
        </div>
      )}

      {view.candidates.length === 0 ? (
        <div className="message">This page has no supported wall candidates to verify.</div>
      ) : (
        <>
          <div style={{
            height: 520, width: '100%', overflow: 'hidden',
            borderRadius: 14, border: '1px solid #334155', background: '#0b1220',
          }}>
            <Canvas orthographic camera={{ position: [0, 0, 1500], zoom: 1, near: 0.1, far: 5000 }}>
              <color attach="background" args={['#0b1220']} />
              <Suspense fallback={null}>
                <group>
                    <PdfPage url={previewUrl} />
                    {view.candidates.map(candidate => {
                      const isSelected = selected.has(candidate.element_id);
                      const overlaps = !isSelected && overlapsSelection.has(candidate.element_id);
                      const color = isSelected
                        ? SELECTED_COLOR
                        : overlaps
                          ? OVERLAP_COLOR
                          : STATUS_COLOR[candidate.height_verification.status];
                      const emphasis = isSelected || hovered === candidate.element_id;
                      return (
                        <CandidateMesh
                          key={candidate.element_id}
                          candidate={candidate}
                          color={color}
                          opacity={emphasis ? 0.9 : overlaps ? 0.75 : 0.5}
                          onToggle={toggle}
                          onHover={setHovered}
                        />
                      );
                    })}
                  </group>
              </Suspense>
              <OrbitControls makeDefault enableRotate={false} enablePan enableZoom />
            </Canvas>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12 }}>
            {[
              [STATUS_COLOR.unverified, 'Unverified'],
              [SELECTED_COLOR, 'Selected'],
              [OVERLAP_COLOR, 'Shares a source line with selection'],
              [STATUS_COLOR.confirmed, 'Height confirmed'],
              [STATUS_COLOR.rejected, 'Rejected'],
            ].map(([color, label]) => (
              <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: 3, background: color }} />
                {label}
              </span>
            ))}
            <span style={{ color: '#94a3b8' }}>
              {hoveredCandidate
                ? `#${hoveredCandidate.candidate_index} · ${mm(hoveredCandidate.length_mm_provisional)} long · ` +
                  `${mm(hoveredCandidate.thickness_mm_provisional)} thick`
                : 'Click candidates to select. Right-drag to pan, scroll to zoom.'}
            </span>
          </div>

          <div style={{ maxHeight: 260, overflow: 'auto', border: '1px solid #29384a', borderRadius: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead style={{ position: 'sticky', top: 0, background: '#121d2b' }}>
                <tr>
                  {['', '#', 'Length', 'Thickness', 'Shares line with', 'Geometry', 'Height status'].map(h => (
                    <th key={h} style={cellStyle(true)}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {view.candidates.map(candidate => {
                  const isSelected = selected.has(candidate.element_id);
                  const status = candidate.height_verification;
                  return (
                    <tr
                      key={candidate.element_id}
                      onClick={() => toggle(candidate.element_id)}
                      onMouseEnter={() => setHovered(candidate.element_id)}
                      onMouseLeave={() => setHovered(null)}
                      style={{
                        cursor: 'pointer',
                        background: isSelected ? '#3b2412' : hovered === candidate.element_id ? '#1a2a3d' : undefined,
                      }}
                    >
                      <td style={cellStyle()}>
                        <input
                          type="checkbox"
                          checked={isSelected}
                          readOnly
                          style={{ width: 'auto', padding: 0 }}
                        />
                      </td>
                      <td style={cellStyle()}>{candidate.candidate_index}</td>
                      <td style={cellStyle()}>{mm(candidate.length_mm_provisional)}</td>
                      <td style={cellStyle()}>{mm(candidate.thickness_mm_provisional)}</td>
                      <td style={cellStyle()}>
                        {candidate.shares_source_line_with.length
                          ? candidate.shares_source_line_with
                              .map(id => `#${byId.get(id)?.candidate_index ?? '?'}`)
                              .join(', ')
                          : '—'}
                      </td>
                      <td style={cellStyle()}>{candidate.geometry_validation_status.replace(/_/g, ' ')}</td>
                      <td style={{ ...cellStyle(), color: STATUS_COLOR[status.status] }}>
                        {status.status}{status.height_mm ? ` · ${mm(status.height_mm)}` : ''}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div style={{ display: 'grid', gap: 14, padding: 16, border: '1px solid #29384a', borderRadius: 12 }}>
            <strong style={{ color: '#eaf0f8' }}>Record a reviewer decision</strong>

            <label>
              Height candidate (from Stage 5.3.4; component named in the annotation text)
              <select value={heightId} onChange={e => setHeightId(e.target.value)}>
                <option value="">Select a height candidate</option>
                {view.height_candidates.map(candidate => (
                  <option key={candidate.candidate_id} value={candidate.candidate_id}>
                    {candidate.component_words.join(' ')} ·{' '}
                    {candidate.height_mm != null
                      ? mm(candidate.height_mm)
                      : `${candidate.height_range_mm?.join('–')} mm range (cannot be confirmed)`}
                    {' '}· {candidate.occurrences_on_this_drawing} on this drawing /{' '}
                    {candidate.occurrence_count} total
                  </option>
                ))}
              </select>
            </label>

            {heightCandidate && (
              <div style={{ display: 'grid', gap: 6 }}>
                <span style={{ fontSize: 12, color: '#a9b8c9' }}>
                  Cited source evidence ({cited.size} selected)
                </span>
                {onDrawingEvidence.length > 0 ? (
                  <div style={{ display: 'grid', gap: 4, maxHeight: 140, overflow: 'auto' }}>
                    {onDrawingEvidence.map(e => (
                      <label key={e.evidence_id} style={checkRowStyle}>
                        <input
                          type="checkbox"
                          checked={cited.has(e.evidence_id)}
                          onChange={() => toggleCited(e.evidence_id)}
                          style={{ width: 'auto', padding: 0 }}
                        />
                        “{e.text}” · this drawing, p{e.page_number} · {e.evidence_id}
                      </label>
                    ))}
                  </div>
                ) : (
                  <select
                    value={Array.from(cited)[0] ?? ''}
                    onChange={e => setCited(new Set(e.target.value ? [e.target.value] : []))}
                  >
                    {otherEvidence.map(e => (
                      <option key={e.evidence_id} value={e.evidence_id}>
                        “{e.text}” · {e.drawing_title ?? e.drawing_id} p{e.page_number}
                      </option>
                    ))}
                  </select>
                )}
                {!citesThisDrawing && (
                  <label>
                    Cross-drawing justification (required: the cited text is not on this drawing)
                    <textarea
                      value={crossJustification}
                      onChange={e => setCrossJustification(e.target.value)}
                      rows={2}
                      style={textareaStyle}
                    />
                  </label>
                )}
              </div>
            )}

            <label>
              Reviewer name
              <input value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="Name of the engineer verifying" />
            </label>

            <label>
              Justification ({justification.trim().length}/{MIN_TEXT} characters minimum)
              <textarea
                value={justification}
                onChange={e => setJustification(e.target.value)}
                rows={3}
                placeholder="Why these line pairs are this component, from the drawing (not from proximity alone)."
                style={textareaStyle}
              />
            </label>

            {internalOverlap.length > 0 && (
              <div style={bannerStyle('#b45309')}>
                Selected candidates {internalOverlap.map(c => `#${c.candidate_index}`).join(', ')} share
                a source line with each other. Keep only one per physical wall to confirm.
              </div>
            )}
            {overlapConfirmed.length > 0 && (
              <div style={bannerStyle('#b45309')}>
                {overlapConfirmed.map(c => `#${c.candidate_index}`).join(', ')} share a source line
                with an already confirmed wall. Revoke that verification first to confirm these.
              </div>
            )}
            {alreadyVerified.length > 0 && (
              <div style={bannerStyle('#b45309')}>
                {alreadyVerified.map(c => `#${c.candidate_index}`).join(', ')} already have a
                verification. Revoke it below before recording a new decision.
              </div>
            )}

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              <button className="primary" disabled={busy || !canConfirm} onClick={() => submit('confirmed')}>
                Confirm {selected.size} wall(s) as {heightLabel || '…'}
              </button>
              <button
                disabled={busy || !formReady}
                onClick={() => submit('rejected')}
                style={secondaryButtonStyle(busy || !formReady)}
              >
                Reject for {selected.size} candidate(s)
              </button>
              <button
                disabled={selected.size === 0}
                onClick={() => setSelected(new Set())}
                style={secondaryButtonStyle(selected.size === 0)}
              >
                Clear selection
              </button>
            </div>
            {!canConfirm && missing.length > 0 && (
              <small style={{ color: '#fbbf24' }}>
                To enable Confirm: {missing.join('; ')}.
              </small>
            )}
            <small style={{ color: '#8fa1b5' }}>
              Confirming records a human decision only. No geometry is generated here; Stage 5.4
              still checks calibration, source lines and mesh validity.
            </small>
          </div>
        </>
      )}

      {notice && <div className="message">{notice}</div>}
      {error && <div className="message" style={{ color: '#fbbf24' }}>{error}</div>}

      {view.verifications_on_page.length > 0 && (
        <div style={{ display: 'grid', gap: 8 }}>
          <strong style={{ color: '#eaf0f8' }}>Verification records on this page</strong>
          {view.verifications_on_page.map(record => (
            <div key={record.verification_id} style={{
              padding: 12, borderRadius: 10, border: '1px solid #29384a',
              opacity: record.status === 'active' ? 1 : 0.6, display: 'grid', gap: 6,
            }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
                <span style={{ color: record.decision === 'confirmed' ? STATUS_COLOR.confirmed : STATUS_COLOR.rejected }}>
                  {record.decision}
                </span>
                <span>
                  {record.height_candidate.component_words.join(' ')} {mm(record.height_candidate.height_mm)}
                </span>
                <span>{record.element_ids.length} wall candidate(s)</span>
                <span>by {record.reviewer}</span>
                <span>{new Date(record.created_at).toLocaleString()}</span>
                <span>{record.status}</span>
              </div>
              <small style={{ color: '#8fa1b5' }}>{record.justification}</small>
              {record.revocation && (
                <small style={{ color: '#fbbf24' }}>
                  Revoked by {record.revocation.reviewer}: {record.revocation.reason}
                </small>
              )}
              {record.status === 'active' && (
                revokeId === record.verification_id ? (
                  <div style={{ display: 'grid', gap: 8 }}>
                    <textarea
                      value={revokeReason}
                      onChange={e => setRevokeReason(e.target.value)}
                      rows={2}
                      placeholder={`Reason for revoking (${MIN_TEXT}+ characters). Uses the reviewer name above.`}
                      style={textareaStyle}
                    />
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button
                        disabled={busy || reviewer.trim().length < 2 || revokeReason.trim().length < MIN_TEXT}
                        onClick={() => revoke(record.verification_id)}
                        style={secondaryButtonStyle(busy || reviewer.trim().length < 2 || revokeReason.trim().length < MIN_TEXT)}
                      >
                        Revoke verification
                      </button>
                      <button onClick={() => setRevokeId('')} style={secondaryButtonStyle(false)}>Cancel</button>
                    </div>
                  </div>
                ) : (
                  <button onClick={() => setRevokeId(record.verification_id)} style={{ ...secondaryButtonStyle(false), justifySelf: 'start' }}>
                    Revoke…
                  </button>
                )
              )}
            </div>
          ))}
        </div>
      )}

      <details>
        <summary>Verification limitations</summary>
        <ul>{view.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </details>
    </div>
  );
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

const checkRowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  fontSize: 12,
};

function bannerStyle(border: string): React.CSSProperties {
  return {
    padding: '10px 12px',
    border: `1px solid ${border}`,
    borderRadius: 8,
    background: '#172033',
    fontSize: 12,
    lineHeight: 1.6,
  };
}

function cellStyle(header = false): React.CSSProperties {
  return {
    padding: '7px 10px',
    textAlign: 'left',
    borderBottom: '1px solid #1f2d3d',
    color: header ? '#94a3b8' : undefined,
    fontWeight: header ? 600 : 400,
    whiteSpace: 'nowrap',
  };
}

function secondaryButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    padding: '12px 16px',
    borderRadius: 9,
    border: '1px solid #475569',
    background: '#172033',
    color: '#e2e8f0',
    cursor: disabled ? 'not-allowed' : 'pointer',
    opacity: disabled ? 0.45 : 1,
    fontWeight: 700,
  };
}
