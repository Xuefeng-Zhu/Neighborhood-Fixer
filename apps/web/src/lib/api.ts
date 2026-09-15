import { apiUrl, audioApiUrl } from './auth';
import type { components } from './generated-api';
export type Category = components['schemas']['Category'];
export type ObservationRequest = components['schemas']['ObservationInput'];
export type ApprovalRequest = components['schemas']['ApprovalInput'];
export type VerificationRequest = components['schemas']['VerificationInput'];
export type DuplicateDecisionRequest =
  components['schemas']['DuplicateDecision'];
export const categories: Record<Category, string> = {
  damaged_sidewalk: 'Sidewalks & accessibility',
  pothole: 'Roads & potholes',
  walkway_obstruction: 'Walkway obstructions',
};
export const categoryLabels: Record<Category, string> = {
  damaged_sidewalk: 'Damaged sidewalk or curb ramp',
  pothole: 'Pothole',
  walkway_obstruction: 'Object obstructing a walkway',
};
export type User = components['schemas']['User'];
export type Session = components['schemas']['SessionResponse'];
export interface Health {
  mode: string;
  integrations: Record<
    string,
    { status?: string; detail?: string; provider?: string } | string
  >;
  status?: string;
}
export interface Analysis {
  observed_facts: string[];
  resident_claims: string[];
  unknowns: string[];
  candidate_category: Category;
  missing_information: string[];
  provenance: string;
}
export interface Observation {
  id: string;
  description: string;
  category: Category;
  location_label: string;
  latitude: number;
  longitude: number;
  evidence_ids: string[];
  is_yours?: boolean;
  evidence?: {
    id: string;
    url: string;
    thumbnail_url?: string;
    public_approved?: boolean;
  }[];
  owner_id: string;
  created_at: string;
  incident_id?: string;
  analysis?: Analysis;
  asset_public?: string;
}
export type Draft = Omit<
  components['schemas']['SubmissionDraft'],
  'contact'
> & { contact: Record<string, string> };
export interface CaseEvent {
  id: string;
  type?: string;
  event_type?: string;
  message?: string;
  summary?: string;
  created_at?: string;
  timestamp?: string;
  data?: Record<string, unknown>;
}
export interface Activity {
  tool?: string;
  tool_name?: string;
  timestamp?: string;
  created_at?: string;
  summary?: string;
  result?: unknown;
  sources?: unknown[];
  agent?: string;
}
// Outreach records use the generated server contract directly. This keeps
// nullable fields, lifecycle states, and fixed simulator targets exact.
export type JurisdictionCandidate =
  components['schemas']['JurisdictionCandidate'];
export type OfficialContact = components['schemas']['OfficialContact'];
export type ContactResearch = components['schemas']['ContactResearch'];
export type ContactSelection = components['schemas']['ContactSelection'];
export type OutreachDraft = components['schemas']['OutreachDraft'];
export type VoiceFact = components['schemas']['VoiceFact'];
export type VoiceEnvelope = components['schemas']['VoiceEnvelope'];
export type VoiceTurn = components['schemas']['VoiceTurn'];
export type VoiceRun = components['schemas']['VoiceRun'];
export type SimulationReceipt = components['schemas']['SimulationReceipt'];
export type OutreachSnapshot = components['schemas']['OutreachSnapshot'];
export type OutreachApproval = components['schemas']['OutreachApproval'];
export type ContactResearchInput =
  components['schemas']['ContactResearchInput'];
export type ContactSelectionInput =
  components['schemas']['ContactSelectionInput'];
export type OutreachEmailDraftInput =
  components['schemas']['OutreachEmailDraftInput'];
export type OutreachApprovalInput =
  components['schemas']['OutreachApprovalInput'];
