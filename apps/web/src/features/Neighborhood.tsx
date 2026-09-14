import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Plus, SlidersHorizontal } from 'lucide-react';
import { request, categories } from '../lib/api';
import type { Incident } from '../lib/api';
import {
  IncidentRow,
  ErrorMessage,
  Loading,
  EmptyState,
} from '../components/ui';
import { NeighborhoodMap } from '../components/NeighborhoodMap';
export function Neighborhood({ mine = false }: { mine?: boolean }) {
  const [category, setCategory] = useState('');
  const [status, setStatus] = useState('');
  const [following, setFollowing] = useState(false);
  const [selected, setSelected] = useState<string>();
  const [cursor, setCursor] = useState<string>();
  const [previous, setPrevious] = useState<Incident[]>([]);
  const query = useQuery({
    queryKey: ['incidents', { mine, category, status, following, cursor }],
    queryFn: () => {
      const params = new URLSearchParams();
      if (mine) params.set('mine', 'true');
      if (category) params.set('category', category);
      if (status) params.set('status', status);
      if (following) params.set('following', 'true');
      if (cursor) params.set('cursor', cursor);
      return request<{ items: Incident[]; next_cursor?: string }>(
        `/incidents?${params}`,
      );
    },
    refetchInterval: 6000,
  });
  const incidents = useMemo(
    () =>
      [...previous, ...(query.data?.items || [])].filter(
        (i, index, all) =>
          all.findIndex((other) => i.id === other.id) === index,
      ),
    [previous, query.data],
  );
  function changeFilter(fn: () => void) {
    setCursor(undefined);
    setPrevious([]);
    fn();
  }
  return (
    <main className="page neighborhood-page">
      <div className="page-heading">
        <div>
          <h1>{mine ? 'My cases' : 'Neighborhood'}</h1>
          <p>
            {mine
              ? 'Your observations. Our shared progress.'
              : 'Report once. Follow through together.'}
          </p>
        </div>
        <Link className="button primary" to="/report">
          <Plus size={20} />
          Report an issue
        </Link>
      </div>
      <div className={`neighborhood-layout ${mine ? 'mine-layout' : ''}`}>
        <section className="case-list" aria-label="Neighborhood cases">
          <div className="filters">
            <label>
              <span className="sr-only">Category</span>
              <select
                aria-label="Category"
                value={category}
                onChange={(e) =>
                  changeFilter(() => setCategory(e.target.value))
                }
              >
                <option value="">All categories</option>
                {Object.entries(categories).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className="sr-only">Agency status</span>
              <select
                aria-label="Agency status"
                value={status}
                onChange={(e) => changeFilter(() => setStatus(e.target.value))}
              >
                <option value="">All statuses</option>
                <option value="NOT_SUBMITTED">Not submitted</option>
                <option value="RECEIVED">Received</option>
                <option value="OPEN">Open</option>
                <option value="IN_PROGRESS">In progress</option>
                <option value="CLOSED">Closed</option>
              </select>
            </label>
            <button
              className={`filter-toggle ${following ? 'active' : ''}`}
              aria-pressed={following}
              onClick={() => changeFilter(() => setFollowing(!following))}
            >
              <SlidersHorizontal size={15} />
              Following
            </button>
          </div>
          <ErrorMessage error={query.error} />
          {query.isPending ? (
            <Loading />
          ) : incidents.length ? (
            <div className="incident-list">
              {incidents.map((incident) => (
                <IncidentRow
                  key={incident.id}
                  incident={incident}
                  selected={selected === incident.id}
                  onSelect={setSelected}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              title={
                mine
                  ? 'Your first observation starts here.'
                  : 'No matching case found in Neighborhood Fixer'
              }
            >
              {' '}
              {mine
                ? 'Report an issue or follow a shared case to keep its progress in one place.'
                : 'Try another filter, or report what you’ve noticed.'}
            </EmptyState>
          )}
          {query.data?.next_cursor && (
            <button
              className="button secondary load-more"
              onClick={() => {
                setPrevious(incidents);
                setCursor(query.data!.next_cursor);
              }}
            >
              Load more cases
            </button>
          )}
          <p className="list-footnote">
            Only explicitly shared case summaries appear in Neighborhood. Exact
            evidence and contacts stay private.
          </p>
        </section>
        <NeighborhoodMap
          incidents={incidents}
          selected={selected}
          onSelect={setSelected}
        />
      </div>
    </main>
  );
}
