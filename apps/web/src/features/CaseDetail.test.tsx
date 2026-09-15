import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApprovalPanel, CaseDetail } from './CaseDetail';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { createApiClient } from '../lib/api';
import type { Draft, Incident } from '../lib/api';
import { ApiContext } from '../lib/api-context';
import { SessionContext } from '../lib/session-context';
vi.mock('../components/NeighborhoodMap', () => ({
  NeighborhoodMap: () => null,
}));
const draft: Draft = {
  id: 'draft-1',
  revision: 1,
  recipient: 'Demo Borough Public Works',
  category: 'damaged_sidewalk',
  description: 'An uneven curb ramp edge obstructs passage.',
  location_label: 'Maple & Alder',
  latitude: 47.615,
  longitude: -122.335,
  contact: {},
  attachment_ids: [],
  attachment_hashes: [],
  payload_hash: 'exact-hash-1',
  expires_at: '2099-01-01T00:00:00Z',
  status: 'PREPARED',
};
const incident = { id: 'case-1', shared_public: false } as Incident;
const sampleIncident = {
  id: 'sample',
  title: 'Illustrative curb',
  category: 'damaged_sidewalk',
  description: 'An illustrative case.',
  location_label: 'Demo Borough',
  agency_status: 'OPEN',
  resolution_status: 'UNVERIFIED',
  submission_status: 'AWAITING_APPROVAL',
  observation_count: 1,
  is_sample: true,
  is_owner: true,
  draft,
};
function wrap(d: Draft = draft, record: Incident = incident) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <ApiContext.Provider value={createApiClient()}>
        <ApprovalPanel incident={record} draft={d} onSuccess={() => {}} />
      </ApiContext.Provider>
    </QueryClientProvider>
  );
}
function renderCaseDetail(
  mode: 'local' | 'aws',
  record: Incident = sampleIncident as Incident,
) {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(record), { status: 200 })),
  );
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SessionContext.Provider
        value={{
          session: {
            user: { id: 'resident-1', name: 'Alex', resident: 'alex' },
            workspace_id: 'workspace-1',
            mode,
          },
          health: { mode, integrations: {} },
          switchResident: async () => {},
        }}
      >
        <ApiContext.Provider value={createApiClient()}>
          <MemoryRouter initialEntries={['/cases/sample']}>
            <Routes>
              <Route path="/cases/:id" element={<CaseDetail />} />
            </Routes>
          </MemoryRouter>
        </ApiContext.Provider>
      </SessionContext.Provider>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
describe('exact submission approval', () => {
  it('requires agency approval independently from optional publication consent', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ operation_id: 'op-1' }), { status: 202 }),
      );
    vi.stubGlobal('fetch', fetchMock);
    render(wrap());
    const approve = screen.getByRole('button', {
      name: 'Approve exact submission',
    });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByLabelText(/Share a sanitized case summary/));
    expect(approve).toBeDisabled();
    fireEvent.click(
      screen.getByLabelText(/I approve sending this exact report/),
    );
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      draft_id: 'draft-1',
      payload_hash: 'exact-hash-1',
      publish_consent: true,
      share_evidence: false,
    });
  });
  it('does not require publication to authorize an agency report', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ operation_id: 'op-1' }), { status: 202 }),
      );
    vi.stubGlobal('fetch', fetchMock);
    render(wrap());
    fireEvent.click(
      screen.getByLabelText(/I approve sending this exact report/),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Approve exact submission' }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).publish_consent).toBe(
      false,
    );
  });
  it('preserves an existing case and photo sharing choice on approval', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ operation_id: 'op-1' }), { status: 202 }),
      );
    vi.stubGlobal('fetch', fetchMock);
    render(
      wrap(draft, {
        ...incident,
        shared_public: true,
        observations: [
          {
            is_yours: true,
            evidence: [
              {
                id: 'photo-1',
                url: '/api/evidence/photo-1',
                public_approved: true,
              },
            ],
          },
        ],
      } as Incident),
    );
    expect(
      screen.getByLabelText(/Share a sanitized case summary/),
    ).toBeChecked();
    expect(
      screen.getByLabelText(/Also publish the attachment photos/),
    ).toBeChecked();
    fireEvent.click(
      screen.getByLabelText(/I approve sending this exact report/),
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Approve exact submission' }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
      publish_consent: true,
      share_evidence: true,
    });
  });
  it('clears approval when the material draft revision changes', () => {
    const view = render(wrap());
    fireEvent.click(
      screen.getByLabelText(/I approve sending this exact report/),
    );
    expect(
      screen.getByRole('button', { name: 'Approve exact submission' }),
    ).toBeEnabled();
    view.rerender(
      wrap({
        ...draft,
        id: 'draft-2',
        revision: 2,
        payload_hash: 'exact-hash-2',
        description: 'Revised wording requires a fresh approval.',
      }),
    );
    expect(
      screen.getByRole('button', { name: 'Approve exact submission' }),
    ).toBeDisabled();
  });
});

describe('illustrative shared sample', () => {
  it('explains that no agent ran and offers no follow, verification, or approval action', async () => {
    renderCaseDetail('aws');
    expect(
      await screen.findByText(/Illustrative sample case · read only/),
    ).toBeVisible();
    expect(
      screen.getByText(/This illustrative sample did not run an agent/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Report your own observation' }),
    ).toHaveAttribute('href', '/report');
    expect(
      screen.queryByText(/Local analysis is deterministic and simulated/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Follow this case' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Approve exact submission' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Save verification' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Cancel if not yet sent' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('heading', {
        name: 'Official contact & demo outreach',
      }),
    ).not.toBeInTheDocument();
  });

  it('keeps private outreach controls hidden from case followers', async () => {
    renderCaseDetail('local', {
      ...sampleIncident,
      id: 'followed-case',
      is_sample: false,
      is_owner: false,
    } as Incident);
    expect(await screen.findByText('Illustrative curb')).toBeVisible();
    expect(
      screen.queryByRole('heading', {
        name: 'Official contact & demo outreach',
      }),
    ).not.toBeInTheDocument();
  });

  it('retains the simulation disclosure in local mode', async () => {
    renderCaseDetail('local');
    expect(await screen.findByText(/Illustrative sample case/)).toBeVisible();
    expect(
      screen.getByText(/Local analysis is deterministic and simulated/),
    ).toBeInTheDocument();
  });
});
