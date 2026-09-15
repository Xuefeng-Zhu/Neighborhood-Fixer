import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ApiContext } from '../lib/api-context';
import { createApiClient } from '../lib/api';
import type {
  ContactResearch,
  ContactSelection,
  Incident,
  JurisdictionCandidate,
  OfficialContact,
  OutreachDraft,
  OutreachSnapshot,
  SimulationReceipt,
  VoiceEnvelope,
  VoiceRun,
  VoiceTurn,
} from '../lib/api';
import { OfficialOutreach, reachedVoiceCallLimit } from './OfficialOutreach';

const INCIDENT_ID = 'incident-case-0001';
const CONTACT_ID = 'official-contact-0001';
const JURISDICTION_ID = 'jurisdiction-candidate-0001';
const RESEARCH_ID = 'contact-research-0001';
const CONTEXT_HASH =
  '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const VOICE_PAYLOAD_HASH =
  'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const EMAIL_PAYLOAD_HASH =
  'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const PLAYBACK_TOKEN =
  'playback-token-0123456789abcdef0123456789abcdef';
const VOICE_FACTS: VoiceEnvelope['facts'] = [
  { id: 'category', label: 'Issue', value: 'Pothole' },
  {
    id: 'description',
    label: 'Description',
    value: 'A pothole is beside the marked crossing.',
  },
  { id: 'location', label: 'Location', value: 'Cedar Street' },
  {
    id: 'jurisdiction',
    label: 'Jurisdiction',
    value: 'Seattle, Washington',
  },
];

const incident = {
  id: INCIDENT_ID,
  title: 'Pothole beside crossing',
  description: 'A pothole is beside the marked crossing.',
  category: 'pothole',
  latitude: 47.61,
  longitude: -122.33,
  location_label: 'Cedar Street',
  agency_status: 'OPEN',
  resolution_status: 'UNVERIFIED',
  submission_status: 'PREPARED',
  observation_count: 1,
  owner_id: 'resident-1',
  following: true,
  shared_public: false,
  version: 1,
  created_at: '2026-09-14T12:00:00Z',
  updated_at: '2026-09-14T12:00:00Z',
  is_owner: true,
} satisfies Incident;

const contact: OfficialContact = {
  id: CONTACT_ID,
  agency: 'Seattle Department of Transportation',
  role: 'Road maintenance intake',
  email: '684-road@seattle.gov',
  phone: '206-684-7623',
  source_title: 'Contact SDOT',
  source_url: 'https://www.seattle.gov/transportation/about-us/contact-us',
  source_hostname: 'www.seattle.gov',
  match_reason: 'Official road-maintenance contact page',
  retrieved_at: '2026-09-14T12:05:00Z',
};

const jurisdiction: JurisdictionCandidate = {
  id: JURISDICTION_ID,
  incident_id: incident.id,
  context_hash: CONTEXT_HASH,
  display_name: 'Seattle, Washington, United States',
  locality: 'Seattle',
  municipality: 'Seattle',
  region: 'Washington',
  country_code: 'USA',
  label: 'Seattle, WA',
  supported: true,
  provider: 'Amazon Location Service',
  status: 'AWAITING_CONFIRMATION',
  created_at: '2026-09-14T12:00:00Z',
  expires_at: '2099-09-14T12:20:00Z',
};

const research: ContactResearch = {
  id: RESEARCH_ID,
  incident_id: incident.id,
  candidate_id: jurisdiction.id,
  status: 'READY',
  context_hash: CONTEXT_HASH,
  query_scope: { jurisdiction: 'Seattle, WA', category: incident.category },
  contacts: [contact],
  provider: 'Brave Web Search and verified official pages',
  created_at: '2026-09-14T12:05:00Z',
  expires_at: '2099-09-15T12:05:00Z',
};

const selection: ContactSelection = {
  id: 'selection-1',
  incident_id: incident.id,
  research_id: research.id,
  contact_id: contact.id,
  contact,
  context_hash: research.context_hash,
  status: 'SELECTED',
  selected_at: '2026-09-14T12:06:00Z',
};

const voiceEnvelopeFixture: VoiceEnvelope = {
  id: 'voice-envelope-fixture',
  incident_id: incident.id,
  revision: 1,
  facts: VOICE_FACTS,
  allowed_intents: {
    reporting_agent: ['report_issue'],
    fictional_intake_agent: ['close'],
  },
  refusal_rules: ['Do not claim a real government response.'],
  max_turns: 6,
  max_duration_seconds: 90,
  research_reference: contact,
  selected_contact: contact,
  research_snapshot_id: research.id,
  execution_target: 'internal-voice-simulator-v1',
  context_hash: research.context_hash,
  payload_hash: VOICE_PAYLOAD_HASH,
  status: 'APPROVED',
  variability_notice:
    'Exact wording may vary inside the approved fact and intent envelope.',
  created_at: '2026-09-14T12:07:00Z',
  expires_at: '2099-09-14T12:30:00Z',
};

const voiceTurnFixture: VoiceTurn = {
  id: 'voice-turn-item-0001',
  speaker: 'reporting_agent',
  intent: 'report_issue',
  fact_ids: ['category'],
  variant_id: 'report-1',
  caption: 'This is a temporary simulated report.',
  audio_url:
    `/api/incidents/${INCIDENT_ID}/outreach/voice/runs/voice-run-fixture/turns/voice-turn-item-0001/audio`,
};

