import { useCallback, useEffect, useState } from 'react';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const MIN_TEXT = 20;

type ColumnCandidate = {
  column_id: string;
  grid_label: string;
  size_mm: number[];
  confirmation: { status: 'confirmed' | 'unconfirmed'; confirmation_id?: string; height_mm?: number };
};

type ColumnConfirmation = {
  confirmation_id: string;
  status: 'active' | 'revoked';
  reviewer: string;
  created_at: string;
  column_ids: string[];
  height_mm: number;
  height_basis: string;
  revocation: { reviewer: string; reason: string } | null;
};

type ColumnsView = {
  calibrated: boolean;
  storey_height_mm: number | null;
  summary: { detected: number; confirmed: number };
  candidates: ColumnCandidate[];
  confirmations_on_page: ColumnConfirmation[];
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

export default function ColumnsBox({ projectId, drawingId }: { projectId: string; drawingId: string }) {
  const [view, setView] = useState<ColumnsView | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [justification, setJustification] = useState('');
  const [revokeReason, setRevokeReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const base = `${API}/projects/${projectId}/geometry/stage5-4/columns`;

  const load = useCallback(async () => {
    setError('');
    try {
      setView(await apiJson<ColumnsView>(`${base}?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load columns.');
    }
  }, [base, drawingId]);

  useEffect(() => {
    setMessage('');
    load();
  }, [load]);

  if (!view) return error ? <div className="message" style={{ color: '#fbbf24' }}>{error}</div> : null;

  const unconfirmed = view.candidates.filter(c => c.confirmation.status === 'unconfirmed');
  const missing: string[] = [];
  if (!view.calibrated) missing.push('calibrate this page (section 04)');
  if (!view.storey_height_mm) missing.push('place this page with a base and top level (section 04)');
  if (unconfirmed.length === 0) missing.push('no unconfirmed columns');
  if (reviewer.trim().length < 2) missing.push('enter your name');
  if (justification.trim().length < MIN_TEXT) missing.push(`write a justification of at least ${MIN_TEXT} characters`);

  async function confirmAll() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await apiJson<{ confirmation: ColumnConfirmation }>(`${base}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          drawing_id: drawingId,
          page_number: PAGE_NUMBER,
          column_ids: unconfirmed.map(c => c.column_id),
          reviewer: reviewer.trim(),
          justification: justification.trim(),
        }),
      });
      setMessage(`Confirmed ${result.confirmation.column_ids.length} column(s) at ${result.confirmation.height_mm} mm. Regenerate the model (section 06) to see them.`);
      setJustification('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Confirming columns failed.');
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
      setMessage('Column confirmation revoked (kept for audit).');
      setRevokeReason('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Revoke failed.');
    } finally {
      setBusy(false);
    }
  }

  const sizes = view.candidates.reduce<Record<string, number>>((acc, c) => {
    const key = `${Math.round(c.size_mm[0] / 50) * 50}×${Math.round(c.size_mm[1] / 50) * 50}`;
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ display: 'grid', gap: 10, padding: 16, border: '1px solid #29384a', borderRadius: 12, color: '#cbd5e1', fontSize: 13 }}>
      <strong style={{ color: '#eaf0f8' }}>Columns (LOD 100)</strong>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        <span>Detected: {view.summary.detected}</span>
        <span style={{ color: '#22c55e' }}>Confirmed: {view.summary.confirmed}</span>
        <span>Storey height: {view.storey_height_mm ? `${Math.round(view.storey_height_mm).toLocaleString()} mm` : 'not placed'}</span>
        <span>Sizes (mm): {Object.entries(sizes).map(([k, n]) => `${k} ×${n}`).join(', ') || '—'}</span>
      </div>
      <small style={{ color: '#8fa1b5' }}>{view.rules.join(' ')}</small>
      {unconfirmed.length > 0 && (
        <small style={{ color: '#8fa1b5' }}>
          Unconfirmed at grid: {unconfirmed.map(c => c.grid_label).join(', ')}
        </small>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10 }}>
        <label>
          Reviewer name
          <input value={reviewer} onChange={e => setReviewer(e.target.value)} />
        </label>
        <label>
          Justification ({justification.trim().length}/{MIN_TEXT})
          <input value={justification} onChange={e => setJustification(e.target.value)}
            placeholder="e.g. Column squares on grid intersections; columns run slab to slab." />
        </label>
      </div>
      <div>
        <button className="primary" disabled={busy || missing.length > 0} onClick={confirmAll}>
          Confirm {unconfirmed.length} detected column(s) at storey height
        </button>
      </div>
      {missing.length > 0 && <small style={{ color: '#fbbf24' }}>To confirm: {missing.join('; ')}.</small>}
      {view.confirmations_on_page.map(record => (
        <div key={record.confirmation_id} style={{ fontSize: 12, opacity: record.status === 'active' ? 1 : 0.6 }}>
          {record.status} · {record.column_ids.length} columns at {record.height_mm} mm ({record.height_basis}) · by {record.reviewer}
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
