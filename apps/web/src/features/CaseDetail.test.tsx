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
function wrap(d: Draft = draft) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <ApiContext.Provider value={createApiClient()}>
        <ApprovalPanel incident={incident} draft={d} onSuccess={() => {}} />
      </ApiContext.Provider>
    </QueryClientProvider>
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
  it('shows a read-only label and offers no follow, verification, or approval action', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
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
            }),
          ),
      ),
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <ApiContext.Provider value={createApiClient()}>
          <MemoryRouter initialEntries={['/cases/sample']}>
            <Routes>
              <Route path="/cases/:id" element={<CaseDetail />} />
            </Routes>
          </MemoryRouter>
        </ApiContext.Provider>
      </QueryClientProvider>,
    );
    expect(
      await screen.findByText(/Illustrative sample case · read only/),
    ).toBeVisible();
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
  });
});