const voiceRunFixture: VoiceRun = {
  id: 'voice-run-fixture',
  incident_id: incident.id,
  envelope_id: voiceEnvelopeFixture.id,
  payload_hash: voiceEnvelopeFixture.payload_hash,
  status: 'RUNNING',
  execution_target: 'internal-voice-simulator-v1',
  created_at: '2026-09-14T12:08:00Z',
  started_at: '2026-09-14T12:08:00Z',
  transcript_expires_at: '2099-09-14T12:23:00Z',
  turn_count: 1,
  research_snapshot_id: research.id,
  turns: [voiceTurnFixture],
};

const emailDraftFixture: OutreachDraft = {
  id: 'email-draft-fixture',
  incident_id: incident.id,
  channel: 'email',
  revision: 1,
  subject: 'Pothole report near Cedar Street',
  body: 'This is an internal simulation of a neighborhood report.',
  research_reference: contact,
  selected_contact: contact,
  research_snapshot_id: research.id,
  execution_target: 'internal-email-simulator-v1',
  context_hash: research.context_hash,
  payload_hash: EMAIL_PAYLOAD_HASH,
  status: 'AWAITING_APPROVAL',
  created_at: '2026-09-14T12:07:00Z',
  expires_at: '2099-09-14T12:30:00Z',
};

const voiceReceiptFixture: SimulationReceipt = {
  id: 'voice-receipt-fixture',
  incident_id: incident.id,
  channel: 'voice',
  status: 'SIMULATED_NOT_DIALED',
  execution_target: 'internal-voice-simulator-v1',
  payload_hash: voiceEnvelopeFixture.payload_hash,
  summary: 'The internal voice simulation ended. No number was dialed.',
  turn_count: 1,
  run_status: 'ENDED',
  research_snapshot_id: research.id,
  created_at: '2026-09-14T12:12:00Z',
};

