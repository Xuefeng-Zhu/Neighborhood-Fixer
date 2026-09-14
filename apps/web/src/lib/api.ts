import { apiUrl, getAccessToken } from './auth';
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
export interface User {
  id: string;
  name: string;
  resident: string;
}
export interface Session {
  user: User;
  workspace_id: string;
  mode: string;
}
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
  constructor(
    status: number,
    error: {
      code?: string;
      message?: string;
      correlation_id?: string;
      retryable?: boolean;
    },
  ) {
    super(error.message || `Request failed (${status})`);
    this.status = status;
    this.code = error.code || 'REQUEST_FAILED';
    this.correlationId = error.correlation_id;
    this.retryable = Boolean(error.retryable);
  }
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(apiUrl(path), {
    credentials: 'include',
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.body instanceof FormData
        ? {}
        : { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      body = {};
    }
    throw new ApiError(
      response.status,
      body.error || {
        message: typeof body.detail === 'string' ? body.detail : undefined,
      },
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) });
export async function upload(file: File) {
  const data = new FormData();
  data.append('file', file);
  return request<Evidence>('/uploads', { method: 'POST', body: data });
}
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
