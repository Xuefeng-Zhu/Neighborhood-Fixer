import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  Bell,
  BellOff,
  Check,
  ChevronDown,
  ClipboardCheck,
  Download,
  ExternalLink,
  FileCheck2,
  MapPin,
  MessageSquare,
  ShieldCheck,
} from 'lucide-react';
import { categories, dateTime, humanize } from '../lib/api';
import type { Draft, Incident } from '../lib/api';
import { useApi } from '../lib/api-context';
import {
  ErrorMessage,
  EvidenceImage,
  EvidenceLink,
  Loading,
  StatusPair,
} from '../components/ui';
import { AnalysisView } from './Report';
import { useSession } from '../lib/session';
export function CaseDetail() {
  const { request, post } = useApi();
  const { id } = useParams();
  const [search] = useSearchParams();
  const client = useQueryClient();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const query = useQuery({
    queryKey: ['incident', id],
    queryFn: () => request<Incident>(`/incidents/${id}`),
    refetchInterval: 2500,
  });
  async function mutate(path: string, body?: unknown) {
    setBusy(true);
    setError(undefined);
    try {
      await post(`/incidents/${id}/${path}`, body);
      await client.invalidateQueries({ queryKey: ['incident', id] });
      await client.invalidateQueries({ queryKey: ['incidents'] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (query.isPending)
    return (
      <main className="page">
        <Loading>Opening this case…</Loading>
      </main>
    );
  if (!query.data)
    return (
      <main className="page">
        <Link className="back-link" to="/">
          <ArrowLeft size={16} />
          Neighborhood
        </Link>
        <ErrorMessage error={query.error} />
        <button
          className="button secondary"
          onClick={() => void query.refetch()}
        >
          Try again
        </button>
      </main>
    );
  const incident = query.data;
  const evidenceUrl =
    incident.thumbnail_url ||
    incident.observations?.flatMap((o) => o.evidence || [])[0]?.url;
  const canApprove =
    !incident.is_sample &&
    incident.is_owner &&
    incident.draft &&
    ['AWAITING_APPROVAL', 'PREPARED', 'FAILED_BEFORE_SUBMISSION'].includes(
      incident.submission_status,
    );
  const canVerify =
    !incident.is_sample &&
    ['CLOSED', 'OPEN', 'IN_PROGRESS', 'RECEIVED'].includes(
      incident.agency_status,
    );
  return (
    <main className="page detail-page">
      <Link className="back-link" to="/">
        <ArrowLeft size={16} />
        Neighborhood
      </Link>
      <div className="case-heading">
        <div>
          <span className="category-label">
            {categories[incident.category]}
          </span>
          <h1>{incident.title}</h1>
          <p className="location">
            <MapPin size={17} />
            {incident.location_label}
          </p>
        </div>
        {!incident.is_sample && (
          <button
            className="button secondary"
            disabled={busy}
            aria-pressed={incident.following}
            onClick={() =>
              void mutate('subscription', { following: !incident.following })
            }
          >
            {incident.following ? <BellOff size={17} /> : <Bell size={17} />}{' '}
            {incident.following ? 'Following case' : 'Follow this case'}
          </button>
        )}
      </div>
      {incident.is_sample && (
        <div className="notice">
          Illustrative sample case · read only. Report your own observation to
          start a real case.
        </div>
      )}
      <ErrorMessage error={error || query.error} />
      {message && (
        <div className="notice success" role="status">
          <Check size={19} />
          {message}
        </div>
      )}
      {search.has('prepared') &&
        incident.is_owner &&
        ['AWAITING_APPROVAL', 'PREPARED'].includes(
          incident.submission_status,
        ) && (
          <div className="notice">
            Your observation is saved. Review the recipient and exact report
            below before approving.
          </div>
        )}
      <div className="detail-layout">
        <div className="detail-main">
          <div className="case-evidence">
            <EvidenceImage
              url={evidenceUrl}
              category={incident.category}
              large
            />
            <span>
              {evidenceUrl?.includes('/fixtures/')
                ? 'Illustrative synthetic image · fictional demo evidence'
                : 'Evidence image · unnecessary metadata removed'}
            </span>
          </div>
          <section className="detail-section">
            <div className="section-heading">
              <h2>The issue</h2>
              <span className="observation-count">
                <MessageSquare size={16} />
                {incident.observation_count}{' '}
                {incident.observation_count === 1
                  ? 'observation'
                  : 'observations'}
              </span>
            </div>
            <p className="issue-description">{incident.description}</p>
            <StatusPair incident={incident} />
            {incident.agency_status === 'CLOSED' &&
              incident.resolution_status !== 'RESIDENT_CONFIRMED_FIXED' && (
                <div className="notice amber">
                  <strong>
                    The agency closed its ticket. Is the issue actually fixed?
                  </strong>
                  <p>
                    Agency closure and physical resolution are separate. A
                    resident can verify the outcome below.
                  </p>
                  {incident.ticket?.closure_note && (
                    <p>Agency note: {incident.ticket.closure_note}</p>
                  )}
                </div>
              )}
            {incident.resolution_status === 'RESIDENT_CONFIRMED_FIXED' && (
              <div className="notice success">
                <ShieldCheck size={22} />
                <div>
                  <strong>Resident-confirmed fixed</strong>
                  <p>
                    A neighbor has confirmed the repair. This is a resident
                    observation, not a safety certification.
                  </p>
                </div>
              </div>
            )}
          </section>
          {incident.analysis && (
            <section className="detail-section">
              <h2>Evidence, with uncertainty intact</h2>
              <AnalysisView analysis={incident.analysis} />
            </section>
          )}
          {incident.verifications?.length ? (
            <section className="detail-section">
              <h2>Resident verification</h2>
              <div className="verification-history">
                {incident.verifications.map((v) => (
                  <article key={v.id}>
                    <strong>
                      {v.choice === 'looks_fixed'
                        ? 'Looks fixed'
                        : v.choice === 'still_present'
                          ? 'Still present'
                          : 'Unable to verify'}
                    </strong>
                    <time>
                      {dateTime(v.created_at)}
                      {v.is_yours ? ' · Your observation' : ''}
                    </time>
                    {v.note && <p>{v.note}</p>}
                    {v.evidence && (
                      <EvidenceImage
                        url={v.evidence.url}
                        category={incident.category}
                      />
                    )}
                  </article>
                ))}
              </div>
            </section>
          ) : null}
          <section className="detail-section">
            <h2>Case activity</h2>
            {incident.events?.length ? (
              <ol className="timeline">
                {incident.events.map((event, index) => (
                  <li key={event.id || index}>
                    <span className="timeline-dot" />
                    <div>
                      <p>
                        {event.message ||
                          event.summary ||
                          humanize(event.type || event.event_type)}
                      </p>
                      <time>
                        {dateTime(event.created_at || event.timestamp)}
                      </time>
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">No activity has been recorded yet.</p>
            )}
          </section>
          {incident.observations?.length ? (
            <section className="detail-section">
              <h2>Neighbor observations</h2>
              <div className="observations">
                {incident.observations.map((observation) => (
                  <article key={observation.id}>
                    <p>{observation.description}</p>
                    <time>{dateTime(observation.created_at)}</time>
                  </article>
                ))}
              </div>
            </section>
          ) : null}
          <details className="agent-panel">
            <summary>
              <span>
                <ClipboardCheck size={18} />
                Agent activity
              </span>
              <ChevronDown size={18} />
            </summary>
            <p className="small muted">
              Recorded tool outcomes and decision summaries. Local analysis is
              deterministic and simulated.
            </p>
            {incident.agent_activity?.length ? (
              incident.agent_activity.map((activity, index) => (
                <article key={index}>
                  <div className="section-heading">
                    <strong>
                      {activity.tool_name ||
                        activity.tool ||
                        activity.agent ||
                        'Case workflow'}
                    </strong>
                    <time>
                      {dateTime(activity.timestamp || activity.created_at)}
                    </time>
                  </div>
                  <p>
                    {activity.summary ||
                      (activity as { message?: string }).message ||
                      (typeof activity.result === 'string'
                        ? activity.result
                        : JSON.stringify(activity.result))}
                  </p>
                  {activity.sources?.length ? (
                    <p className="small">
                      Sources:{' '}
                      {activity.sources
                        .map((s) =>
                          typeof s === 'string' ? s : JSON.stringify(s),
                        )
                        .join(' · ')}
                    </p>
                  ) : null}
                </article>
              ))
            ) : (
              <p>No tool activity recorded yet.</p>
            )}
          </details>
        </div>
        <aside className="detail-rail">
          <section className="next-action">
            <span className="rail-label">NEXT STEP</span>
            <h2>{nextTitle(incident)}</h2>
            <p>{nextCopy(incident)}</p>
            {incident.submission_status === 'IN_FLIGHT' && (
              <Loading>The browser is working on the approved report…</Loading>
            )}
            {!incident.is_sample &&
              incident.submission_status === 'OUTCOME_UNKNOWN' &&
              incident.is_owner && (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => void mutate('reconcile')}
                >
                  Check for a receipt
                  <ArrowRight size={16} />
                </button>
              )}
            {['HANDOFF_REQUIRED', 'FAILED_BEFORE_SUBMISSION'].includes(
              incident.submission_status,
            ) && (
              <EvidenceLink url={`/api/incidents/${id}/packet`} download>
                <span className="button secondary">
                  <Download size={16} />
                  Download report packet
                </span>
              </EvidenceLink>
            )}
            {canApprove && (
              <ApprovalPanel
                incident={incident}
                draft={incident.draft!}
                onSuccess={() => {
                  setMessage(
                    'Approval recorded. The worker will submit this exact report.',
                  );
                  void query.refetch();
                }}
              />
            )}
            {!incident.is_sample &&
              incident.is_owner &&
              ['AWAITING_APPROVAL', 'PREPARED', 'IN_FLIGHT'].includes(
                incident.submission_status,
              ) && (
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => void mutate('cancel')}
                >
                  Cancel if not yet sent
                </button>
              )}
            {!incident.is_owner &&
              incident.submission_status === 'AWAITING_APPROVAL' && (
                <p className="small">
                  Only the report owner can approve this submission. You’ll see
                  progress as a follower.
                </p>
              )}
            {!incident.is_sample &&
              incident.is_owner &&
              !incident.draft &&
              ['PREPARED', 'AWAITING_APPROVAL'].includes(
                incident.submission_status,
              ) && (
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => void mutate('draft')}
                >
                  Prepare report
                </button>
              )}
          </section>
          {incident.ticket && (
            <section className="rail-section receipt">
              <FileCheck2 size={25} />
              <h2>Agency receipt</h2>
              <strong className="receipt-id">
                {incident.ticket.receipt_id || 'Receipt pending'}
              </strong>
              <p>
                {humanize(
                  incident.ticket.normalized_status || incident.agency_status,
                )}
              </p>
              {incident.ticket.url && (
                <a
                  className="text-button"
                  href={incident.ticket.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  View fictional agency ticket
                  <ExternalLink size={15} />
                </a>
              )}
              {incident.ticket.screenshot_url && (
                <EvidenceLink url={incident.ticket.screenshot_url}>
                  View browser receipt capture
                </EvidenceLink>
              )}
            </section>
          )}
          {canVerify && (
            <VerificationPanel
              incident={incident}
              onSuccess={() => {
                setMessage('Your verification was saved to the shared case.');
                void query.refetch();
              }}
            />
          )}
          {incident.routing && (
            <section className="rail-section">
              <h2>Why this recipient?</h2>
              <p>
                <strong>
                  {incident.routing.recipient ||
                    incident.draft?.recipient ||
                    'Routing needs clarification'}
                </strong>
              </p>
              <p className="small">
                {incident.routing.decision_summary ||
                  humanize(
                    String(
                      incident.routing.status ||
                        'Reviewed fictional agency registry',
                    ),
                  )}
              </p>
              {(incident.routing.sources || []).map((source, index) => (
                <p key={index} className="small">
                  {typeof source === 'string' ? (
                    source
                  ) : source.url ? (
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.title || source.description || 'Routing source'}
                      <ExternalLink size={12} />
                    </a>
                  ) : (
                    source.title || source.description
                  )}
                </p>
              ))}
              {(incident.routing.registry_review_date ||
                incident.routing.review_date) && (
                <p className="small muted">
                  Registry reviewed{' '}
                  {incident.routing.registry_review_date ||
                    incident.routing.review_date}
                </p>
              )}
              {incident.routing.unresolved_questions?.map((q) => (
                <p key={q} className="small">
                  {q}
                </p>
              ))}
            </section>
          )}
        </aside>
      </div>
    </main>
  );
}
function nextTitle(incident: Incident) {
  if (incident.resolution_status === 'RESIDENT_CONFIRMED_FIXED')
    return 'A repair, confirmed together.';
  if (incident.agency_status === 'CLOSED') return 'Help confirm the outcome';
  const titles: Record<string, string> = {
    AWAITING_APPROVAL: 'Your report is ready',
    PREPARED: 'Review your report',
    IN_FLIGHT: 'Submitting approved report',
    RECEIPT_CONFIRMED: 'We’re following the case',
    OUTCOME_UNKNOWN: 'Submission needs a check',
    FAILED_BEFORE_SUBMISSION: 'The report wasn’t sent',
    HANDOFF_REQUIRED: 'A person needs to take over',
    CANCELLED: 'Submission cancelled',
  };
  return titles[incident.submission_status] || 'Preparing the next step';
}
function nextCopy(incident: Incident) {
  if (incident.resolution_status === 'RESIDENT_CONFIRMED_FIXED')
    return 'Everyone following this case can see the resident-confirmed outcome.';
  if (incident.agency_status === 'CLOSED')
    return 'If you happen to pass by, share what you can observe from a comfortable place.';
  if (incident.submission_status === 'OUTCOME_UNKNOWN')
    return 'The agency may have received the report, but no receipt was captured. We will check for the existing ticket before any further action.';
  if (incident.submission_status === 'HANDOFF_REQUIRED')
    return 'Routing or the receiving portal needs human review. Your report packet is saved.';
  if (incident.submission_status === 'RECEIPT_CONFIRMED')
    return 'The receipt is saved. Status checks will appear in the timeline; agency closure won’t automatically resolve the issue.';
  if (
    incident.submission_status === 'AWAITING_APPROVAL' ||
    incident.submission_status === 'PREPARED'
  )
    return 'Check exactly what will be sent. Sending this report and sharing a community summary are separate choices.';
  return 'Your case is saved. Recorded changes appear here as the workflow progresses.';
}
export function ApprovalPanel({
  incident,
  draft,
  onSuccess,
}: {
  incident: Incident;
  draft: Draft;
  onSuccess: () => void;
}) {
  const { post } = useApi();
  const [consent, setConsent] = useState(false);
  const [publish, setPublish] = useState(incident.shared_public);
  const [publishPhotos, setPublishPhotos] = useState(
    Boolean(
      incident.observations?.some(
        (o) => o.is_yours && o.evidence?.some((e) => e.public_approved),
      ),
    ),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(draft.description);
  const [contactName, setContactName] = useState(draft.contact.name || '');
  const [contactEmail, setContactEmail] = useState(draft.contact.email || '');
  const [attachments, setAttachments] = useState(draft.attachment_ids);
  const client = useQueryClient();
  useEffect(() => {
    setConsent(false);
    setDescription(draft.description);
    setContactName(draft.contact.name || '');
    setContactEmail(draft.contact.email || '');
    setAttachments(draft.attachment_ids);
  }, [draft.id]);
  async function approve() {
    setBusy(true);
    setError(undefined);
    try {
      await post(`/incidents/${incident.id}/approve`, {
        draft_id: draft.id,
        payload_hash: draft.payload_hash,
        publish_consent: publish,
        share_evidence: publish && publishPhotos,
      });
      setConsent(false);
      onSuccess();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function saveRevision() {
    setBusy(true);
    setError(undefined);
    try {
      await post(`/incidents/${incident.id}/draft`, {
        description,
        contact: {
          ...(contactName ? { name: contactName } : {}),
          ...(contactEmail ? { email: contactEmail } : {}),
        },
        attachment_ids: attachments,
      });
      setEditing(false);
      setConsent(false);
      await client.invalidateQueries({ queryKey: ['incident', incident.id] });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="approval-panel">
      <ErrorMessage error={error} />
      <dl className="draft-fields">
        <div>
          <dt>To</dt>
          <dd>{draft.recipient}</dd>
        </div>
        <div>
          <dt>Category</dt>
          <dd>{categories[draft.category] || draft.category}</dd>
        </div>
        <div>
          <dt>Location</dt>
          <dd>
            {draft.location_label}
            <small>
              {draft.latitude}, {draft.longitude}
            </small>
          </dd>
        </div>
        <div>
          <dt>Report revision</dt>
          <dd>{draft.revision}</dd>
        </div>
      </dl>
      {editing ? (
        <>
          <div className="field">
            <label htmlFor="draft-wording">Report wording</label>
            <textarea
              id="draft-wording"
              rows={5}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="contact-name">Shared name (optional)</label>
            <input
              id="contact-name"
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="contact-email">Shared email (optional)</label>
            <input
              id="contact-email"
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
            />
          </div>
          <fieldset className="attachment-options">
            <legend>Photos to send</legend>
            {draft.attachment_ids.map((id, index) => (
              <label className="checkbox-row" key={id}>
                <input
                  type="checkbox"
                  checked={attachments.includes(id)}
                  onChange={(e) =>
                    setAttachments(
                      e.target.checked
                        ? [...attachments, id]
                        : attachments.filter((v) => v !== id),
                    )
                  }
                />
                <span>Photo {index + 1}</span>
              </label>
            ))}
          </fieldset>
          <button
            className="button primary full-width"
            onClick={() => void saveRevision()}
            disabled={busy || description.trim().length < 12}
          >
            Save new revision
          </button>
          <button className="text-button" onClick={() => setEditing(false)}>
            Keep current revision
          </button>
        </>
      ) : (
        <>
          <div className="report-wording">
            <span className="small muted">Exact report wording</span>
            <p>{draft.description}</p>
          </div>
          <p className="small">
            <strong>Shared contact:</strong>{' '}
            {Object.values(draft.contact).filter(Boolean).join(' · ') || 'None'}
          </p>
          <div className="attachment-preview">
            <span className="small">
              <strong>Attachments:</strong> {draft.attachment_ids.length}{' '}
              private photo{draft.attachment_ids.length === 1 ? '' : 's'} sent
              to the agency
            </span>
            {draft.attachment_ids.map((id, index) => (
              <EvidenceLink url={`/api/evidence/${id}`} key={id}>
                Review photo {index + 1}
                <ExternalLink size={12} />
              </EvidenceLink>
            ))}
          </div>
          <details className="payload-details">
            <summary>Exact payload integrity</summary>
            <p>Expires {dateTime(draft.expires_at)}</p>
            <code>{draft.payload_hash}</code>
            <p>Attachment SHA-256 hashes</p>
            {draft.attachment_hashes.map((hash) => (
              <code key={hash}>{hash}</code>
            ))}
          </details>
          <button className="text-button" onClick={() => setEditing(true)}>
            Edit wording, contacts or attachments
          </button>
          <div className="approval-consents">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={publish}
                onChange={(e) => setPublish(e.target.checked)}
              />
              <span>
                Share a sanitized case summary in Neighborhood.
                <small>
                  Optional. Contacts stay private. Photos remain private unless
                  separately selected below.
                </small>
              </span>
            </label>
            {publish && (
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={publishPhotos}
                  onChange={(e) => setPublishPhotos(e.target.checked)}
                />
                <span>
                  Also publish the attachment photos I reviewed.
                  <small>
                    Optional. Keep this unchecked to share only the description
                    and approximate location.
                  </small>
                </span>
              </label>
            )}
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span>
                I approve sending this exact report and these attachments to{' '}
                {draft.recipient}.
              </span>
            </label>
          </div>
          <button
            className="button primary full-width"
            disabled={!consent || busy}
            onClick={() => void approve()}
          >
            {busy ? 'Recording approval…' : 'Approve exact submission'}
            <ArrowRight size={16} />
          </button>
          <p className="small muted approval-note">
            Not ready? This report is saved. You can return from My cases.
          </p>
        </>
      )}
    </div>
  );
}
function VerificationPanel({
  incident,
  onSuccess,
}: {
  incident: Incident;
  onSuccess: () => void;
}) {
  const { post, upload } = useApi();
  const { session } = useSession();
  const maxUploadMB = session.mode.toLowerCase().includes('local') ? 8 : 4;
  const [choice, setChoice] = useState('');
  const [note, setNote] = useState('');
  const [evidenceId, setEvidenceId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  async function submit() {
    setBusy(true);
    setError(undefined);
    try {
      await post(`/incidents/${incident.id}/verify`, {
        choice,
        note,
        evidence_id: evidenceId,
      });
      onSuccess();
      setChoice('');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function addPhoto(file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      if (file.size > maxUploadMB * 1024 * 1024)
        throw new Error(`Choose an image smaller than ${maxUploadMB} MB.`);
      const e = await upload(file);
      setEvidenceId(e.id);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function demoAfter() {
    setBusy(true);
    try {
      const response = await fetch('/fixtures/curb-after.png');
      if (!response.ok)
        throw new Error('Could not load the illustrative after-photo.');
      const blob = await response.blob();
      await addPhoto(
        new File([blob], 'illustrative-after.png', {
          type: blob.type || 'image/png',
        }),
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="rail-section verification">
      <h2>What do you see?</h2>
      <p className="small">
        Optional. Only verify from somewhere you feel comfortable; no extra trip
        or inspection is needed.
      </p>
      <ErrorMessage error={error} />
      <fieldset>
        <legend className="sr-only">Verification outcome</legend>
        {[
          ['looks_fixed', 'Looks fixed'],
          ['still_present', 'Still present'],
          ['unable_to_verify', 'Unable to verify'],
        ].map(([value, label]) => (
          <label
            className={`verification-option ${choice === value ? 'selected' : ''}`}
            key={value}
          >
            <input
              type="radio"
              name="verification"
              value={value}
              checked={choice === value}
              onChange={() => setChoice(value)}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {choice && (
        <>
          <div className="field">
            <label htmlFor="verification-note">Note (optional)</label>
            <textarea
              id="verification-note"
              rows={2}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
            />
          </div>
          <div className="field">
            <label htmlFor="after-photo">
              After-photo (optional, up to {maxUploadMB} MB)
            </label>
            <input
              id="after-photo"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => void addPhoto(e.target.files?.[0])}
            />
            {evidenceId && <span className="small">Photo attached</span>}
          </div>
          {session.mode.toLowerCase().includes('local') && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => void demoAfter()}
            >
              Use illustrative after-photo
            </button>
          )}
          <button
            className="button primary full-width"
            disabled={busy}
            onClick={() => void submit()}
          >
            {busy ? 'Saving…' : 'Save verification'}
          </button>
        </>
      )}
    </section>
  );
}