const emailReceiptFixture: SimulationReceipt = {
  id: 'email-receipt-fixture',
  incident_id: incident.id,
  channel: 'email',
  status: 'SIMULATED_NOT_SENT',
  execution_target: 'internal-email-simulator-v1',
  payload_hash: emailDraftFixture.payload_hash,
  summary: 'Recorded in the internal demo inbox. No email was sent.',
  subject: emailDraftFixture.subject,
  research_snapshot_id: research.id,
  created_at: '2026-09-14T12:10:00Z',
};

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderOutreach(snapshot: OutreachSnapshot) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ApiContext.Provider value={createApiClient()}>
        <OfficialOutreach incident={incident} />
      </ApiContext.Provider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('official contact research', () => {
  it('requires jurisdiction confirmation and an explicit unselected contact choice', async () => {
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
    };
    const researched = {
      ...research,
      contacts: [
        contact,
        {
          ...contact,
          id: 'contact-reviewed-exception',
          agency: 'Reviewed regional authority',
          email: 'roads@transport.example.org',
          source_url: 'https://transport.example.org/contact',
          source_hostname: 'transport.example.org',
        },
        {
          ...contact,
          id: 'contact-spoofed',
          agency: 'Spoofed contact',
          source_url: 'https://www.seattle.gov.attacker.example/contact',
          source_hostname: 'www.seattle.gov',
        },
      ],
    };
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') return response(snapshot);
        if (path.endsWith('/jurisdiction-preview')) {
          snapshot = { ...snapshot, jurisdiction };
          return response(jurisdiction, 201);
        }
        if (path.endsWith('/contact-research')) {
          snapshot = { ...snapshot, research: researched };
          return response(researched, 201);
        }
        if (path.endsWith('/contact-selection')) {
          snapshot = {
            ...snapshot,
            selection,
          };
          return response(snapshot.selection, 201);
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    expect(
      await screen.findByText(
        /In an AWS deployment, Amazon Location uses this case’s saved coordinates/,
      ),
    ).toBeVisible();
    expect(
      screen.getByText(/Local development uses deterministic fixtures/),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Find official contact' }),
    );
    expect(
      await screen.findByText('Seattle, Washington, United States'),
    ).toBeVisible();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes('contact-research'),
      ),
    ).toBe(false);

    const confirmButton = screen.getByRole('button', {
      name: 'Confirm Seattle and search',
    });
    expect(confirmButton).toBeDisabled();
    fireEvent.click(
      screen.getByLabelText('I confirm this case is in Seattle, Washington.'),
    );
    fireEvent.click(confirmButton);

    const radio = await screen.findByRole('radio', {
      name: /Seattle Department of Transportation/,
    });
    expect(radio).not.toBeChecked();
    expect(
      screen.queryByRole('button', { name: 'Prepare demo email' }),
    ).not.toBeInTheDocument();
    expect(document.querySelector('a[href^="mailto:"]')).toBeNull();
    expect(document.querySelector('a[href^="tel:"]')).toBeNull();
    expect(screen.getByText('Reviewed regional authority')).toBeVisible();
    expect(screen.queryByText('Spoofed contact')).not.toBeInTheDocument();
    expect(
      screen
        .getAllByRole('link', { name: 'Open official source' })
        .map((link) => link.getAttribute('href')),
    ).toEqual([contact.source_url, 'https://transport.example.org/contact']);

    const researchRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).includes('contact-research'),
    );
    expect(JSON.parse(String(researchRequest?.[1]?.body))).toEqual({
      candidate_id: jurisdiction.id,
      context_hash: jurisdiction.context_hash,
      confirmed: true,
      refresh: false,
    });
    expect(String(researchRequest?.[1]?.body)).not.toContain(
      incident.description,
    );
    expect(String(researchRequest?.[1]?.body)).not.toContain(
      String(incident.latitude),
    );

    fireEvent.click(radio);
    fireEvent.click(
      screen.getByRole('button', { name: 'Use selected contact' }),
    );
    expect(
      await screen.findByRole('button', { name: 'Prepare demo email' }),
    ).toBeEnabled();
    expect(
      screen.getByRole('button', { name: 'Prepare demo call' }),
    ).toBeEnabled();
    for (const [, init] of fetchMock.mock.calls.filter(
      ([, value]) => value?.method === 'POST',
    )) {
      expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy();
    }
  });

  it('reuses the action key when an ambiguous preview request is retried', async () => {
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
    };
    let attempts = 0;
    const keys: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') return response(snapshot);
        if (!path.endsWith('/jurisdiction-preview'))
          throw new Error(`Unexpected request: ${path}`);
        keys.push(new Headers(init.headers).get('Idempotency-Key') || '');
        attempts += 1;
        if (attempts === 1)
          return response(
            {
              error: {
                code: 'JURISDICTION_LOOKUP_FAILED',
                message: 'The government area could not be checked.',
                retryable: true,
              },
            },
            503,
          );
        snapshot = { ...snapshot, jurisdiction };
        return response(jurisdiction, 201);
      }),
    );
    renderOutreach(snapshot);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Find official contact' }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The government area could not be checked.',
    );
    fireEvent.click(
      screen.getByRole('button', { name: 'Find official contact' }),
    );
    expect(
      await screen.findByText('Seattle, Washington, United States'),
    ).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it('distinguishes a provider failure from a successful search with no results', async () => {
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, status: 'FAILED', contacts: [] },
    };
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') return response(snapshot);
        if (path.endsWith('/contact-research'))
          return response({ ...snapshot.research, status: 'PENDING' }, 201);
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    expect(
      await screen.findByRole('heading', {
        name: 'Official contact research failed',
      }),
    ).toBeVisible();
    expect(
      screen.queryByText(/No usable official contact was found/),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Try official search again' }),
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).includes('/contact-research'),
        ),
      ).toBe(true),
    );
    const refreshRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).includes('/contact-research'),
    );
    expect(JSON.parse(String(refreshRequest?.[1]?.body))).toMatchObject({
      candidate_id: jurisdiction.id,
      context_hash: jurisdiction.context_hash,
      confirmed: true,
      refresh: true,
    });
  });

  it('shows a successful search with zero contacts as a no-result state', async () => {
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, status: 'READY', contacts: [] },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(snapshot)),
    );
    renderOutreach(snapshot);

    expect(
      await screen.findByText(/No usable official contact was found/),
    ).toBeVisible();
    expect(
      screen.queryByRole('heading', {
        name: 'Official contact research failed',
      }),
    ).not.toBeInTheDocument();
  });

  it('labels local deterministic results without claiming provider calls', async () => {
    const localProvider = 'Local deterministic contact fixture';
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction: {
        ...jurisdiction,
        provider: 'Local deterministic jurisdiction fixture',
      },
      research: { ...research, provider: localProvider },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(snapshot)),
    );
    renderOutreach(snapshot);

    expect(
      await screen.findAllByText('LOCAL DETERMINISTIC FIXTURE · NO WEB SEARCH'),
    ).toHaveLength(2);
    expect(screen.getByText(`Provider: ${localProvider}`)).toBeVisible();
    expect(screen.getByText(/no provider request/)).toBeVisible();
    expect(
      screen.getByText(/No web search or provider request occurred/),
    ).toBeVisible();
    expect(screen.queryByText('REAL CONTACT RESEARCH')).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Amazon Location Service candidate/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Searching official government sites with Brave/),
    ).not.toBeInTheDocument();
  });

  it('requires refreshed research when case context has gone stale', async () => {
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, status: 'STALE' },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(snapshot)),
    );
    renderOutreach(snapshot);

    expect(
      await screen.findByRole('heading', { name: 'Case details changed' }),
    ).toBeVisible();
    expect(
      screen.getByText(
        /category, location, jurisdiction, or prior selection changed/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Prepare demo email' }),
    ).not.toBeInTheDocument();
  });

  it('does not offer Seattle confirmation for a non-Seattle candidate', async () => {
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction: {
        ...jurisdiction,
        id: 'jurisdiction-bellevue',
        display_name: 'Bellevue, Washington, United States',
        locality: 'Bellevue',
        supported: false,
      },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => response(snapshot)),
    );
    renderOutreach(snapshot);

    expect(
      await screen.findByText(/researches Seattle, Washington cases only/),
    ).toBeVisible();
    expect(
      screen.queryByLabelText('I confirm this case is in Seattle, Washington.'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Confirm Seattle and search' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Check location again' }),
    ).toBeEnabled();
  });
});

