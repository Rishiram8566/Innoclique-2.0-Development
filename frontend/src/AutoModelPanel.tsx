import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check } from 'lucide-react';

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
  started_at?: string | null;
  finished_at?: string | null;
};

const ICON: Record<Step['status'], string> = { pending: '○', running: '…', done: '✓', skipped: '✓', failed: '✗' };
const COLOR: Record<Step['status'], string> = {
  pending: 'var(--faint)', running: 'var(--orange)', done: 'var(--ok)', skipped: 'var(--ok)', failed: 'var(--bad)',
};

function since(iso?: string | null): string {
  if (!iso) return '';
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

/**
 * Generate and its progress, kept simple: one button, a progress bar while it runs, and the result.
 * Every step, the CAD/BIM import notes and the plan table are under "Show details".
 */
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
  const [, setTick] = useState(0);
  const timer = useRef<number | null>(null);
  // A run seen in progress here: when it ends (done or failed) the 3D model is reloaded. A run that had already
  // finished when the project was opened needs no reload, since the viewer has just loaded the model.
  const sawRunning = useRef(false);
  const url = `${API}/projects/${projectId}/auto-model`;

  const poll = useCallback(async () => {
    try {
      const response = await fetch(url);
      const body = (await response.json()) as Status;
      setStatus(body);
      setTick(n => n + 1);
      if (body.state === 'running') {
        sawRunning.current = true;
        timer.current = window.setTimeout(poll, 1500);
      } else if (sawRunning.current) {
        sawRunning.current = false;
        onFinished();
      }
    } catch {
      setError('The model engine is not reachable. Start the server and try again.');
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
    sawRunning.current = true;
    timer.current = window.setTimeout(poll, 800);
  }

  const running = status?.state === 'running';
  const summary = status?.summary;
  const steps = status?.steps ?? [];
  const finished = steps.filter(s => s.status === 'done' || s.status === 'skipped').length;
  const current = steps.find(s => s.status === 'running');
  const percent = steps.length ? Math.round((finished / steps.length) * 100) : 0;

  return (
    <div className="generate">
      {drawingCount === 0 ? (
        <div className="empty">Add your drawings or model files to this project first, then generate.</div>
      ) : running ? (
        <div className="progress-card">
          <div className="progress-head">
            <span className="loader" />
            <strong>Building your 3D model…</strong>
            <span className="progress-pct">{percent}%</span>
          </div>
          <div className="progress-bar"><i style={{ width: `${Math.max(4, percent)}%` }} /></div>
          <div className="progress-sub">
            Step {Math.min(finished + 1, steps.length)} of {steps.length}: {current?.label ?? 'Starting'}
            {status?.started_at ? ` · ${since(status.started_at)}` : ''}
          </div>
        </div>
      ) : status?.state === 'done' ? (
        <div className="result ok">
          <span className="result-icon"><Check size={18} strokeWidth={3} /></span>
          <div>
            <strong>3D model ready</strong>
            <span>
              {summary ? `${summary.plans_in_model} floor(s) · ${summary.walls_served} building element(s)` : 'Open it below.'}
            </span>
          </div>
          <button className="btn-quiet" onClick={start}>Regenerate</button>
        </div>
      ) : status?.state === 'failed' ? (
        <div className="result bad">
          <span className="result-icon"><AlertTriangle size={17} /></span>
          <div>
            <strong>Generation stopped</strong>
            <span>{status.error}</span>
          </div>
          <button className="primary" onClick={start}>Try again <span>→</span></button>
        </div>
      ) : (
        <div className="result">
          <div>
            <strong>{status?.state === 'interrupted' ? 'The last run was interrupted' : 'Ready to build'}</strong>
            <span>
              {status?.state === 'interrupted'
                ? 'The server restarted while it ran. Finished steps are kept.'
                : `${drawingCount} file(s) in this project.`}
            </span>
          </div>
          <button className="primary" onClick={start} disabled={!status}>Generate 3D model <span>→</span></button>
        </div>
      )}

      {error && <div className="note warn">{error}</div>}

      {status && status.state !== 'not_started' && (
        <details className="more">
          <summary>Show details</summary>
          <div className="more-body">
            <ol className="steps">
              {steps.map(step => (
                <li key={step.key}>
                  <span style={{ color: COLOR[step.status] }}>{ICON[step.status]}</span>
                  <span className={step.status === 'pending' ? 'pending' : ''}>{step.label}</span>
                  {step.detail && <small>{step.detail}</small>}
                </li>
              ))}
            </ol>

            {summary && summary.automatic_elements > 0 && (
              <div className="note warn">
                {summary.automatic_elements} element(s) were added automatically from the drawings and have not been
                reviewed by a person.
              </div>
            )}

            {status.models && status.models.length > 0 && (
              <div className="report">
                <strong>CAD / BIM files</strong>
                {status.models.map(m => (
                  <span key={m.model_id} className={m.status === 'failed' ? 'bad' : ''}>
                    {m.title}: {m.status}{m.notes.length ? ` — ${m.notes.join(' ')}` : ''}
                  </span>
                ))}
              </div>
            )}

            {status.plans && status.plans.length > 0 && (
              <table className="table">
                <thead>
                  <tr>{['Floor plan', 'Scale (mm per PDF pt)', 'Storey height', 'What happened'].map(h => <th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {status.plans.map(plan => (
                    <tr key={plan.drawing_id}>
                      <td>{plan.title}</td>
                      <td>{plan.scale ? plan.scale.toFixed(3) : '—'}</td>
                      <td>{plan.storey_height_mm ? `${Math.round(plan.storey_height_mm).toLocaleString()} mm` : '—'}</td>
                      <td className="muted">{plan.notes.join('; ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </details>
      )}
    </div>
  );
}
