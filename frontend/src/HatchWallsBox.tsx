import { useCallback, useEffect, useState } from 'react';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const MIN_TEXT = 20;

type HatchWall = {
  wall_id: string;
  wall_type: string;
  orientation: string;
  thickness_mm: number;
  length_mm: number;
  suggested: boolean;
  suggestion_reason: string;
  confirmation: { status: 'confirmed' | 'unconfirmed'; confirmation_id?: string; height_mm?: number };
};

type Confirmation = {
  confirmation_id: string;
  status: 'active' | 'revoked';
  reviewer: string;
  element_ids: string[];
  wall_types: Record<string, number>;
  height_mm: number;
  height_basis: string;
  revocation: { reviewer: string; reason: string } | null;
};

type HatchWallsView = {
  calibrated: boolean;
  storey_height_mm: number | null;
  legend: { label: string }[];
  summary: {
    detected: number;
    suggested: number;
    confirmed: number;
    by_type: Record<string, { detected: number; suggested: number; length_m: number }>;
  };
  candidates: HatchWall[];
  confirmations_on_page: Confirmation[];
  rules: string[];
};

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

const cell: React.CSSProperties = { padding: '4px 8px', borderBottom: '1px solid #1f2d3d', textAlign: 'left' };

export default function HatchWallsBox({ projectId, drawingId }: { projectId: string; drawingId: string }) {
  const [view, setView] = useState<HatchWallsView | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [justification, setJustification] = useState('');
  const [revokeReason, setRevokeReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const base = `${API}/projects/${projectId}/geometry/stage5-4/hatch-walls`;

  const load = useCallback(async () => {
    setError('');
    try {
      setView(await apiJson<HatchWallsView>(`${base}?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load hatch walls.');
    }
  }, [base, drawingId]);

  useEffect(() => {
    setView(null);
    setMessage('');
    load();
  }, [load]);

  if (!view) {
    return error
      ? <div className="message" style={{ color: '#fbbf24' }}>{error}</div>
      : <div className="message">Reading wall hatches…</div>;
  }

  const toConfirm = view.candidates.filter(c => c.suggested && c.confirmation.status === 'unconfirmed');
  const notSuggested = view.candidates.filter(c => !c.suggested);
  const missing: string[] = [];
  if (!view.calibrated) missing.push('calibrate this page (section 04)');
  if (!view.storey_height_mm) missing.push('place this page with a base and top level (section 04)');
  if (toConfirm.length === 0) missing.push('no unconfirmed suggested walls');
  if (reviewer.trim().length < 2) missing.push('enter your name');
  if (justification.trim().length < MIN_TEXT) missing.push(`write a justification of at least ${MIN_TEXT} characters`);

  async function confirmSuggested() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await apiJson<{ confirmation: Confirmation }>(`${base}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          drawing_id: drawingId,
          page_number: PAGE_NUMBER,
          element_ids: toConfirm.map(c => c.wall_id),
          reviewer: reviewer.trim(),
          justification: justification.trim(),
        }),
      });
      setMessage(`Confirmed ${result.confirmation.element_ids.length} wall(s) at ${result.confirmation.height_mm} mm. Regenerate the model (section 06) to see them.`);
      setJustification('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Confirming walls failed.');
    } finally {
      setBusy(false);
    }
  }

  async function revoke(confirmationId: string) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await apiJson(`${base}/${confirmationId}/revoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewer: reviewer.trim(), reason: revokeReason.trim() }),
      });
      setMessage('Wall confirmation revoked (kept for audit).');
      setRevokeReason('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Revoke failed.');
    } finally {
      setBusy(false);
    }
  }

  const reasons = notSuggested.reduce<Record<string, number>>((acc, c) => {
    const key = c.suggestion_reason.replace(/^\d+ mm outside/, 'Outside').split(' - ')[0].split(';')[0];
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: 'grid', gap: 10, padding: 16, border: '1px solid #29384a', borderRadius: 12, color: '#cbd5e1', fontSize: 13 }}>
      <strong style={{ color: '#eaf0f8' }}>Walls from the hatch legend (LOD 300)</strong>
      {view.legend.length === 0 ? (
        <small style={{ color: '#fbbf24' }}>
          No wall hatch legend found on this sheet (text containing WALL with a swatch beside it).
        </small>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
            <span>Detected: {view.summary.detected}</span>
            <span>Suggested: {view.summary.suggested}</span>
            <span style={{ color: '#22c55e' }}>Confirmed: {view.summary.confirmed}</span>
            <span>Storey height: {view.storey_height_mm ? `${Math.round(view.storey_height_mm).toLocaleString()} mm` : 'not placed'}</span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr>{['Wall type (legend)', 'Detected', 'Suggested', 'Length'].map(h => <th key={h} style={{ ...cell, color: '#94a3b8' }}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(view.summary.by_type).map(([type, entry]) => (
                <tr key={type}>
                  <td style={cell}>{type}</td>
                  <td style={cell}>{entry.detected}</td>
                  <td style={cell}>{entry.suggested}</td>
                  <td style={cell}>{entry.length_m.toLocaleString()} m</td>
                </tr>
              ))}
            </tbody>
          </table>
          {notSuggested.length > 0 && (
            <small style={{ color: '#8fa1b5' }}>
              Not suggested ({notSuggested.length}): {Object.entries(reasons).map(([r, n]) => `${r} ×${n}`).join(' · ')}
            </small>
          )}
        </>
      )}
      <small style={{ color: '#8fa1b5' }}>{view.rules.join(' ')}</small>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
        <label>
          Reviewer name
          <input value={reviewer} onChange={e => setReviewer(e.target.value)} />
        </label>
        <label>
          Justification ({justification.trim().length}/{MIN_TEXT})
          <input value={justification} onChange={e => setJustification(e.target.value)}
            placeholder="e.g. Hatched walls match the sheet legend; walls run slab to slab." />
        </label>
      </div>
      <div>
        <button className="primary" disabled={busy || missing.length > 0} onClick={confirmSuggested}>
          Confirm {toConfirm.length} suggested wall(s) at storey height
        </button>
      </div>
      {missing.length > 0 && <small style={{ color: '#fbbf24' }}>To confirm: {missing.join('; ')}.</small>}
      {view.confirmations_on_page.map(record => (
        <div key={record.confirmation_id} style={{ fontSize: 12, opacity: record.status === 'active' ? 1 : 0.6 }}>
          {record.status} · {record.element_ids.length} walls at {record.height_mm} mm ({record.height_basis}) · by {record.reviewer}
          {record.revocation && ` · revoked by ${record.revocation.reviewer}: ${record.revocation.reason}`}
          {record.status === 'active' && (
            <span style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <input value={revokeReason} onChange={e => setRevokeReason(e.target.value)}
                placeholder={`Reason to revoke (${MIN_TEXT}+ characters)`} />
              <button
                disabled={busy || reviewer.trim().length < 2 || revokeReason.trim().length < MIN_TEXT}
                onClick={() => revoke(record.confirmation_id)}
                style={{ padding: '8px 12px', borderRadius: 7, border: '1px solid #475569', background: '#172033', color: '#e2e8f0' }}
              >
                Revoke
              </button>
            </span>
          )}
        </div>
      ))}
      {message && <div className="message">{message}</div>}
      {error && <div className="message" style={{ color: '#fbbf24' }}>{error}</div>}
    </div>
  );
}
