import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { ChevronDown, FlaskConical, Clock, RotateCcw } from 'lucide-react';
import { humanize } from '../lib/api';
import { useApi } from '../lib/api-context';
import type { Incident } from '../lib/api';
import { ErrorMessage } from './ui';
import { useSession } from '../lib/session';
export function DemoPanel() {
  const { request, post } = useApi();
  const { session, health } = useSession();
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState('');
  const [resetConfirm, setResetConfirm] = useState(false);
  const [lostReceipt, setLostReceipt] = useState(false);
  const [ticketStatus, setTicketStatus] = useState('CLOSED');
  const [note, setNote] = useState('');
  const incidentId = location.pathname.startsWith('/cases/')
    ? location.pathname.split('/')[2]
    : undefined;
  const active = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => request<Incident>(`/incidents/${incidentId}`),
    enabled: !!incidentId,
  });
  async function action(path: string, body: unknown, result: string) {
    setBusy(true);
    setError(undefined);
    try {
      await post(path, body);
      setMessage(result);
      await client.invalidateQueries();
      if (path === '/demo/reset') {
        Object.keys(localStorage)
          .filter((k) => k.startsWith(`nf-report:${session.workspace_id}:`))
          .forEach((k) => localStorage.removeItem(k));
        setResetConfirm(false);
        navigate('/');
      }
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="demo-panel">
      <summary>
        <span>
          <FlaskConical size={15} />
          {session.mode.toLowerCase().includes('local')
            ? 'Local scenario controls'
            : 'Integration status'}
        </span>
        <ChevronDown size={15} />
      </summary>
      <div className="demo-body">
        <div className="integration-status">
          <h3>Component status</h3>
          <dl>
            {Object.entries(health.integrations || {}).map(
              ([component, value]) => (
                <div key={component}>
                  <dt>{humanize(component)}</dt>
                  <dd>
                    {typeof value === 'string'
                      ? value
                      : [value.provider, value.status, value.detail]
                          .filter(Boolean)
                          .join(' · ')}
                  </dd>
                </div>
              ),
            )}
          </dl>
        </div>
        {session.mode.toLowerCase().includes('local') && (
          <div className="scenario-controls">
            <h3>Your isolated demo workspace</h3>
            <p className="small muted">
              These controls exercise persisted backend transitions. Alex and
              Sam share only this local workspace.
            </p>
            <ErrorMessage error={error} />
            {message && (
              <p role="status" className="notice success">
                {message}
              </p>
            )}
            <div className="demo-control-row">
              <button
                className="button secondary"
                disabled={busy}
                onClick={() =>
                  void action(
                    '/demo/clock',
                    { advance_seconds: 3600 },
                    'Virtual clock advanced one hour. The worker will process due checks.',
                  )
                }
              >
                <Clock size={15} />
                Advance local clock 1 hour
              </button>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={lostReceipt}
                  disabled={busy}
                  onChange={(e) => {
                    const value = e.target.checked;
                    setLostReceipt(value);
                    void action(
                      '/demo/scenario',
                      { lost_receipt: value },
                      value
                        ? 'Next submission will simulate a lost receipt.'
                        : 'Lost receipt simulation is off.',
                    );
                  }}
                />
                <span>Simulate a lost receipt on next submission</span>
              </label>
            </div>
            {active.data?.ticket && (
              <div className="demo-ticket-controls">
                <label>
                  Fictional ticket status
                  <select
                    value={ticketStatus}
                    onChange={(e) => setTicketStatus(e.target.value)}
                  >
                    <option value="OPEN">Open</option>
                    <option value="IN_PROGRESS">In progress</option>
                    <option value="CLOSED">Closed</option>
                  </select>
                </label>
                <label>
                  Agency note
                  <input
                    placeholder="e.g. Work completed, or duplicate"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                </label>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() =>
                    void action(
                      `/demo/tickets/${incidentId}/status`,
                      { status: ticketStatus, closure_note: note },
                      'Fictional agency ticket updated. Advance the local clock to run a status check.',
                    )
                  }
                >
                  Update fictional ticket
                </button>
              </div>
            )}
            {resetConfirm ? (
              <div className="notice amber">
                <strong>Reset this workspace?</strong>
                <p>
                  This removes its demo cases, evidence and progress for Alex
                  and Sam.
                </p>
                <button
                  className="button danger"
                  disabled={busy}
                  onClick={() =>
                    void action(
                      '/demo/reset',
                      {},
                      'Your isolated workspace was reset.',
                    )
                  }
                >
                  Reset my demo workspace
                </button>
                <button
                  className="text-button"
                  onClick={() => setResetConfirm(false)}
                >
                  Keep my cases
                </button>
              </div>
            ) : (
              <button
                className="text-button danger-text"
                onClick={() => setResetConfirm(true)}
              >
                <RotateCcw size={14} />
                Reset this demo workspace
              </button>
            )}
          </div>
        )}
      </div>
    </details>
  );
}