describe('internal outreach simulations', () => {
  it('enforces the approved ninety-second call envelope', () => {
    expect(reachedVoiceCallLimit(89)).toBe(false);
    expect(reachedVoiceCallLimit(90)).toBe(true);
    expect(reachedVoiceCallLimit(91)).toBe(true);
  });

  it('does not expose voice actions when temporary playback is unavailable', async () => {
    const snapshot: OutreachSnapshot = {
      available: true,
      voice_available: false,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection,
    };
    const fetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        response(snapshot),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    expect(
      await screen.findByRole('heading', {
        name: 'Demo voice call unavailable',
      }),
    ).toBeVisible();
    expect(
      screen.getByText(/has not enabled temporary voice playback/),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Prepare demo call' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Prepare demo email' }),
    ).toBeEnabled();
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === 'POST'),
    ).toBe(false);
  });

  it('clears an interrupted voice poll and reloads its durable receipt', async () => {
    const runId = 'voice-run-interrupted';
    const envelope: VoiceEnvelope = {
      ...voiceEnvelopeFixture,
      id: 'voice-envelope-interrupted',
      revision: 1,
      payload_hash: VOICE_PAYLOAD_HASH,
      status: 'APPROVED',
      selected_contact: contact,
      research_reference: contact,
      execution_target: 'internal-voice-simulator-v1',
      facts: VOICE_FACTS,
      allowed_intents: {
        reporting_agent: ['report_issue'],
        fictional_intake_agent: ['close'],
      },
      refusal_rules: ['Do not claim a real government response.'],
      max_turns: 6,
      max_duration_seconds: 90,
      expires_at: '2099-09-14T12:30:00Z',
    };
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection,
      voice_envelope: envelope,
    };
    let statusRequests = 0;
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') {
          if (path.endsWith(`/outreach/voice/runs/${runId}`)) {
            statusRequests += 1;
            snapshot = {
              ...snapshot,
              voice_run: {
                ...voiceRunFixture,
                id: runId,
                envelope_id: envelope.id,
                payload_hash: envelope.payload_hash,
                status: 'INTERRUPTED',
                turns: [],
              },
              voice_receipt: {
                ...voiceReceiptFixture,
                id: 'voice-receipt-interrupted',
                payload_hash: envelope.payload_hash,
                summary:
                  'The temporary demo ended and no phone number was dialed.',
                run_status: 'INTERRUPTED',
              },
            };
            return response(snapshot.voice_run);
          }
          return response(snapshot);
        }
        if (path.endsWith('/outreach/voice/run')) {
          snapshot = {
            ...snapshot,
            voice_run: {
              ...voiceRunFixture,
              id: runId,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: 'GENERATING',
              turns: [],
            },
          };
          return response({
            ...snapshot.voice_run,
            playback_token: PLAYBACK_TOKEN,
          });
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start approved demo call' }),
    );

    expect(await screen.findByText('Call simulation complete')).toBeVisible();
    expect(screen.getByText(/temporary demo ended/)).toBeVisible();
    expect(statusRequests).toBe(1);
    expect(
      fetchMock.mock.calls.some(([input, init]) =>
        Boolean(
          init?.method === 'POST' && String(input).endsWith(`/${runId}/end`),
        ),
      ),
    ).toBe(false);
    expect(
      screen.queryByText(
        'Preparing validated voices… No number is being dialed.',
      ),
    ).not.toBeInTheDocument();
  });

  it('keeps email and voice approvals separate and ends a captioned call without a real send or dial', async () => {
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection,
    };
    const envelope: VoiceEnvelope = {
      ...voiceEnvelopeFixture,
      id: 'voice-envelope-1',
      revision: 1,
      payload_hash: VOICE_PAYLOAD_HASH,
      status: 'AWAITING_APPROVAL',
      selected_contact: contact,
      research_reference: contact,
      execution_target: 'internal-voice-simulator-v1',
      facts: VOICE_FACTS,
      allowed_intents: {
        reporting_agent: ['introduce', 'report_issue'],
        fictional_intake_agent: ['close'],
      },
      refusal_rules: ['Do not claim that an official received this report.'],
      max_turns: 6,
      max_duration_seconds: 90,
      expires_at: '2099-09-14T12:30:00Z',
    };
    let playbackRun: VoiceRun | undefined;
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (!init?.method || init.method === 'GET') {
          if (path.endsWith('/outreach/voice/runs/voice-run-1'))
            return response(playbackRun);
          return response(snapshot);
        }
        if (path.endsWith('/outreach/email/draft')) {
          snapshot = {
            ...snapshot,
            email_draft: {
              ...emailDraftFixture,
              id: 'email-draft-1',
              payload_hash: EMAIL_PAYLOAD_HASH,
            },
          };
          return response(snapshot.email_draft, 201);
        }
        if (path.endsWith('/outreach/email/approve')) {
          snapshot = {
            ...snapshot,
            email_draft: { ...snapshot.email_draft!, status: 'APPROVED' },
          };
          return response(snapshot.email_draft);
        }
        if (path.endsWith('/outreach/email/run')) {
          snapshot = {
            ...snapshot,
            email_receipt: {
              ...emailReceiptFixture,
              id: 'email-receipt-1',
              payload_hash: body.payload_hash,
              subject: snapshot.email_draft!.subject,
            },
          };
          return response(snapshot.email_receipt, 201);
        }
        if (path.endsWith('/outreach/voice/envelope')) {
          snapshot = { ...snapshot, voice_envelope: envelope };
          return response(envelope, 201);
        }
        if (path.endsWith('/outreach/voice/approve')) {
          snapshot = {
            ...snapshot,
            voice_envelope: { ...envelope, status: 'APPROVED' },
          };
          return response(snapshot.voice_envelope);
        }
        if (path.endsWith('/outreach/voice/run')) {
          const generating: VoiceRun = {
            ...voiceRunFixture,
            id: 'voice-run-1',
            envelope_id: envelope.id,
            payload_hash: envelope.payload_hash,
            status: 'GENERATING',
            playback_token: PLAYBACK_TOKEN,
            started_at: '2026-09-14T12:11:00Z',
            turns: [],
          };
          playbackRun = { ...generating, playback_token: undefined };
          snapshot = {
            ...snapshot,
            voice_run: {
              ...voiceRunFixture,
              id: generating.id,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: generating.status,
              started_at: generating.started_at,
              turns: [],
            },
          };
          window.setTimeout(() => {
            playbackRun = {
              ...voiceRunFixture,
              id: generating.id,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: 'RUNNING',
              transcript_expires_at: '2099-09-14T12:26:00Z',
              started_at: generating.started_at,
              turns: [
                {
                  ...voiceTurnFixture,
                  id: 'turn-1',
                  caption:
                    'I am demonstrating a pothole report for Cedar Street.',
                  audio_url: '',
                },
              ],
            };
          }, 25);
          return response(generating, 202);
        }
        if (path.endsWith('/outreach/voice/runs/voice-run-1/end')) {
          snapshot = {
            ...snapshot,
            voice_receipt: {
              ...voiceReceiptFixture,
              id: 'voice-receipt-1',
              payload_hash: envelope.payload_hash,
              turn_count: 1,
              summary: `Stopped by ${body.reason}. No number was dialed.`,
            },
          };
          return response(snapshot.voice_receipt);
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Prepare demo email' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Prepare demo call' }),
    );
    const emailApprove = await screen.findByRole('button', {
      name: 'Approve exact email simulation',
    });
    const voiceApprove = screen.getByRole('button', {
      name: 'Approve demo call envelope',
    });
    expect(screen.getByText('internal-email-simulator-v1')).toBeVisible();
    expect(screen.getByText('internal-voice-simulator-v1')).toBeVisible();
    expect(emailApprove).toBeDisabled();
    expect(voiceApprove).toBeDisabled();

    fireEvent.click(
      screen.getByLabelText(/I approve this exact internal email simulation/),
    );
    fireEvent.click(emailApprove);
    expect(
      await screen.findByRole('button', {
        name: 'Run approved email simulation',
      }),
    ).toBeEnabled();
    expect(voiceApprove).toBeDisabled();
    fireEvent.click(
      screen.getByRole('button', { name: 'Run approved email simulation' }),
    );
    expect(await screen.findByText('Email simulation recorded')).toBeVisible();
    expect(screen.getByText(/No email was sent/)).toBeVisible();

    fireEvent.click(
      screen.getByLabelText(/I approve this internal call simulation/),
    );
    fireEvent.click(voiceApprove);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Start approved demo call' }),
    );
    expect(await screen.findByText(/Preparing validated voices/)).toBeVisible();
    const play = await screen.findByRole(
      'button',
      { name: 'Play approved demo call' },
      { timeout: 3000 },
    );
    expect(
      screen.getByRole('button', { name: 'Refresh official results' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('radio', {
        name: /Seattle Department of Transportation/,
      }),
    ).toBeDisabled();
    expect(
      screen.queryByText(
        'I am demonstrating a pothole report for Cedar Street.',
      ),
    ).not.toBeInTheDocument();
    fireEvent.click(play);
    expect(
      await screen.findByText(
        'I am demonstrating a pothole report for Cedar Street.',
        {},
        { timeout: 3000 },
      ),
    ).toBeVisible();
    expect(screen.getByText('SIMULATED · NOT DIALED')).toBeVisible();
    const playbackRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/outreach/voice/runs/voice-run-1'),
    );
    expect(
      new Headers(playbackRequest?.[1]?.headers).get('X-NF-Playback-Token'),
    ).toBe(PLAYBACK_TOKEN);
    expect(String(playbackRequest?.[0])).not.toContain(PLAYBACK_TOKEN);
    fireEvent.click(screen.getByRole('button', { name: 'End demo call' }));

    expect(await screen.findByText('Call simulation complete')).toBeVisible();
    expect(
      screen.getByText(/Audio and captions were temporary and were not saved/),
    ).toBeVisible();
    await waitFor(() => {
      const endRequest = fetchMock.mock.calls.find(([input]) =>
        String(input).includes('/voice/runs/voice-run-1/end'),
      );
      expect(JSON.parse(String(endRequest?.[1]?.body))).toEqual({
        reason: 'resident',
      });
    });
    for (const [, init] of fetchMock.mock.calls.filter(
      ([, value]) => value?.method === 'POST',
    )) {
      expect(new Headers(init?.headers).get('Idempotency-Key')).toBeTruthy();
    }
  });

  it('fails closed when temporary Polly audio cannot be loaded', async () => {
    const envelope: VoiceEnvelope = {
      ...voiceEnvelopeFixture,
      id: 'voice-envelope-audio-failure',
      revision: 1,
      payload_hash: VOICE_PAYLOAD_HASH,
      status: 'APPROVED',
      selected_contact: contact,
      research_reference: contact,
      execution_target: 'internal-voice-simulator-v1',
      facts: VOICE_FACTS,
      allowed_intents: {
        reporting_agent: ['report_issue'],
        fictional_intake_agent: ['close'],
      },
      refusal_rules: ['Do not claim a real government response.'],
      max_turns: 6,
      max_duration_seconds: 90,
      expires_at: '2099-09-14T12:30:00Z',
    };
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection,
      voice_envelope: envelope,
    };
    const privateRun: VoiceRun = {
      ...voiceRunFixture,
      id: 'voice-run-audio-failure',
      envelope_id: envelope.id,
      payload_hash: envelope.payload_hash,
      status: 'RUNNING',
      transcript_expires_at: '2099-09-14T12:26:00Z',
      turns: [
        {
          ...voiceTurnFixture,
          id: 'turn-audio-failure',
          caption: 'Temporary caption for a pothole report.',
          audio_url:
            '/api/incidents/case-1/outreach/voice/runs/voice-run-audio-failure/turns/turn-audio-failure/audio',
        },
      ],
    };
    let audioAttempts = 0;
    let firstAudioSignal: AbortSignal | undefined;
    let resolveFirstAudio: (() => void) | undefined;
    let endAttempts = 0;
    const endKeys: string[] = [];
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') {
          if (path.endsWith('/voice/runs/voice-run-audio-failure'))
            return response(privateRun);
          if (path.endsWith('/turn-audio-failure/audio')) {
            audioAttempts += 1;
            if (audioAttempts === 1) {
              firstAudioSignal = init?.signal || undefined;
              return await new Promise<Response>((resolve) => {
                resolveFirstAudio = () =>
                  resolve(
                    response(
                      {
                        error: {
                          code: 'POLLY_AUDIO_FAILED',
                          message: 'Temporary Polly audio is unavailable.',
                        },
                      },
                      503,
                    ),
                  );
              });
            }
            return response(
              {
                error: {
                  code: 'POLLY_AUDIO_FAILED',
                  message: 'Temporary Polly audio is unavailable.',
                },
              },
              503,
            );
          }
          return response(snapshot);
        }
        if (path.endsWith('/outreach/voice/run')) {
          snapshot = {
            ...snapshot,
            voice_run: {
              ...voiceRunFixture,
              id: privateRun.id,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: 'RUNNING',
              turns: [],
            },
          };
          return response({
            ...snapshot.voice_run,
            id: privateRun.id,
            status: 'RUNNING',
            playback_token: PLAYBACK_TOKEN,
          });
        }
        if (path.endsWith('/voice/runs/voice-run-audio-failure/end')) {
          endAttempts += 1;
          endKeys.push(new Headers(init?.headers).get('Idempotency-Key') || '');
          if (endAttempts === 1)
            return response(
              {
                error: {
                  code: 'VOICE_CLEANUP_FAILED',
                  message: 'Temporary cleanup could not be confirmed.',
                  retryable: true,
                },
              },
              503,
            );
          snapshot = {
            ...snapshot,
            voice_receipt: {
              ...voiceReceiptFixture,
              id: 'voice-audio-failure-receipt',
              payload_hash: envelope.payload_hash,
              summary: 'Ended after audio failure. No number was dialed.',
            },
          };
          return response(snapshot.voice_receipt);
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start approved demo call' }),
    );
    fireEvent.click(
      await screen.findByRole(
        'button',
        { name: 'Play approved demo call' },
        { timeout: 3000 },
      ),
    );
    await waitFor(() => expect(audioAttempts).toBe(1));
    fireEvent.click(screen.getByRole('button', { name: 'Pause demo call' }));
    expect(firstAudioSignal?.aborted).toBe(false);
    expect(screen.getByText('Demo call paused')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Resume demo call' }));
    resolveFirstAudio?.();
    expect(
      await screen.findByText('Temporary Polly audio is unavailable.'),
    ).toBeVisible();
    expect(audioAttempts).toBe(1);
    expect(
      screen.getByRole('button', { name: 'Play approved demo call' }),
    ).toBeVisible();
    const audioRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith('/turn-audio-failure/audio'),
    );
    expect(
      new Headers(audioRequest?.[1]?.headers).get('X-NF-Playback-Token'),
    ).toBe(PLAYBACK_TOKEN);
    expect(String(audioRequest?.[0])).not.toContain(PLAYBACK_TOKEN);

    fireEvent.click(screen.getByRole('button', { name: 'End demo call' }));
    expect(
      await screen.findByRole('heading', {
        name: 'Temporary call cleanup needs retry',
      }),
    ).toBeVisible();
    expect(
      screen.queryByText('Temporary caption for a pothole report.'),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Retry ending demo call' }),
    );
    expect(await screen.findByText('Call simulation complete')).toBeVisible();
    expect(
      screen.queryByText('Temporary caption for a pothole report.'),
    ).not.toBeInTheDocument();
    expect(endKeys).toHaveLength(2);
    expect(endKeys[0]).toBeTruthy();
    expect(endKeys[1]).toBe(endKeys[0]);
  });

  it('ends a restored running call and leaves only its durable summary', async () => {
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection,
      voice_run: {
        ...voiceRunFixture,
        id: 'restored-run-1234',
        status: 'RUNNING',
        started_at: '2026-09-14T12:11:00Z',
        turns: [
          {
            ...voiceTurnFixture,
            id: 'private-turn',
            caption: 'This caption must be purged after reload.',
          },
        ],
      },
    };
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') return response(snapshot);
        if (path.endsWith('/outreach/voice/runs/restored-run-1234/end')) {
          snapshot = {
            ...snapshot,
            voice_run: { ...snapshot.voice_run!, status: 'ENDED', turns: [] },
            voice_receipt: {
              ...voiceReceiptFixture,
              id: 'restored-receipt',
              payload_hash: VOICE_PAYLOAD_HASH,
              summary: 'Stopped after reload; no phone number was dialed.',
              run_status: 'ENDED',
            },
          };
          return response(snapshot.voice_receipt);
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    expect(await screen.findByText('Call simulation complete')).toBeVisible();
    expect(screen.getByText(/Stopped after reload/)).toBeVisible();
    expect(
      screen.queryByText('This caption must be purged after reload.'),
    ).not.toBeInTheDocument();
    const endRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).includes('/voice/runs/restored-run-1234/end'),
    );
    expect(JSON.parse(String(endRequest?.[1]?.body))).toEqual({
      reason: 'resident',
    });
    expect(
      new Headers(endRequest?.[1]?.headers).get('Idempotency-Key'),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          (!init?.method || init.method === 'GET') &&
          String(input).endsWith('/outreach/voice/runs/restored-run-1234'),
      ),
    ).toBe(false);
  });

  it('purges paused captions at the absolute transcript deadline', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const now = new Date('2026-09-14T12:00:00Z');
    vi.setSystemTime(now);
    const runId = 'voice-run-absolute-expiry';
    const envelope: VoiceEnvelope = {
      ...voiceEnvelopeFixture,
      id: 'envelope-absolute-expiry',
      revision: 1,
      payload_hash: VOICE_PAYLOAD_HASH,
      status: 'APPROVED',
      selected_contact: contact,
      research_reference: contact,
      execution_target: 'internal-voice-simulator-v1',
      facts: VOICE_FACTS,
      allowed_intents: {
        reporting_agent: ['report_issue'],
        fictional_intake_agent: ['close'],
      },
      refusal_rules: ['Do not claim a real government response.'],
      max_turns: 6,
      max_duration_seconds: 90,
      expires_at: '2026-09-14T12:30:00Z',
    };
    let snapshot: OutreachSnapshot = {
      available: true,
      voice_available: true,
      jurisdiction,
      research: { ...research, selected_contact_id: contact.id },
      selection: { ...selection, selected_at: '2026-09-14T11:59:00Z' },
      voice_envelope: envelope,
    };
    let endAttempts = 0;
    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname;
        if (!init?.method || init.method === 'GET') {
          if (path.endsWith(`/outreach/voice/runs/${runId}`))
            return response({
              ...voiceRunFixture,
              id: runId,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: 'RUNNING',
              transcript_expires_at: '2026-09-14T12:00:05Z',
              turns: [
                {
                  ...voiceTurnFixture,
                  id: 'expiring-turn',
                  caption: 'This paused caption must expire.',
                  audio_url: '',
                },
              ],
            });
          return response(snapshot);
        }
        if (path.endsWith('/outreach/voice/run')) {
          snapshot = {
            ...snapshot,
            voice_run: {
              ...voiceRunFixture,
              id: runId,
              envelope_id: envelope.id,
              payload_hash: envelope.payload_hash,
              status: 'GENERATING',
              turns: [],
            },
          };
          return response(
            {
              ...snapshot.voice_run,
              playback_token: PLAYBACK_TOKEN,
            },
            202,
          );
        }
        if (path.endsWith(`/outreach/voice/runs/${runId}/end`)) {
          endAttempts += 1;
          return response(
            {
              error: {
                code: 'VOICE_CLEANUP_FAILED',
                message: 'Temporary cleanup could not be confirmed.',
                retryable: true,
              },
            },
            503,
          );
        }
        throw new Error(`Unexpected request: ${path}`);
      },
    );
    vi.stubGlobal('fetch', fetchMock);
    renderOutreach(snapshot);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Start approved demo call' }),
    );
    fireEvent.click(
      await screen.findByRole('button', { name: 'Play approved demo call' }),
    );
    expect(
      await screen.findByText('This paused caption must expire.'),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Pause demo call' }));
    expect(screen.getByText('Demo call paused')).toBeVisible();

    await vi.advanceTimersByTimeAsync(5_000);

    expect(
      await screen.findByRole('heading', {
        name: 'Temporary call cleanup needs retry',
      }),
    ).toBeVisible();
    expect(
      screen.queryByText('This paused caption must expire.'),
    ).not.toBeInTheDocument();
    expect(endAttempts).toBe(1);
    const endRequest = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith(`/outreach/voice/runs/${runId}/end`),
    );
    expect(JSON.parse(String(endRequest?.[1]?.body))).toEqual({
      reason: 'resident',
    });
    expect(
      screen.getByRole('button', { name: 'Refresh official results' }),
    ).toBeDisabled();
  });

  it.each([
    ['reload recovery', 'reload'],
    ['status lookup failure', 'status-error'],
    ['missing playback capability', 'missing-token'],
  ] as const)(
    'keeps contact mutations locked and exposes cleanup retry after %s',
    async (_label, scenario) => {
      const runId = `cleanup-${scenario}`;
      const envelope: VoiceEnvelope = {
        ...voiceEnvelopeFixture,
        id: `envelope-${scenario}`,
        revision: 1,
        payload_hash: VOICE_PAYLOAD_HASH,
        status: 'APPROVED',
        selected_contact: contact,
        research_reference: contact,
        execution_target: 'internal-voice-simulator-v1',
        facts: VOICE_FACTS,
        allowed_intents: {
          reporting_agent: ['report_issue'],
          fictional_intake_agent: ['close'],
        },
        refusal_rules: ['Do not claim a real government response.'],
        max_turns: 6,
        max_duration_seconds: 90,
        expires_at: '2099-09-14T12:30:00Z',
      };
      let snapshot: OutreachSnapshot = {
        available: true,
        voice_available: true,
        jurisdiction,
        research: { ...research, selected_contact_id: contact.id },
        selection,
        voice_envelope: envelope,
        ...(scenario === 'reload'
          ? {
              voice_run: {
                ...voiceRunFixture,
                id: runId,
                envelope_id: envelope.id,
                payload_hash: envelope.payload_hash,
                status: 'RUNNING',
                started_at: '2026-09-14T12:11:00Z',
                turns: [],
              },
            }
          : {}),
      };
      let endAttempts = 0;
      const endKeys: string[] = [];
      const fetchMock = vi.fn(
        async (input: string | URL | Request, init?: RequestInit) => {
          const path = new URL(String(input), 'http://localhost').pathname;
          if (!init?.method || init.method === 'GET') {
            if (path.endsWith(`/outreach/voice/runs/${runId}`)) {
              if (scenario !== 'status-error')
                throw new Error(`Unexpected playback request: ${path}`);
              return response(
                {
                  error: {
                    code: 'VOICE_STATUS_UNAVAILABLE',
                    message: 'Temporary call status is unavailable.',
                    retryable: true,
                  },
                },
                503,
              );
            }
            return response(snapshot);
          }
          if (path.endsWith('/outreach/voice/run')) {
            snapshot = {
              ...snapshot,
              voice_run: {
                ...voiceRunFixture,
                id: runId,
                envelope_id: envelope.id,
                payload_hash: envelope.payload_hash,
                status: 'GENERATING',
                turns: [],
              },
            };
            return response(
              {
                ...snapshot.voice_run,
                ...(scenario === 'missing-token'
                  ? {}
                  : { playback_token: PLAYBACK_TOKEN }),
              },
              202,
            );
          }
          if (path.endsWith(`/outreach/voice/runs/${runId}/end`)) {
            endAttempts += 1;
            endKeys.push(
              new Headers(init?.headers).get('Idempotency-Key') || '',
            );
            if (endAttempts === 1)
              return response(
                {
                  error: {
                    code: 'VOICE_CLEANUP_FAILED',
                    message: 'Temporary cleanup could not be confirmed.',
                    retryable: true,
                  },
                },
                503,
              );
            snapshot = {
              ...snapshot,
              voice_run: {
                ...voiceRunFixture,
                id: runId,
                envelope_id: envelope.id,
                payload_hash: envelope.payload_hash,
                status: 'ENDED',
                turns: [],
              },
              voice_receipt: {
                ...voiceReceiptFixture,
                id: `receipt-${scenario}`,
                payload_hash: envelope.payload_hash,
                summary: 'Cleanup completed. No phone number was dialed.',
                run_status: 'ENDED',
              },
            };
            return response(snapshot.voice_receipt);
          }
          throw new Error(`Unexpected request: ${path}`);
        },
      );
      vi.stubGlobal('fetch', fetchMock);
      renderOutreach(snapshot);

      if (scenario !== 'reload') {
        fireEvent.click(
          await screen.findByRole('button', {
            name: 'Start approved demo call',
          }),
        );
      }

      expect(
        await screen.findByRole('heading', {
          name: 'Temporary call cleanup needs retry',
        }),
      ).toBeVisible();
      expect(
        screen.getByRole('button', { name: 'Refresh official results' }),
      ).toBeDisabled();
      expect(
        screen.getByRole('radio', {
          name: /Seattle Department of Transportation/,
        }),
      ).toBeDisabled();
      expect(screen.getByText('SIMULATED · NOT DIALED')).toBeVisible();

      fireEvent.click(
        screen.getByRole('button', { name: 'Retry ending demo call' }),
      );
      expect(await screen.findByText('Call simulation complete')).toBeVisible();
      expect(screen.getByText(/Cleanup completed/)).toBeVisible();
      expect(endKeys).toHaveLength(2);
      expect(endKeys[0]).toBeTruthy();
      expect(endKeys[1]).toBe(endKeys[0]);

      const statusRequests = fetchMock.mock.calls.filter(
        ([input, init]) =>
          (!init?.method || init.method === 'GET') &&
          String(input).endsWith(`/outreach/voice/runs/${runId}`),
      );
      expect(statusRequests).toHaveLength(scenario === 'status-error' ? 1 : 0);
    },
  );
});