export type VoiceApprovalInput = components['schemas']['VoiceApprovalInput'];
export type VoiceEndInput = components['schemas']['VoiceEndInput'];
export interface Routing {
  recipient?: string;
  status?: string;
  sources?: (string | { title?: string; url?: string; description?: string })[];
  source_references?: unknown[];
  registry_review_date?: string;
  review_date?: string;
  unresolved_questions?: string[];
  decision_summary?: string;
  [key: string]: unknown;
}
export interface Incident {
  id: string;
  title: string;
  description: string;
  category: Category;
  latitude: number;
  longitude: number;
  location_label: string;
  agency_status: string;
  resolution_status: string;
  submission_status: string;
  observation_count: number;
  owner_id: string;
  following: boolean;
  shared_public: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  thumbnail_url?: string;
  next_action?: string;
  observations?: Observation[];
  verifications?: {
    id: string;
    choice: string;
    created_at: string;
    is_yours: boolean;
    note?: string;
    evidence?: { id: string; url: string };
  }[];
  events?: CaseEvent[];
  draft?: Draft | null;
  ticket?: {
    receipt_id?: string;
    url?: string;
    raw_status?: string;
    normalized_status?: string;
    closure_note?: string;
    screenshot_url?: string;
  };
  analysis?: Analysis;
  routing?: Routing;
  agent_activity?: Activity[];
  is_owner?: boolean;
  is_sample?: boolean;
}
export interface Operation {
  id: string;
  status: string;
  result?: {
    analysis?: Analysis;
    duplicate_candidates?: Incident[];
    incident_id?: string;
  };
  error?: unknown;
}
export interface Evidence {
  id: string;
  url?: string;
  filename?: string;
  [key: string]: unknown;
}
export class ApiError extends Error {
  code: string;
  correlationId?: string;
  retryable: boolean;
  status: number;
  details?: {
    resource?: string;
    limit?: number;
    remaining?: number;
    reset_at?: string;
  };
  constructor(
    status: number,
    error: {
      code?: string;
      message?: string;
      correlation_id?: string;
      retryable?: boolean;
      details?: ApiError['details'];
    },
  ) {
    super(error.message || `Request failed (${status})`);
    this.status = status;
    this.details = error.details;
    this.code = error.code || 'REQUEST_FAILED';
    this.correlationId = error.correlation_id;
    this.retryable = Boolean(error.retryable);
  }
}
export type TokenGetter = () => Promise<string | null>;
/** One transport per authenticated identity. No bearer is cached by the app. */
export function createApiClient(
  options: {
    getToken?: TokenGetter;
    credentials?: RequestCredentials;
    onWrite?: () => void;
  } = {},
) {
  let lifetime = new AbortController();
  const uploadKeys = new Map<string, string>();
  async function response(
    path: string,
    init: RequestInit = {},
    destination: 'api' | 'audio' = 'api',
  ) {
    if (!path.startsWith('/') || path.startsWith('//'))
      throw new Error('API requests must use a relative API path.');
    const signal = init.signal
      ? AbortSignal.any([lifetime.signal, init.signal])
      : lifetime.signal;
    signal.throwIfAborted();
    const token = options.getToken ? await options.getToken() : null;
    signal.throwIfAborted();
    if (options.getToken && !token)
      throw new ApiError(401, {
        code: 'SIGN_IN_REQUIRED',
        message: 'Your session has ended. Sign in again to continue.',
      });
    const headers = new Headers(init.headers);
    if (init.body && !(init.body instanceof FormData))
      headers.set('Content-Type', 'application/json');
    // Callers cannot override the current authenticated principal.
    headers.delete('Authorization');
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const result = await fetch(
      destination === 'audio' ? audioApiUrl(path) : apiUrl(path),
      {
        ...init,
        credentials: options.getToken
          ? 'omit'
          : options.credentials || 'include',
        headers,
        signal,
      },
    );
    signal.throwIfAborted();
    if (!result.ok) {
      let body;
      try {
        body = await result.json();
      } catch {
        body = {};
      }
      throw new ApiError(
        result.status,
        body.error || {
          message: typeof body.detail === 'string' ? body.detail : undefined,
        },
      );
    }
    return result;
  }
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const result = await response(path, init);
    const value =
      result.status === 204 ? (undefined as T) : await result.json();
    lifetime.signal.throwIfAborted();
    if (init.method && !['GET', 'HEAD'].includes(init.method.toUpperCase()))
      options.onWrite?.();
    return value;
  }
  const post = <T>(path: string, body: unknown = {}, init: RequestInit = {}) =>
    request<T>(path, { ...init, method: 'POST', body: JSON.stringify(body) });
  const segment = (value: string) => encodeURIComponent(value);
  const idempotency = (key: string) => ({
    headers: { 'Idempotency-Key': key },
  });
  const outreach = {
    snapshot(incidentId: string, init: RequestInit = {}) {
      return request<OutreachSnapshot>(
        `/incidents/${segment(incidentId)}/outreach`,
        init,
      );
    },
    previewJurisdiction(incidentId: string, key: string) {
      return post<JurisdictionCandidate>(
        `/incidents/${segment(incidentId)}/jurisdiction-preview`,
        {},
        idempotency(key),
      );
    },
    researchContacts(
      incidentId: string,
      data: ContactResearchInput,
      key: string,
    ) {
      return post<ContactResearch>(
        `/incidents/${segment(incidentId)}/contact-research`,
        data,
        idempotency(key),
      );
    },
    selectContact(
      incidentId: string,
      data: ContactSelectionInput,
      key: string,
    ) {
      return post<ContactSelection>(
        `/incidents/${segment(incidentId)}/contact-selection`,
        data,
        idempotency(key),
      );
    },
    draftEmail(incidentId: string, data: OutreachEmailDraftInput, key: string) {
      return post<OutreachDraft>(
        `/incidents/${segment(incidentId)}/outreach/email/draft`,
        data,
        idempotency(key),
      );
    },
    approveEmail(incidentId: string, data: OutreachApprovalInput, key: string) {
      return post<OutreachApproval>(
        `/incidents/${segment(incidentId)}/outreach/email/approve`,
        data,
        idempotency(key),
      );
    },
    runEmail(incidentId: string, data: OutreachApprovalInput, key: string) {
      return post<SimulationReceipt>(
        `/incidents/${segment(incidentId)}/outreach/email/run`,
        data,
        idempotency(key),
      );
    },
    draftVoice(incidentId: string, key: string) {
      return post<VoiceEnvelope>(
        `/incidents/${segment(incidentId)}/outreach/voice/envelope`,
        {},
        idempotency(key),
      );
    },
    approveVoice(incidentId: string, data: VoiceApprovalInput, key: string) {
      return post<OutreachApproval>(
        `/incidents/${segment(incidentId)}/outreach/voice/approve`,
        data,
        idempotency(key),
      );
    },
    runVoice(incidentId: string, data: VoiceApprovalInput, key: string) {
      return post<VoiceRun>(
        `/incidents/${segment(incidentId)}/outreach/voice/run`,
        data,
        idempotency(key),
      );
    },
    endVoice(
      incidentId: string,
      runId: string,
      data: VoiceEndInput,
      key: string,
      init: RequestInit = {},
    ) {
      return post<SimulationReceipt>(
        `/incidents/${segment(incidentId)}/outreach/voice/runs/${segment(runId)}/end`,
        data,
        {
          ...init,
          headers: {
            ...Object.fromEntries(new Headers(init.headers)),
            'Idempotency-Key': key,
          },
        },
      );
    },
    voiceStatus(
      incidentId: string,
      runId: string,
      playbackToken: string,
      init: RequestInit = {},
    ) {
      return request<VoiceRun>(
        `/incidents/${segment(incidentId)}/outreach/voice/runs/${segment(runId)}`,
        {
          ...init,
          headers: {
            ...Object.fromEntries(new Headers(init.headers)),
            'X-NF-Playback-Token': playbackToken,
          },
        },
      );
    },
  };
  async function upload(file: File, idempotencyKey?: string) {
    if (!idempotencyKey) {
      const digest = await crypto.subtle.digest(
        'SHA-256',
        await file.arrayBuffer(),
      );
      const fingerprint = Array.from(new Uint8Array(digest), (byte) =>
        byte.toString(16).padStart(2, '0'),
      ).join('');
      idempotencyKey = uploadKeys.get(fingerprint) || crypto.randomUUID();
      uploadKeys.set(fingerprint, idempotencyKey);
    }
    const data = new FormData();
    data.append('file', file);
    return request<Evidence>('/uploads', {
      method: 'POST',
      body: data,
      headers: { 'Idempotency-Key': idempotencyKey },
    });
  }
  return {
    request,
    post,
    outreach,
    upload,
    async evidence(path: string, signal?: AbortSignal) {
      return (await response(path, { signal })).blob();
    },
    async blob(path: string, init: RequestInit = {}) {
      return (await response(path, init)).blob();
    },
    async audioStream(path: string, init: RequestInit = {}) {
      return response(path, init, 'audio');
    },
    dispose() {
      lifetime.abort();
      uploadKeys.clear();
    },
    activate() {
      if (lifetime.signal.aborted) lifetime = new AbortController();
    },
  };
}
export type ApiClient = ReturnType<typeof createApiClient>;
/** Only public health and the credential-free local session use this client. */
export const localApi = createApiClient();
export const publicApi = createApiClient({ credentials: 'omit' });
export const dateTime = (value?: string) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    : '';
export function humanize(value = '') {
  return value
    .toLowerCase()
    .split('_')
    .map((s, index) => (index ? s : s.charAt(0).toUpperCase() + s.slice(1)))
    .join(' ');
}
export const isPending = (status: string) =>
  ['PENDING', 'QUEUED', 'RUNNING', 'IN_PROGRESS', 'LEASED'].includes(
    status.toUpperCase(),
  );
