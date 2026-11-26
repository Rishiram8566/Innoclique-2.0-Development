import { useCallback, useEffect, useRef, useState } from 'react';

const API = 'http://127.0.0.1:8000/api';

type Step = { key: string; label: string; status: 'pending' | 'running' | 'done' | 'skipped' | 'failed'; detail: string };
type Plan = { drawing_id: string; title: string; scale?: number; storey_height_mm?: number | null; notes: string[] };
type Status = {
  state: 'not_started' | 'running' | 'done' | 'failed' | 'interrupted';
  steps: Step[];
  plans?: Plan[];
  summary?: {
    plans_in_model: number;
    walls_served: number;
    automatic_elements: number;
    slabs: number;
    automatic_confirmations: { columns: number; hatch_walls: number; line_pair_walls: number };
  } | null;
  error?: string | null;
  reviewer?: string;
  models?: { model_id: string; title: string; status: string; notes: string[] }[];
  finished_at?: string | null;
};

const ICON: Record<Step['status'], string> = { pending: '○', running: '…', done: '✓', skipped: '✓', failed: '✗' };
const COLOR: Record<Step['status'], string> = {
  pending: '#64748b', running: '#fbbf24', done: '#22c55e', skipped: '#22c55e', failed: '#f87171',
};

export default function AutoModelPanel({
  projectId,
  drawingCount,
  onFinished,
}: {
  projectId: string;
  drawingCount: number;
  onFinished: () => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState('');
  const timer = useRef<number | null>(null);
  const url = `${API}/projects/${projectId}/auto-model`;

  const poll = useCallback(async () => {
    try {
      const response = await fetch(url);
      const body = (await response.json()) as Status;
      setStatus(body);
      if (body.state === 'running') {
        timer.current = window.setTimeout(poll, 1500);
      } else if (body.state === 'done') {
        onFinished();
      }
    } catch {
      setError('Backend not reachable.');
    }
  }, [url, onFinished]);

  useEffect(() => {
    setStatus(null);
    setError('');
    poll();
    return () => { if (timer.current) window.clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function start() {
    setError('');
    const response = await fetch(url, { method: 'POST' });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      setError(typeof body?.detail === 'string' ? body.detail : `Could not start (${response.status}).`);
      return;
    }
    setStatus(body as Status);
    timer.current = window.setTimeout(poll, 800);
  }

  const running = status?.state === 'running';
  const summary = status?.summary;

  return (
    <div style={{ display: 'grid', gap: 14, color: '#cbd5e1', fontSize: 13 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <button className="primary" disabled={running || drawingCount === 0} onClick={start}>
          {running ? 'Generating…' : status?.state === 'done' ? 'Regenerate 3D model' : 'Generate 3D model'} <span>→</span>
        </button>
        <span style={{ color: '#8fa1b5' }}>
          {drawingCount === 0
            ? 'Upload at least one PDF, DXF or IFC file first.'
            : `${drawingCount} file(s) in this project. Finished steps are skipped on re-runs.`}
        </span>
      </div>

      {status && status.state !== 'not_started' && (
        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6 }}>
          {status.steps.map(step => (
            <li key={step.key} style={{ display: 'flex', gap: 10 }}>
              <span style={{ width: 16, color: COLOR[step.status], fontWeight: 700 }}>{ICON[step.status]}</span>
              <span style={{ color: step.status === 'pending' ? '#64748b' : '#e2e8f0' }}>{step.label}</span>
              {step.detail && <span style={{ color: '#8fa1b5' }}>— {step.detail}</span>}
            </li>
          ))}
        </ol>
      )}

      {status?.models && status.models.length > 0 && (
        <div className="message" style={{ display: 'grid', gap: 4 }}>
          <strong style={{ color: '#eaf0f8' }}>CAD / BIM models</strong>
          {status.models.map(m => (
            <span key={m.model_id} style={{ color: m.status === 'failed' ? '#f87171' : undefined }}>
              {m.title}: {m.status}{m.notes.length ? ` — ${m.notes.join(' ')}` : ''}
            </span>
          ))}
        </div>
      )}

      {status?.state === 'failed' && (
        <div className="message" style={{ color: '#f87171' }}>Generation stopped: {status.error}</div>
      )}
      {status?.state === 'interrupted' && (
        <div className="message" style={{ color: '#fbbf24' }}>
          The last run was interrupted (the server restarted). Press Generate again; finished steps are kept.
        </div>
      )}

      {summary && status?.state === 'done' && (
        <div className="message" style={{ display: 'grid', gap: 6 }}>
          <strong style={{ color: '#eaf0f8' }}>
            {summary.plans_in_model} storey(s) · {summary.walls_served} element(s) · {summary.slabs} slab(s)
          </strong>
          {summary.automatic_elements > 0 && (
            <span style={{ color: '#fbbf24' }}>
              {summary.automatic_elements} element(s) were added automatically from the drawings and have not been
              reviewed by a person.
            </span>
          )}
        </div>
      )}

      {status?.plans && status.plans.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Floor plan', 'Scale (mm per PDF pt)', 'Storey height', 'What happened'].map(h => (
                <th key={h} style={{ textAlign: 'left', padding: '4px 8px', color: '#94a3b8', borderBottom: '1px solid #1f2d3d' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {status.plans.map(plan => (
              <tr key={plan.drawing_id}>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #1f2d3d' }}>{plan.title}</td>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #1f2d3d' }}>{plan.scale ? plan.scale.toFixed(3) : '—'}</td>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #1f2d3d' }}>
                  {plan.storey_height_mm ? `${Math.round(plan.storey_height_mm).toLocaleString()} mm` : '—'}
                </td>
                <td style={{ padding: '4px 8px', borderBottom: '1px solid #1f2d3d', color: '#8fa1b5' }}>{plan.notes.join('; ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <div className="message" style={{ color: '#fbbf24' }}>{error}</div>}
    </div>
  );
}
