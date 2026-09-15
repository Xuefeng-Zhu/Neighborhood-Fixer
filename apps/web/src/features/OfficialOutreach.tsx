import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Check,
  CirclePause,
  CirclePlay,
  ExternalLink,
  Mail,
  MapPinned,
  PhoneCall,
  RefreshCw,
  Search,
  ShieldCheck,
  Square,
} from 'lucide-react';
import { ErrorMessage, Loading } from '../components/ui';
import { dateTime, humanize } from '../lib/api';
import type {
  Incident,
  OfficialContact,
  OutreachSnapshot,
  VoiceRun,
  VoiceTurn,
} from '../lib/api';
import { useApi } from '../lib/api-context';
import { createTemporaryAudioPlayback } from '../lib/streaming-audio';

type Action =
  | 'preview'
  | 'research'
  | 'selection'
  | 'email-draft'
  | 'email-approve'
  | 'email-run'
  | 'voice-envelope'
  | 'voice-approve'
  | 'voice-run'
  | 'voice-end';
type RunAction = <T>(
  name: Action,
  operationKey: string,
  request: (idempotencyKey: string) => Promise<T>,
) => Promise<T | undefined>;

const isStatus = (status: string | undefined, expected: string) =>
  status?.toUpperCase() === expected;
const voiceTerminalStatuses = new Set([
  'FAILED',
  'EXPIRED',
  'STALE',
  'INTERRUPTED',
  'COMPLETED',
  'ENDED',
]);
const isVoiceTerminalStatus = (status: string | undefined) =>
  voiceTerminalStatuses.has(status?.toUpperCase() || '');
export const VOICE_CALL_LIMIT_SECONDS = 90;
export const reachedVoiceCallLimit = (elapsedSeconds: number) =>
  elapsedSeconds >= VOICE_CALL_LIMIT_SECONDS;
const isUnavailable = (status: string | undefined, expiresAt: string) =>
  ['EXPIRED', 'STALE'].includes(status?.toUpperCase() || '') ||
  Date.parse(expiresAt) <= Date.now();
const isLocalFixtureProvider = (provider: string) =>
  provider.startsWith('Local deterministic');

function ResearchModeBadge({ provider }: { provider: string }) {
  return (
    <span className="outreach-badge research">
      {isLocalFixtureProvider(provider)
        ? 'LOCAL DETERMINISTIC FIXTURE · NO WEB SEARCH'
        : 'REAL CONTACT RESEARCH'}
    </span>
  );
}

