import { useState } from 'react';
import { NavLink, Route, Routes, Link } from 'react-router-dom';
import { AlertCircle, ChevronDown, UserRound, Bell, X } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSession } from './lib/session';
import { dateTime } from './lib/api';
import { useApi } from './lib/api-context';
import { Neighborhood } from './features/Neighborhood';
import { Report } from './features/Report';
import { CaseDetail } from './features/CaseDetail';
import { DemoPanel } from './components/DemoPanel';
import { ErrorMessage } from './components/ui';
function Notifications() {
  const { request, post } = useApi();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<unknown>();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ['notifications'],
    queryFn: () =>
      request<{
        items: {
          id: string;
          incident_id?: string;
          message?: string;
          title?: string;
          body?: string;
          read?: boolean;
          read_at?: string;
          created_at?: string;
        }[];
      }>('/notifications'),
    refetchInterval: 6000,
  });
  const unread =
    query.data?.items.filter((n) => !n.read && !n.read_at).length || 0;
  return (
    <div className="notifications-wrap">
      <button
        className="icon-button notification-button"
        aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Bell size={19} />
        {unread > 0 && <span className="notification-dot" />}
      </button>
      {open && (
        <div className="notifications-popover">
          <div className="section-heading">
            <h3>Notifications</h3>
            <button
              className="icon-button"
              aria-label="Close notifications"
              onClick={() => setOpen(false)}
            >
              <X size={17} />
            </button>
          </div>
          <ErrorMessage error={query.error || error} />
          {!query.data?.items.length ? (
            <p className="muted small">Case updates will appear here.</p>
          ) : (
            query.data.items.map((item) => (
              <article key={item.id}>
                {item.incident_id ? (
                  <Link
                    to={`/cases/${item.incident_id}`}
                    onClick={() => {
                      setOpen(false);
                      void post(`/notifications/${item.id}/read`)
                        .then(() =>
                          client.invalidateQueries({
                            queryKey: ['notifications'],
                          }),
                        )
                        .catch(setError);
                    }}
                  >
                    {item.message || item.title || item.body || 'Case update'}
                  </Link>
                ) : (
                  <p>{item.message || item.title || item.body}</p>
                )}
                <time>{dateTime(item.created_at)}</time>
              </article>
            ))
          )}
        </div>
      )}
    </div>
  );
}
export default function App() {
  const { session, switchResident, signOut } = useSession();
  const local = session.mode.toLowerCase().includes('local');
  return (
    <>
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="app-header">
        <Link to="/" className="brand">
          <span className="brand-symbol" aria-hidden="true" />
          <span>Neighborhood Fixer</span>
        </Link>
        <nav aria-label="Main navigation">
          <NavLink to="/" end>
            Neighborhood
          </NavLink>
          <NavLink to="/report">Report an issue</NavLink>
          <NavLink to="/my-cases">My cases</NavLink>
        </nav>
        <div className="header-controls">
          <Notifications />
          {local ? (
            <label className="resident-selector">
              <UserRound size={18} />
              <span className="sr-only">Local demo resident</span>
              <select
                value={session.user.resident}
                onChange={(e) => void switchResident(e.target.value)}
              >
                <option value="alex">Alex</option>
                <option value="sam">Sam</option>
              </select>
              <ChevronDown size={16} />
            </label>
          ) : (
            <>
              <span className="small">{session.user.name}</span>
              <button className="text-button" onClick={() => void signOut?.()}>
                Sign out
              </button>
            </>
          )}
        </div>
      </header>
      <div className={`mode-notice ${!local ? 'aws-mode' : ''}`}>
        <AlertCircle size={18} />
        {local
          ? 'Local demo — simulated AI and fictional agency.'
          : 'AWS demo — fictional receiving agency. Check component status below.'}
      </div>
      <div id="main-content" tabIndex={-1}>
        <Routes key={session.user.id}>
          <Route path="/" element={<Neighborhood />} />
          <Route path="/my-cases" element={<Neighborhood mine />} />
          <Route
            path="/report"
            element={<Report key={`${session.user.id}-report`} />}
          />
          <Route path="/cases/:id" element={<CaseDetail />} />
          <Route
            path="*"
            element={
              <main className="page">
                <h1>This page isn’t here.</h1>
                <Link className="button primary" to="/">
                  Back to Neighborhood
                </Link>
              </main>
            }
          />
        </Routes>
      </div>
      <footer className="app-footer">
        <div>
          <span>Demo Borough</span>
          <span className="footer-tagline">
            Report once. Follow through together.
          </span>
          <span>Neighborhood Fixer</span>
        </div>
        <DemoPanel />
      </footer>
    </>
  );
}