export function OfficialOutreach({ incident }: { incident: Incident }) {
  const api = useApi();
  const { audioStream } = api;
  const client = useQueryClient();
  const [action, setAction] = useState<Action>();
  const [error, setError] = useState<unknown>();
  const [jurisdictionConfirmed, setJurisdictionConfirmed] = useState(false);
  const [selectedContactId, setSelectedContactId] = useState('');
  const [emailConsent, setEmailConsent] = useState(false);
  const [voiceConsent, setVoiceConsent] = useState(false);
  const [voiceRun, setVoiceRun] = useState<VoiceRun>();
  const [pendingVoiceRunId, setPendingVoiceRunId] = useState<string>();
  const [playbackToken, setPlaybackToken] = useState('');
  const [endingRun, setEndingRun] = useState<{
    id: string;
    reason: 'resident' | 'completed';
  }>();
  const [completedTurns, setCompletedTurns] = useState<VoiceTurn[]>([]);
  const [currentTurn, setCurrentTurn] = useState<VoiceTurn>();
  const [callState, setCallState] = useState<
    | 'idle'
    | 'ready'
    | 'running'
    | 'paused'
    | 'blocked'
    | 'ending'
    | 'end-failed'
    | 'complete'
  >('idle');
  const [elapsed, setElapsed] = useState(0);
  const audioRef = useRef<HTMLAudioElement | undefined>(undefined);
  const audioUrlRef = useRef<string | undefined>(undefined);
  const audioFetchRef = useRef<AbortController | undefined>(undefined);
  const timerRef = useRef<number | undefined>(undefined);
  const turnIndexRef = useRef(0);
  const activeRunRef = useRef<string | undefined>(undefined);
  const idempotencyKeysRef = useRef(new Map<string, string>());
  const startingRunRef = useRef(false);
  const restoredRunRef = useRef<string | undefined>(undefined);
  const pauseRequestedRef = useRef(false);

  const query = useQuery({
    queryKey: ['outreach', incident.id],
    queryFn: ({ signal }) => api.outreach.snapshot(incident.id, { signal }),
    refetchInterval: (value) =>
      isStatus(value.state.data?.research?.status, 'PENDING') ? 1500 : false,
  });

  const playbackQuery = useQuery({
    queryKey: ['outreach-voice-run', incident.id, pendingVoiceRunId],
    queryFn: ({ signal }) =>
      api.outreach.voiceStatus(incident.id, pendingVoiceRunId!, playbackToken, {
        signal,
      }),
    enabled: Boolean(pendingVoiceRunId && playbackToken),
    refetchInterval: (value) =>
      isStatus(value.state.data?.status, 'GENERATING') ? 1500 : false,
    retry: false,
  });

  const snapshot = query.data;
  const research = snapshot?.research;
  const snapshotVoiceRunActive = Boolean(
    snapshot?.voice_run &&
    ['RUNNING', 'GENERATING'].includes(snapshot.voice_run.status.toUpperCase()),
  );
  const selectedContact =
    research && !isUnavailable(research.status, research.expires_at)
      ? research.contacts.find(
          (contact) =>
            Boolean(
              officialSourceUrl(contact.source_url, contact.source_hostname),
            ) &&
            contact.id ===
              (snapshot?.selection?.contact_id || research.selected_contact_id),
        )
      : undefined;

  useEffect(() => {
    setJurisdictionConfirmed(false);
  }, [snapshot?.jurisdiction?.id, snapshot?.jurisdiction?.context_hash]);

  useEffect(() => {
    setSelectedContactId(
      snapshot?.selection?.contact_id || research?.selected_contact_id || '',
    );
  }, [
    research?.id,
    research?.selected_contact_id,
    snapshot?.selection?.contact_id,
  ]);

  useEffect(
    () => setEmailConsent(false),
    [snapshot?.email_draft?.payload_hash],
  );
  useEffect(
    () => setVoiceConsent(false),
    [snapshot?.voice_envelope?.payload_hash],
  );

  const clearPlayback = useCallback(() => {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    audioFetchRef.current?.abort();
    audioFetchRef.current = undefined;
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.pause();
    }
    audioRef.current = undefined;
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = undefined;
  }, []);

  useEffect(
    () => () => {
      clearPlayback();
      client.removeQueries({
        queryKey: ['outreach-voice-run', incident.id],
      });
      const runId = activeRunRef.current;
      if (!runId) return;
      const key = `voice-end:${runId}:unmount`;
      const idempotencyKey =
        idempotencyKeysRef.current.get(key) || crypto.randomUUID();
      idempotencyKeysRef.current.set(key, idempotencyKey);
      void api.outreach
        .endVoice(incident.id, runId, { reason: 'resident' }, idempotencyKey, {
          keepalive: true,
        })
        .catch(() => undefined);
    },
    [api.outreach, clearPlayback, client, incident.id],
  );

  useEffect(() => {
    if (callState !== 'running') return;
    const interval = window.setInterval(() => {
      setElapsed((value) => Math.min(VOICE_CALL_LIMIT_SECONDS, value + 1));
    }, 1000);
    return () => window.clearInterval(interval);
  }, [callState]);

  async function runAction<T>(
    name: Action,
    operationKey: string,
    request: (idempotencyKey: string) => Promise<T>,
  ): Promise<T | undefined> {
    setAction(name);
    setError(undefined);
    try {
      const key = `${name}:${operationKey}`;
      const idempotencyKey =
        idempotencyKeysRef.current.get(key) || crypto.randomUUID();
      idempotencyKeysRef.current.set(key, idempotencyKey);
      const value = await request(idempotencyKey);
      idempotencyKeysRef.current.delete(key);
      await client.invalidateQueries({ queryKey: ['outreach', incident.id] });
      await client.invalidateQueries({ queryKey: ['incident', incident.id] });
      return value;
    } catch (caught) {
      setError(caught);
      return undefined;
    } finally {
      setAction(undefined);
    }
  }

  const submitVoiceEnd = useCallback(
    async (runId: string, reason: 'resident' | 'completed') => {
      setAction('voice-end');
      setError(undefined);
      setCallState('ending');
      const body = { reason };
      const key = `voice-end:${runId}:${reason}`;
      const idempotencyKey =
        idempotencyKeysRef.current.get(key) || crypto.randomUUID();
      idempotencyKeysRef.current.set(key, idempotencyKey);
      try {
        await api.outreach.endVoice(incident.id, runId, body, idempotencyKey);
      } catch (caught) {
        setError(caught);
        setCallState('end-failed');
        setAction(undefined);
        return;
      }
      idempotencyKeysRef.current.delete(key);
      activeRunRef.current = undefined;
      setEndingRun(undefined);
      setCallState('complete');
      setAction(undefined);
      await Promise.allSettled([
        client.invalidateQueries({ queryKey: ['outreach', incident.id] }),
        client.invalidateQueries({ queryKey: ['incident', incident.id] }),
      ]);
    },
    [api.outreach, client, incident.id],
  );

  const beginVoiceEnd = useCallback(
    async (runId: string, reason: 'resident' | 'completed') => {
      clearPlayback();
      activeRunRef.current = runId;
      restoredRunRef.current = runId;
      startingRunRef.current = false;
      setEndingRun({ id: runId, reason });
      setPendingVoiceRunId(undefined);
      setPlaybackToken('');
      setCallState('ending');
      setCompletedTurns([]);
      setCurrentTurn(undefined);
      setVoiceRun(undefined);
      client.removeQueries({
        queryKey: ['outreach-voice-run', incident.id, runId],
      });
      await submitVoiceEnd(runId, reason);
    },
    [clearPlayback, client, incident.id, submitVoiceEnd],
  );

  useEffect(() => {
    const restored = snapshot?.voice_run;
    if (
      !restored ||
      !['RUNNING', 'GENERATING'].includes(restored.status.toUpperCase()) ||
      voiceRun ||
      pendingVoiceRunId === restored.id ||
      startingRunRef.current ||
      restoredRunRef.current === restored.id
    )
      return;
    void beginVoiceEnd(restored.id, 'resident');
  }, [beginVoiceEnd, pendingVoiceRunId, snapshot?.voice_run, voiceRun]);

  useEffect(() => {
    const generated = playbackQuery.data;
    if (!pendingVoiceRunId || generated?.id !== pendingVoiceRunId) return;
    if (isStatus(generated.status, 'RUNNING') && generated.turns?.length) {
      turnIndexRef.current = 0;
      setElapsed(0);
      setCompletedTurns([]);
      setCurrentTurn(undefined);
      setCallState('ready');
      setPendingVoiceRunId(undefined);
      setVoiceRun(generated);
      startingRunRef.current = false;
      client.removeQueries({
        queryKey: ['outreach-voice-run', incident.id, generated.id],
      });
    } else if (isVoiceTerminalStatus(generated.status)) {
      clearPlayback();
      activeRunRef.current = undefined;
      restoredRunRef.current = generated.id;
      setPendingVoiceRunId(undefined);
      setPlaybackToken('');
      startingRunRef.current = false;
      setEndingRun(undefined);
      setVoiceRun(undefined);
      setCompletedTurns([]);
      setCurrentTurn(undefined);
      setCallState('idle');
      client.removeQueries({
        queryKey: ['outreach-voice-run', incident.id, generated.id],
      });
      void Promise.allSettled([
        client.invalidateQueries({ queryKey: ['outreach', incident.id] }),
        client.invalidateQueries({ queryKey: ['incident', incident.id] }),
      ]);
      if (isStatus(generated.status, 'FAILED'))
        setError(
          new Error(
            'The internal demo call could not be prepared. No phone number was dialed.',
          ),
        );
    }
  }, [
    clearPlayback,
    client,
    incident.id,
    pendingVoiceRunId,
    playbackQuery.data,
  ]);

  useEffect(() => {
    if (!pendingVoiceRunId || !playbackQuery.error) return;
    const runId = pendingVoiceRunId;
    void beginVoiceEnd(runId, 'resident');
  }, [beginVoiceEnd, pendingVoiceRunId, playbackQuery.error]);

  async function previewJurisdiction() {
    setSelectedContactId('');
    await runAction('preview', 'jurisdiction-preview', (key) =>
      api.outreach.previewJurisdiction(incident.id, key),
    );
  }

  async function researchContacts(confirmedOverride = false, refresh = false) {
    const jurisdiction = snapshot?.jurisdiction;
    if (!jurisdiction || (!jurisdictionConfirmed && !confirmedOverride)) return;
    setSelectedContactId('');
    await runAction(
      'research',
      `contact-research:${jurisdiction.id}:${jurisdiction.context_hash}:${refresh}`,
      (key) =>
        api.outreach.researchContacts(
          incident.id,
          {
            candidate_id: jurisdiction.id,
            context_hash: jurisdiction.context_hash,
            confirmed: true,
            refresh,
          },
          key,
        ),
    );
  }

  async function saveSelection() {
    if (!research || !selectedContactId) return;
    await runAction(
      'selection',
      `contact-selection:${research.id}:${selectedContactId}`,
      (key) =>
        api.outreach.selectContact(
          incident.id,
          { research_id: research.id, contact_id: selectedContactId },
          key,
        ),
    );
  }

  const finishPlayback = useCallback(
    async (runId: string) => beginVoiceEnd(runId, 'completed'),
    [beginVoiceEnd],
  );

  const playNext = useCallback(async () => {
    const run = voiceRun;
    if (!run) return;
    const turn = run.turns?.[turnIndexRef.current];
    if (!turn) {
      await finishPlayback(run.id);
      return;
    }
    clearPlayback();
    setCurrentTurn(turn);
    setCallState('running');
    const advance = () => {
      setCompletedTurns((items) => [...items, turn]);
      setCurrentTurn(undefined);
      turnIndexRef.current += 1;
      void playNext();
    };
    if (!turn.audio_url) {
      timerRef.current = window.setTimeout(advance, 900);
      return;
    }
    const audioFetch = new AbortController();
    try {
      audioFetchRef.current = audioFetch;
      const response = await audioStream(turn.audio_url, {
        headers: { 'X-NF-Playback-Token': playbackToken },
        signal: audioFetch.signal,
      });
      if (audioFetch.signal.aborted) return;
      const playback = await createTemporaryAudioPlayback(
        response,
        audioFetch.signal,
      );
      if (audioFetch.signal.aborted) {
        playback.audio.pause();
        URL.revokeObjectURL(playback.objectUrl);
        return;
      }
      audioUrlRef.current = playback.objectUrl;
      const audio = playback.audio;
      audioRef.current = audio;
      audio.onended = advance;
      audio.onerror = () => {
        audioFetch.abort();
        if (audioRef.current === audio) audioRef.current = undefined;
        if (audioUrlRef.current === playback.objectUrl) {
          URL.revokeObjectURL(playback.objectUrl);
          audioUrlRef.current = undefined;
        }
        setError(new Error('The approved demo audio could not be played.'));
        setCallState('blocked');
      };
      void playback.pump
        .catch((caught) => {
          if (
            audioFetch.signal.aborted ||
            (caught instanceof DOMException && caught.name === 'AbortError')
          )
            return;
          audio.pause();
          if (audioRef.current === audio) audioRef.current = undefined;
          if (audioUrlRef.current === playback.objectUrl) {
            URL.revokeObjectURL(playback.objectUrl);
            audioUrlRef.current = undefined;
          }
          setError(caught);
          setCallState('blocked');
        })
        .finally(() => {
          if (audioFetchRef.current === audioFetch)
            audioFetchRef.current = undefined;
        });
      if (!pauseRequestedRef.current) await audio.play();
    } catch (caught) {
      if (audioFetchRef.current === audioFetch)
        audioFetchRef.current = undefined;
      if (
        audioFetch.signal.aborted ||
        (caught instanceof DOMException && caught.name === 'AbortError')
      )
        return;
      setError(caught);
      setCallState('blocked');
    }
  }, [audioStream, clearPlayback, finishPlayback, playbackToken, voiceRun]);

  async function startVoiceRun() {
    const envelope = snapshot?.voice_envelope;
    if (!envelope) return;
    startingRunRef.current = true;
    const run = await runAction(
      'voice-run',
      `voice-run:${envelope.id}:${envelope.payload_hash}`,
      (key) =>
        api.outreach.runVoice(
          incident.id,
          { envelope_id: envelope.id, payload_hash: envelope.payload_hash },
          key,
        ),
    );
    if (!run) {
      startingRunRef.current = false;
      return;
    }
    activeRunRef.current = run.id;
    if (!run.playback_token) {
      await beginVoiceEnd(run.id, 'resident');
      return;
    }
    setPlaybackToken(run.playback_token);
    setPendingVoiceRunId(run.id);
    startingRunRef.current = false;
  }

  function pausePlayback() {
    if (callState === 'ready') {
      pauseRequestedRef.current = false;
      void playNext();
      return;
    }
    if (callState === 'paused' || callState === 'blocked') {
      pauseRequestedRef.current = false;
      setCallState('running');
      if (audioRef.current) {
        void audioRef.current.play().catch((caught) => {
          setError(caught);
          setCallState('blocked');
        });
      } else if (!audioFetchRef.current) void playNext();
      return;
    }
    pauseRequestedRef.current = true;
    audioRef.current?.pause();
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = undefined;
    setCallState('paused');
  }

  async function endVoiceRun(reason: 'resident' | 'completed' = 'resident') {
    if (!voiceRun) return;
    await beginVoiceEnd(voiceRun.id, reason);
  }

  async function retryVoiceEnd() {
    if (!endingRun) return;
    await submitVoiceEnd(endingRun.id, endingRun.reason);
  }

  useEffect(() => {
    if (voiceRun && callState === 'running' && reachedVoiceCallLimit(elapsed))
      void endVoiceRun('completed');
  }, [callState, elapsed, voiceRun]);

  useEffect(() => {
    if (
      !voiceRun ||
      !['ready', 'running', 'paused', 'blocked'].includes(callState)
    )
      return;
    const parsedDeadline = Date.parse(voiceRun.transcript_expires_at || '');
    const remaining = Number.isFinite(parsedDeadline)
      ? Math.max(0, parsedDeadline - Date.now())
      : 0;
    const timeout = window.setTimeout(
      () => void beginVoiceEnd(voiceRun.id, 'resident'),
      Math.min(remaining, 2_147_483_647),
    );
    return () => window.clearTimeout(timeout);
  }, [beginVoiceEnd, callState, voiceRun]);

  if (query.isPending)
    return (
      <section className="detail-section outreach-section">
        <Loading>Opening private outreach tools…</Loading>
      </section>
    );

  return (
    <section
      className="detail-section outreach-section"
      aria-labelledby="outreach-title"
    >
      <div className="section-heading outreach-heading">
        <div>
          <span className="outreach-kicker">DEMO OUTREACH</span>
          <h2 id="outreach-title">Official contact &amp; demo outreach</h2>
        </div>
        <ShieldCheck size={24} aria-hidden="true" />
      </div>
      <p className="outreach-intro">
        Research public government pages, then demonstrate a report without
        sending email or dialing a phone number.
      </p>
      <ErrorMessage error={error || query.error} />

      {snapshot && !snapshot.available ? (
        <div className="notice amber">
          <strong>Contact research is not available yet.</strong>
          <p>
            {snapshot.disabled_reason ||
              'The required providers are not configured.'}
          </p>
        </div>
      ) : !snapshot?.jurisdiction ? (
        <div className="outreach-step">
          <div className="outreach-step-icon">
            <Search size={19} />
          </div>
          <div>
            <h3>Find an official contact</h3>
            <p>
              In an AWS deployment, Amazon Location uses this case’s saved
              coordinates to suggest a government area. If you confirm Seattle,
              Brave receives only Seattle, WA and the issue category. Local
              development uses deterministic fixtures and does not call Amazon
              Location or Brave. Outreach stays inside the demo.
            </p>
            <button
              className="button primary"
              disabled={Boolean(action)}
              onClick={() => void previewJurisdiction()}
            >
              <Search size={16} />
              {action === 'preview'
                ? 'Finding jurisdiction…'
                : 'Find official contact'}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="outreach-step">
            <div className="outreach-step-icon">
              <MapPinned size={19} />
            </div>
            <div className="outreach-step-body">
              <ResearchModeBadge provider={snapshot.jurisdiction.provider} />
              <h3>Confirm the government area</h3>
              <div className="jurisdiction-card">
                <strong>{snapshot.jurisdiction.display_name}</strong>
                <span>
                  {snapshot.jurisdiction.locality},{' '}
                  {snapshot.jurisdiction.region} ·{' '}
                  {snapshot.jurisdiction.country_code}
                </span>
                <small>
                  {isLocalFixtureProvider(snapshot.jurisdiction.provider)
                    ? `${snapshot.jurisdiction.provider} · no provider request`
                    : `${snapshot.jurisdiction.provider} candidate`}{' '}
                  · expires {dateTime(snapshot.jurisdiction.expires_at)}
                </small>
              </div>
              <p className="small muted">
                A location result does not prove that this government office
                owns the street or asset.
              </p>
              {!snapshot.jurisdiction.supported && (
                <div className="notice amber">
                  Version one researches Seattle, Washington cases only. No
                  contact search occurred.
                </div>
              )}
              {isUnavailable(
                snapshot.jurisdiction.status,
                snapshot.jurisdiction.expires_at,
              ) && (
                <div className="notice amber">
                  This location candidate expired. Check the location again.
                </div>
              )}
              {!research &&
                snapshot.jurisdiction.supported &&
                !isUnavailable(
                  snapshot.jurisdiction.status,
                  snapshot.jurisdiction.expires_at,
                ) && (
                  <>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={jurisdictionConfirmed}
                        onChange={(event) =>
                          setJurisdictionConfirmed(event.target.checked)
                        }
                      />
                      <span>
                        I confirm this case is in Seattle, Washington.
                      </span>
                    </label>
                    <div className="outreach-actions">
                      <button
                        className="button primary"
                        disabled={
                          !jurisdictionConfirmed ||
                          Boolean(action) ||
                          !snapshot.jurisdiction.supported ||
                          isUnavailable(
                            snapshot.jurisdiction.status,
                            snapshot.jurisdiction.expires_at,
                          )
                        }
                        onClick={() => void researchContacts()}
                      >
                        <Search size={16} />
                        {action === 'research'
                          ? 'Searching official sources…'
                          : 'Confirm Seattle and search'}
                      </button>
                      <button
                        className="text-button"
                        disabled={Boolean(action)}
                        onClick={() => void previewJurisdiction()}
                      >
                        Check location again
                      </button>
                    </div>
                  </>
                )}
              {!research &&
                (!snapshot.jurisdiction.supported ||
                  isUnavailable(
                    snapshot.jurisdiction.status,
                    snapshot.jurisdiction.expires_at,
                  )) && (
                  <button
                    className="text-button"
                    disabled={Boolean(action)}
                    onClick={() => void previewJurisdiction()}
                  >
                    Check location again
                  </button>
                )}
            </div>
          </div>

          {research && (
            <ContactResults
              contacts={research.contacts}
              provider={research.provider}
              researchStatus={research.status}
              expiresAt={research.expires_at}
              selectedContactId={selectedContactId}
              savedContactId={
                snapshot.selection?.contact_id ||
                research.selected_contact_id ||
                undefined
              }
              disabled={
                Boolean(action) ||
                Boolean(voiceRun) ||
                Boolean(pendingVoiceRunId) ||
                snapshotVoiceRunActive ||
                callState === 'ending' ||
                callState === 'end-failed'
              }
              onSelect={setSelectedContactId}
              onSave={() => void saveSelection()}
              onRefresh={() => {
                setJurisdictionConfirmed(true);
                void researchContacts(true, true);
              }}
            />
          )}

          {selectedContact && (
            <div className="outreach-channels">
              <EmailSimulation
                incidentId={incident.id}
                snapshot={snapshot}
                contact={selectedContact}
                consent={emailConsent}
                busy={action}
                onConsent={setEmailConsent}
                onAction={runAction}
              />
              {snapshot.voice_available ? (
                <VoiceSimulation
                  incidentId={incident.id}
                  snapshot={snapshot}
                  contact={selectedContact}
                  consent={voiceConsent}
                  busy={action}
                  preparing={
                    Boolean(pendingVoiceRunId) ||
                    (snapshotVoiceRunActive && callState === 'idle')
                  }
                  callState={callState}
                  elapsed={elapsed}
                  completedTurns={completedTurns}
                  currentTurn={currentTurn}
                  onConsent={setVoiceConsent}
                  onAction={runAction}
                  onStart={startVoiceRun}
                  onPause={pausePlayback}
                  onEnd={endVoiceRun}
                  onRetryEnd={retryVoiceEnd}
                />
              ) : (
                <VoiceSimulationUnavailable />
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function VoiceSimulationUnavailable() {
  return (
    <article className="simulation-card">
      <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
      <PhoneCall size={22} />
      <h3>Demo voice call unavailable</h3>
      <p>
        This deployment has not enabled temporary voice playback. No phone
        number will be dialed.
      </p>
    </article>
  );
}

function ContactResults({
  contacts,
  provider,
  researchStatus,
  expiresAt,
  selectedContactId,
  savedContactId,
  disabled,
  onSelect,
  onSave,
  onRefresh,
}: {
  contacts: OfficialContact[];
  provider: string;
  researchStatus: string;
  expiresAt: string;
  selectedContactId: string;
  savedContactId?: string;
  disabled: boolean;
  onSelect: (id: string) => void;
  onSave: () => void;
  onRefresh: () => void;
}) {
  const visibleContacts = contacts
    .filter((contact) =>
      officialSourceUrl(contact.source_url, contact.source_hostname),
    )
    .slice(0, 3);
  const localFixture = isLocalFixtureProvider(provider);
  if (isStatus(researchStatus, 'PENDING'))
    return (
      <Loading>
        {localFixture
          ? 'Loading deterministic contact fixtures… No web search is running.'
          : `Searching official government sites with ${provider}…`}
      </Loading>
    );
  if (isStatus(researchStatus, 'FAILED'))
    return (
      <div className="outreach-step contact-results">
        <div className="outreach-step-icon">
          <Search size={19} />
        </div>
        <div className="outreach-step-body">
          <ResearchModeBadge provider={provider} />
          <h3>Official contact research failed</h3>
          <p>
            The search provider could not return verified results. No outreach
            occurred.
          </p>
          <button
            className="button secondary"
            disabled={disabled}
            onClick={onRefresh}
          >
            <RefreshCw size={14} /> Try official search again
          </button>
        </div>
      </div>
    );
  if (isUnavailable(researchStatus, expiresAt))
    return (
      <div className="outreach-step contact-results">
        <div className="outreach-step-icon">
          <Search size={19} />
        </div>
        <div className="outreach-step-body">
          <ResearchModeBadge provider={provider} />
          <h3>
            {isStatus(researchStatus, 'STALE')
              ? 'Case details changed'
              : 'Official contact research expired'}
          </h3>
          <p>
            {isStatus(researchStatus, 'STALE')
              ? 'The category, location, jurisdiction, or prior selection changed. Refresh before selecting a contact.'
              : 'Refresh the official sources before selecting a contact.'}
          </p>
          <button
            className="button secondary"
            disabled={disabled}
            onClick={onRefresh}
          >
            <RefreshCw size={14} /> Refresh official results
          </button>
        </div>
      </div>
    );
  return (
    <div className="outreach-step contact-results">
      <div className="outreach-step-icon">
        <Search size={19} />
      </div>
      <div className="outreach-step-body">
        <ResearchModeBadge provider={provider} />
        <h3>Select a public contact</h3>
        <p className="small muted">
          {localFixture
            ? 'Local development fixture only. No web search or provider request occurred.'
            : 'Research only. These details came from an official government page, but Neighborhood Fixer has not confirmed the receiving office.'}
        </p>
        <p className="small muted">Provider: {provider}</p>
        {visibleContacts.length ? (
          <fieldset className="contact-options">
            <legend className="sr-only">
              Official contact research results
            </legend>
            {visibleContacts.map((contact) => (
              <div
                className={`contact-card ${selectedContactId === contact.id ? 'selected' : ''}`}
                key={contact.id}
              >
                <input
                  id={`official-contact-${contact.id}`}
                  type="radio"
                  name="official-contact"
                  disabled={disabled}
                  checked={selectedContactId === contact.id}
                  onChange={() => onSelect(contact.id)}
                />
                <div className="contact-card-body">
                  <span className="contact-card-heading">
                    <label
                      className="contact-choice-label"
                      htmlFor={`official-contact-${contact.id}`}
                    >
                      <strong>{contact.agency}</strong>
                      {contact.role && <span>{contact.role}</span>}
                    </label>
                    <span className="official-source">Official source</span>
                  </span>
                  {contact.email && <span>Email: {contact.email}</span>}
                  {contact.phone && <span>Phone: {contact.phone}</span>}
                  {contact.match_reason && (
                    <small>{contact.match_reason}</small>
                  )}
                  <small>
                    {contact.source_hostname} · retrieved{' '}
                    {dateTime(contact.retrieved_at)}
                  </small>
                  {officialSourceUrl(
                    contact.source_url,
                    contact.source_hostname,
                  ) && (
                    <a
                      href={officialSourceUrl(
                        contact.source_url,
                        contact.source_hostname,
                      )}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open official source <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              </div>
            ))}
          </fieldset>
        ) : (
          <div className="notice amber">
            No usable official contact was found. No outreach occurred.
          </div>
        )}
        <div className="outreach-actions">
          {visibleContacts.length > 0 &&
            selectedContactId !== savedContactId && (
              <button
                className="button primary"
                disabled={!selectedContactId || disabled}
                onClick={onSave}
              >
                <Check size={16} /> Use selected contact
              </button>
            )}
          {savedContactId && (
            <span className="selected-contact-note">
              <Check size={14} /> Contact selected
            </span>
          )}
          <button
            className="text-button"
            disabled={disabled}
            onClick={onRefresh}
          >
            <RefreshCw size={14} /> Refresh official results
          </button>
        </div>
      </div>
    </div>
  );
}

function EmailSimulation({
  incidentId,
  snapshot,
  contact,
  consent,
  busy,
  onConsent,
  onAction,
}: {
  incidentId: string;
  snapshot: OutreachSnapshot;
  contact: OfficialContact;
  consent: boolean;
  busy?: Action;
  onConsent: (value: boolean) => void;
  onAction: RunAction;
}) {
  const { outreach } = useApi();
  const draft = snapshot.email_draft;
  const receipt = snapshot.email_receipt;
  if (receipt)
    return (
      <article className="simulation-card complete">
        <span className="outreach-badge email">SIMULATED · NOT SENT</span>
        <Mail size={22} />
        <h3>Email simulation recorded</h3>
        <p>
          {receipt.summary ||
            'The approved email was recorded in the internal demo inbox.'}
        </p>
        <dl className="simulation-receipt">
          <div>
            <dt>Receipt</dt>
            <dd>{receipt.id}</dd>
          </div>
          <div>
            <dt>Recorded</dt>
            <dd>{dateTime(receipt.created_at)}</dd>
          </div>
          {receipt.subject && (
            <div>
              <dt>Subject</dt>
              <dd>{receipt.subject}</dd>
            </div>
          )}
          <div>
            <dt>Target</dt>
            <dd>
              Neighborhood Fixer internal demo inbox ·{' '}
              <code>{receipt.execution_target}</code>
            </dd>
          </div>
        </dl>
      </article>
    );
  if (!draft)
    return (
      <article className="simulation-card">
        <span className="outreach-badge email">SIMULATED · NOT SENT</span>
        <Mail size={22} />
        <h3>Demo email</h3>
        <p>Prepare an exact message for the internal demo inbox.</p>
        <p className="research-reference">
          <strong>Research reference:</strong>{' '}
          {contact.email || 'No public email found'}
          <small>
            {contact.email
              ? 'This address will not be contacted.'
              : 'Email simulation is unavailable for this result.'}
          </small>
        </p>
        <button
          className="button secondary"
          disabled={!contact.email || Boolean(busy)}
          onClick={() =>
            void onAction('email-draft', 'email-draft', (key) =>
              outreach.draftEmail(incidentId, {}, key),
            )
          }
        >
          Prepare demo email
        </button>
      </article>
    );
  const approved = isStatus(draft.status, 'APPROVED');
  const unavailable = isUnavailable(draft.status, draft.expires_at);
  const researchReference =
    draft.selected_contact || draft.research_reference || contact;
  return (
    <article className="simulation-card">
      <span className="outreach-badge email">SIMULATED · NOT SENT</span>
      <Mail size={22} />
      <h3>Review exact demo email</h3>
      <p className="research-reference">
        <strong>Research reference:</strong> {researchReference.email}
        <small>This address will not be contacted.</small>
      </p>
      <p className="research-reference">
        <strong>Fixed simulation target:</strong> Neighborhood Fixer internal
        demo inbox · <code>{draft.execution_target}</code>
      </p>
      <div className="email-preview">
        <span>Subject</span>
        <strong>{draft.subject}</strong>
        <span>Exact body</span>
        <p>{draft.body}</p>
      </div>
      <details className="payload-details">
        <summary>Exact simulation integrity</summary>
        <p>
          Revision {draft.revision} · expires {dateTime(draft.expires_at)}
        </p>
        <code>{draft.payload_hash}</code>
      </details>
      {unavailable && (
        <div className="notice amber">
          This draft changed or expired. Review the latest revision.
          <button
            className="text-button"
            disabled={Boolean(busy)}
            onClick={() =>
              void onAction('email-draft', 'email-draft', (key) =>
                outreach.draftEmail(incidentId, {}, key),
              )
            }
          >
            Prepare fresh demo email
          </button>
        </div>
      )}
      {!approved ? (
        <>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => onConsent(event.target.checked)}
            />
            <span>
              I approve this exact internal email simulation. It will not be
              sent to {researchReference.email}.
            </span>
          </label>
          <button
            className="button primary full-width"
            disabled={!consent || unavailable || Boolean(busy)}
            onClick={() =>
              void onAction(
                'email-approve',
                `email-approve:${draft.id}:${draft.payload_hash}`,
                (key) =>
                  outreach.approveEmail(
                    incidentId,
                    { draft_id: draft.id, payload_hash: draft.payload_hash },
                    key,
                  ),
              )
            }
          >
            Approve exact email simulation
          </button>
        </>
      ) : (
        <button
          className="button primary full-width"
          disabled={unavailable || Boolean(busy)}
          onClick={() =>
            void onAction(
              'email-run',
              `email-run:${draft.id}:${draft.payload_hash}`,
              (key) =>
                outreach.runEmail(
                  incidentId,
                  { draft_id: draft.id, payload_hash: draft.payload_hash },
                  key,
                ),
            )
          }
        >
          {busy === 'email-run'
            ? 'Recording simulation…'
            : 'Run approved email simulation'}
        </button>
      )}
      <span className="sr-only">Case {incidentId}</span>
    </article>
  );
}

function VoiceSimulation({
  incidentId,
  snapshot,
  contact,
  consent,
  busy,
  preparing,
  callState,
  elapsed,
  completedTurns,
  currentTurn,
  onConsent,
  onAction,
  onStart,
  onPause,
  onEnd,
  onRetryEnd,
}: {
  incidentId: string;
  snapshot: OutreachSnapshot;
  contact: OfficialContact;
  consent: boolean;
  busy?: Action;
  preparing: boolean;
  callState: string;
  elapsed: number;
  completedTurns: VoiceTurn[];
  currentTurn?: VoiceTurn;
  onConsent: (value: boolean) => void;
  onAction: RunAction;
  onStart: () => Promise<void>;
  onPause: () => void;
  onEnd: () => Promise<void>;
  onRetryEnd: () => Promise<void>;
}) {
  const { outreach } = useApi();
  const envelope = snapshot.voice_envelope;
  const receipt = snapshot.voice_receipt;
  if (preparing)
    return (
      <article className="simulation-card">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <Loading>
          Preparing validated voices… No number is being dialed.
        </Loading>
      </article>
    );
  if (callState === 'ending')
    return (
      <article className="simulation-card">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <Loading>
          Ending the demo call and deleting temporary audio and captions…
        </Loading>
      </article>
    );
  if (callState === 'end-failed')
    return (
      <article className="simulation-card">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <PhoneCall size={22} />
        <h3>Temporary call cleanup needs retry</h3>
        <p>
          Playback stopped and captions were removed from this page, but the
          server has not confirmed deletion yet.
        </p>
        <button
          className="button secondary"
          disabled={Boolean(busy)}
          onClick={() => void onRetryEnd()}
        >
          Retry ending demo call
        </button>
      </article>
    );
  if (['ready', 'running', 'paused', 'blocked'].includes(callState))
    return (
      <article className="simulation-card call-live">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <div className="call-metrics" aria-label="Demo call status">
          <span>{elapsed}s elapsed</span>
          <span>
            {completedTurns.length + (currentTurn ? 1 : 0)} of{' '}
            {envelope?.max_turns || 6} turns
          </span>
        </div>
        <div className="call-status" role="status" aria-live="polite">
          {callState === 'ready'
            ? 'Approved demo call is ready'
            : callState === 'paused'
              ? 'Demo call paused'
              : callState === 'blocked'
                ? 'Audio needs your permission to continue'
                : currentTurn
                  ? `${speakerName(currentTurn.speaker)} speaking`
                  : 'Preparing next turn'}
        </div>
        {(completedTurns.length > 0 || currentTurn) && (
          <ol
            className="caption-log"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
          >
            {completedTurns.map((turn) => (
              <li key={turn.id}>
                <strong>{speakerName(turn.speaker)}</strong>
                <span>{turn.caption}</span>
              </li>
            ))}
            {currentTurn && (
              <li className="current" key={currentTurn.id}>
                <strong>{speakerName(currentTurn.speaker)}</strong>
                <span>{currentTurn.caption}</span>
              </li>
            )}
          </ol>
        )}
        <div className="outreach-actions">
          <button
            className="button secondary"
            disabled={Boolean(busy)}
            onClick={onPause}
          >
            {callState === 'ready' ||
            callState === 'paused' ||
            callState === 'blocked' ? (
              <CirclePlay size={16} />
            ) : (
              <CirclePause size={16} />
            )}
            {callState === 'ready' || callState === 'blocked'
              ? 'Play approved demo call'
              : callState === 'paused'
                ? 'Resume demo call'
                : 'Pause demo call'}
          </button>
          <button
            className="text-button danger-text"
            disabled={Boolean(busy)}
            onClick={() => void onEnd()}
          >
            <Square size={14} /> End demo call
          </button>
        </div>
      </article>
    );
  if (receipt)
    return (
      <article className="simulation-card complete">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <PhoneCall size={22} />
        <h3>Call simulation complete</h3>
        <p>
          {receipt.summary ||
            'The reporting agent completed an internal conversation with a fictional intake agent.'}
        </p>
        <dl className="simulation-receipt">
          <div>
            <dt>Receipt</dt>
            <dd>{receipt.id}</dd>
          </div>
          <div>
            <dt>Recorded</dt>
            <dd>{dateTime(receipt.created_at)}</dd>
          </div>
          {receipt.turn_count != null && (
            <div>
              <dt>Turns</dt>
              <dd>{receipt.turn_count}</dd>
            </div>
          )}
          <div>
            <dt>Target</dt>
            <dd>
              Neighborhood Fixer internal voice simulator ·{' '}
              <code>{receipt.execution_target}</code>
            </dd>
          </div>
        </dl>
        <p className="small muted">
          Audio and captions were temporary and were not saved.
        </p>
      </article>
    );
  if (!envelope)
    return (
      <article className="simulation-card">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <PhoneCall size={22} />
        <h3>Demo voice call</h3>
        <p>Prepare a bounded conversation between two internal demo agents.</p>
        <p className="research-reference">
          <strong>Research reference:</strong>{' '}
          {contact.phone || 'No public phone found'}
          <small>
            {contact.phone
              ? 'This number will not be dialed.'
              : 'Call simulation is unavailable for this result.'}
          </small>
        </p>
        <button
          className="button secondary"
          disabled={!contact.phone || Boolean(busy)}
          onClick={() =>
            void onAction('voice-envelope', 'voice-envelope', (key) =>
              outreach.draftVoice(incidentId, key),
            )
          }
        >
          Prepare demo call
        </button>
      </article>
    );
  if (isStatus(envelope.status, 'FAILED'))
    return (
      <article className="simulation-card">
        <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
        <PhoneCall size={22} />
        <h3>Demo call preparation failed</h3>
        <p>
          No phone number was dialed. Prepare a fresh envelope to try again.
        </p>
        <button
          className="button secondary"
          disabled={Boolean(busy)}
          onClick={() =>
            void onAction('voice-envelope', 'voice-envelope', (key) =>
              outreach.draftVoice(incidentId, key),
            )
          }
        >
          Prepare new demo call
        </button>
      </article>
    );
  const approved = isStatus(envelope.status, 'APPROVED');
  const unavailable = isUnavailable(envelope.status, envelope.expires_at);
  const researchReference =
    envelope.selected_contact || envelope.research_reference || contact;
  return (
    <article className="simulation-card">
      <span className="outreach-badge voice">SIMULATED · NOT DIALED</span>
      <PhoneCall size={22} />
      <h3>Review demo call envelope</h3>
      <p className="research-reference">
        <strong>Research reference:</strong> {researchReference.phone}
        <small>This number will not be dialed.</small>
      </p>
      <p className="research-reference">
        <strong>Fixed simulation target:</strong> Neighborhood Fixer reporting
        agent and fictional receiving agent ·{' '}
        <code>{envelope.execution_target}</code>
      </p>
      <div className="voice-envelope">
        <h4>Approved facts</h4>
        <dl>
          {envelope.facts.map((fact) => (
            <div key={fact.id}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
        <h4>Allowed intents</h4>
        <p>
          {Object.entries(envelope.allowed_intents)
            .map(
              ([speaker, intents]) =>
                `${speakerName(speaker)}: ${intents.map(humanize).join(', ')}`,
            )
            .join(' · ')}
        </p>
        <h4>Refusal rules</h4>
        <ul>
          {envelope.refusal_rules.map((rule) => (
            <li key={rule}>{rule}</li>
          ))}
        </ul>
        <p className="small">
          <strong>Limits:</strong> {envelope.max_turns} turns ·{' '}
          {envelope.max_duration_seconds} seconds
        </p>
      </div>
      <p className="small muted">
        Exact wording may vary inside this approved fact and intent envelope.
        Every turn is checked before playback.
      </p>
      <details className="payload-details">
        <summary>Envelope integrity</summary>
        <p>
          Revision {envelope.revision} · expires {dateTime(envelope.expires_at)}
        </p>
        <code>{envelope.payload_hash}</code>
      </details>
      {unavailable && (
        <div className="notice amber">
          This call envelope changed or expired. Review the latest revision.
          <button
            className="text-button"
            disabled={Boolean(busy)}
            onClick={() =>
              void onAction('voice-envelope', 'voice-envelope', (key) =>
                outreach.draftVoice(incidentId, key),
              )
            }
          >
            Prepare fresh demo call
          </button>
        </div>
      )}
      {!approved ? (
        <>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => onConsent(event.target.checked)}
            />
            <span>
              I approve this internal call simulation. No number will be dialed,
              my microphone will not be used, and only a summary will be kept.
            </span>
          </label>
          <button
            className="button primary full-width"
            disabled={!consent || unavailable || Boolean(busy)}
            onClick={() =>
              void onAction(
                'voice-approve',
                `voice-approve:${envelope.id}:${envelope.payload_hash}`,
                (key) =>
                  outreach.approveVoice(
                    incidentId,
                    {
                      envelope_id: envelope.id,
                      payload_hash: envelope.payload_hash,
                    },
                    key,
                  ),
              )
            }
          >
            Approve demo call envelope
          </button>
        </>
      ) : (
        <button
          className="button primary full-width"
          disabled={unavailable || Boolean(busy)}
          onClick={() => void onStart()}
        >
          <CirclePlay size={16} />
          {busy === 'voice-run'
            ? 'Preparing voices…'
            : 'Start approved demo call'}
        </button>
      )}
    </article>
  );
}

function speakerName(speaker: string) {
  if (speaker === 'reporting_agent') return 'Reporting agent';
  if (speaker === 'fictional_intake_agent') return 'Fictional intake agent';
  return humanize(speaker);
}

function officialSourceUrl(value: string, approvedHostname: string) {
  try {
    const url = new URL(value);
    const hostname = url.hostname.toLowerCase();
    const expected = approvedHostname.trim().toLowerCase();
    const isIpLiteral =
      /^\d{1,3}(?:\.\d{1,3}){3}$/.test(hostname) ||
      hostname.startsWith('[') ||
      hostname.includes(':');
    return url.protocol === 'https:' &&
      hostname === expected &&
      !hostname.includes('xn--') &&
      !isIpLiteral &&
      !url.username &&
      !url.password &&
      (!url.port || url.port === '443')
      ? url.toString()
      : undefined;
  } catch {
    return undefined;
  }
}
