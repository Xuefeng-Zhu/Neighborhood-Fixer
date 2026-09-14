import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  Camera,
  Check,
  Crosshair,
  FileImage,
  LockKeyhole,
  Upload,
  X,
} from 'lucide-react';
import { categoryLabels, isPending } from '../lib/api';
import { useApi } from '../lib/api-context';
import { creationAttempt } from '../lib/idempotency';
import { reportDraftKey } from '../lib/draft-lifecycle';
import type {
  Analysis,
  Evidence,
  Incident,
  Observation,
  Operation,
} from '../lib/api';
import { useSession } from '../lib/session';
import {
  ErrorMessage,
  Loading,
  StatusPair,
  EvidenceImage,
} from '../components/ui';
import { NeighborhoodMap } from '../components/NeighborhoodMap';
const schema = z.object({
  description: z
    .string()
    .min(12, 'Describe what you noticed in at least 12 characters.')
    .max(3000),
  category: z.enum(['damaged_sidewalk', 'pothole', 'walkway_obstruction']),
  location_label: z
    .string()
    .min(3, 'Enter a street, intersection, or useful landmark.'),
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  location_confirmed: z
    .boolean()
    .refine(Boolean, 'Confirm the location before continuing.'),
  asset_public: z.enum(['yes', 'no', 'unknown']),
  share_public: z.boolean(),
  share_evidence: z.boolean(),
});
type Values = z.infer<typeof schema>;
type Saved = {
  values: Values;
  evidence: Evidence[];
  step: number;
  observationId?: string;
  operationId?: string;
  operationKind?: 'analysis' | 'decision';
  analysis?: Analysis;
  candidates?: Incident[];
  creationRequest?: { key: string; body: string };
};
const initial: Values = {
  description: '',
  category: 'damaged_sidewalk',
  location_label: '',
  latitude: 47.615,
  longitude: -122.335,
  location_confirmed: false,
  asset_public: 'unknown',
  share_public: false,
  share_evidence: false,
};
const steps = [
  'Your observation',
  'Confirm location',
  'Review observations',
  'Nearby cases',
  'Review report',
  'Approve',
];
export function Report() {
  const { request, post, upload } = useApi();
  const { session } = useSession();
  const quotas = session.quotas;
  const maxUploadMB = session.mode.toLowerCase().includes('local') ? 8 : 4;
  const navigate = useNavigate();
  const client = useQueryClient();
  const storageKey = reportDraftKey(session);
  const [saved] = useState<Saved>(() => {
    try {
      return (
        JSON.parse(localStorage.getItem(storageKey) || 'null') || {
          values: initial,
          evidence: [],
          step: 0,
        }
      );
    } catch {
      return { values: initial, evidence: [], step: 0 };
    }
  });
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { ...initial, ...saved.values },
  });
  const values = form.watch();
  const [step, setStep] = useState(saved.step);
  const [evidence, setEvidence] = useState<Evidence[]>(saved.evidence);
  const [observationId, setObservationId] = useState(saved.observationId);
  const [creationRequest, setCreationRequest] = useState(saved.creationRequest);
  const [operationId, setOperationId] = useState(saved.operationId);
  const [operationKind, setOperationKind] = useState(saved.operationKind);
  const [analysis, setAnalysis] = useState(saved.analysis);
  const [candidates, setCandidates] = useState<Incident[]>(
    saved.candidates || [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [locationMessage, setLocationMessage] = useState('');
  useEffect(() => {
    localStorage.setItem(
      storageKey,
      JSON.stringify({
        values,
        evidence,
        step,
        observationId,
        operationId,
        operationKind,
        analysis,
        candidates,
        creationRequest,
      }),
    );
  }, [
    values,
    evidence,
    step,
    observationId,
    operationId,
    operationKind,
    analysis,
    candidates,
    creationRequest,
    storageKey,
  ]);
  const operation = useQuery({
    queryKey: ['operation', operationId],
    enabled: !!operationId,
    queryFn: () => request<Operation>(`/operations/${operationId}`),
    refetchInterval: (q) =>
      q.state.data && !isPending(q.state.data.status) ? false : 1500,
  });
  useEffect(() => {
    const op = operation.data;
    if (!op || isPending(op.status)) return;
    if (['FAILED', 'ERROR'].includes(op.status.toUpperCase())) {
      setError(
        new Error(
          typeof op.error === 'string'
            ? op.error
            : JSON.stringify(op.error) ||
                'The operation stopped. Your observation is saved.',
        ),
      );
      setOperationId(undefined);
      return;
    }
    if (operationKind === 'decision' && op.result?.incident_id) {
      localStorage.removeItem(storageKey);
      void client.invalidateQueries({ queryKey: ['incidents'] });
      navigate(`/cases/${op.result.incident_id}?prepared=1`);
      return;
    }
    if (op.result?.analysis) {
      setAnalysis(op.result.analysis);
      setCandidates(
        (op.result.duplicate_candidates || []).filter(
          (candidate) => !candidate.is_sample,
        ),
      );
      setOperationId(undefined);
      setStep(2);
    }
  }, [operation.data, operationKind, navigate, client, storageKey]);
  async function createObservation(payload: unknown) {
    const attempt = creationAttempt(payload, creationRequest);
    setCreationRequest(attempt);
    // Persist before the network write so a reload/retry uses the same request identity.
    const current = JSON.parse(localStorage.getItem(storageKey) || '{}');
    localStorage.setItem(
      storageKey,
      JSON.stringify({ ...current, creationRequest: attempt }),
    );
    return post<Observation>('/observations', payload, {
      headers: { 'Idempotency-Key': attempt.key },
    });
  }
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(undefined);
    try {
      await action();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function addFile(file?: File) {
    if (!file) return;
    await run(async () => {
      if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type))
        throw new Error('Choose a JPEG, PNG, or WebP image.');
      if (file.size > maxUploadMB * 1024 * 1024)
        throw new Error(`Choose an image smaller than ${maxUploadMB} MB.`);
      const item = await upload(file);
      setEvidence((previous) => [...previous, item]);
    });
  }
  async function demoPhoto() {
    await run(async () => {
      const response = await fetch('/fixtures/curb-before.png');
      if (!response.ok)
        throw new Error(
          'Illustrative demo photo could not load. You can upload your own image.',
        );
      const blob = await response.blob();
      const item = await upload(
        new File([blob], 'illustrative-curb-ramp.png', {
          type: blob.type || 'image/png',
        }),
      );
      setEvidence((previous) => [...previous, item]);
      if (!form.getValues('description'))
        form.setValue(
          'description',
          'The curb ramp at Maple Street and Alder Avenue has a broken, uneven edge. It makes crossing with a stroller difficult.',
        );
    });
  }
  async function analyze() {
    const valid = await form.trigger();
    if (!valid) return;
    await run(async () => {
      const payload: import('../lib/api').ObservationRequest = {
        ...form.getValues(),
        evidence_ids: evidence.map((e) => e.id),
      };
      const observation = observationId
        ? await request<Observation>(`/observations/${observationId}`, {
            method: 'PATCH',
            body: JSON.stringify(payload),
          })
        : await createObservation(payload);
      setObservationId(observation.id);
      const result = await post<{ operation_id: string }>(
        `/observations/${observation.id}/analyze`,
      );
      setOperationKind('analysis');
      setOperationId(result.operation_id);
      setStep(2);
    });
  }
  async function decide(incidentId?: string) {
    await run(async () => {
      const result = await post<{ operation_id: string }>(
        `/observations/${observationId}/decision`,
        { incident_id: incidentId, different_issue: !incidentId },
      );
      setOperationKind('decision');
      setOperationId(result.operation_id);
    });
  }
  function geolocate() {
    if (!navigator.geolocation) {
      setLocationMessage(
        'Location is unavailable in this browser. Enter it manually.',
      );
      return;
    }
    setLocationMessage('Waiting for your location permission…');
    navigator.geolocation.getCurrentPosition(
      (p) => {
        form.setValue('latitude', p.coords.latitude);
        form.setValue('longitude', p.coords.longitude);
        form.setValue('location_confirmed', false);
        setLocationMessage(
          'Location found. Check the pin and add a readable street or landmark.',
        );
      },
      () =>
        setLocationMessage(
          'Location was not shared. You can enter it manually.',
        ),
      { enableHighAccuracy: false, timeout: 10000 },
    );
  }
  const waiting =
    !!operationId && (!operation.data || isPending(operation.data.status));
  return (
    <main className="page report-page">
      {quotas && (
        <p className="notice small" role="status">
          Today: {quotas.reports.remaining} reports, {quotas.uploads.remaining}{' '}
          uploads and {quotas.reasoning.remaining} AI reviews remaining. Resets{' '}
          {new Date(quotas.reset_at).toLocaleString()}.
        </p>
      )}
      <Link className="back-link" to="/">
        <ArrowLeft size={16} />
        Neighborhood
      </Link>
      <div className="page-heading">
        <div>
          <h1>Report an issue</h1>
          <p>A little evidence can move a neighborhood forward.</p>
        </div>
        <span className="saved-label">
          <Check size={15} />
          Draft saved on this device
        </span>
      </div>
      <ol className="report-steps" aria-label="Report progress">
        {steps.map((label, index) => (
          <li
            key={label}
            className={
              index === step ? 'current' : index < step ? 'complete' : ''
            }
            aria-current={index === step ? 'step' : undefined}
          >
            <span>{index < step ? <Check size={13} /> : index + 1}</span>
            {label}
          </li>
        ))}
      </ol>
      <ErrorMessage error={error || operation.error} />
      <div className="report-content">
        <section className="report-main">
          {step === 0 && (
            <>
              <h2>What did you notice?</h2>
              <p className="section-intro">
                A clear photo and a few details help us prepare a useful report.
              </p>
              <div className="field">
                <label htmlFor="description">Describe the issue</label>
                <textarea
                  id="description"
                  rows={4}
                  placeholder="What is damaged or obstructed? How does it affect getting around?"
                  {...form.register('description')}
                />
                {form.formState.errors.description && (
                  <span className="field-error">
                    {form.formState.errors.description.message}
                  </span>
                )}
              </div>
              <div className="field">
                <label htmlFor="category">Issue category</label>
                <select id="category" {...form.register('category')}>
                  {Object.entries(categoryLabels).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="photo">Evidence photo</label>
                <label className="upload-zone" htmlFor="photo">
                  <Upload size={25} />
                  <strong>Choose a photo</strong>
                  <span>JPEG, PNG or WebP · up to {maxUploadMB} MB</span>
                  <input
                    id="photo"
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    onChange={(e) => {
                      void addFile(e.target.files?.[0]);
                      e.target.value = '';
                    }}
                    disabled={busy}
                  />
                </label>
              </div>
              {evidence.length > 0 && (
                <div className="upload-previews">
                  {evidence.map((item) => (
                    <div key={item.id}>
                      <EvidenceImage
                        url={`/api/evidence/${item.id}`}
                        category={values.category}
                      />
                      <button
                        aria-label="Remove photo from this report"
                        onClick={() =>
                          setEvidence(evidence.filter((e) => e.id !== item.id))
                        }
                      >
                        <X size={16} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {session.mode.toLowerCase().includes('local') && (
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => void demoPhoto()}
                >
                  <FileImage size={17} />
                  Use illustrative demo photo
                </button>
              )}
              <label className="checkbox-row">
                <input type="checkbox" {...form.register('share_public')} />
                <span>
                  Share a sanitized observation with neighbors so they can add
                  evidence.
                  <small>
                    Optional community publication. Your description and
                    approximate location become visible; private contacts and
                    original photos stay private. Sending to an agency still
                    needs separate approval.
                  </small>
                </span>
              </label>
              {values.share_public && (
                <label className="checkbox-row">
                  <input type="checkbox" {...form.register('share_evidence')} />
                  <span>
                    Also publish the photos I reviewed above.
                    <small>
                      Optional. Leave this unchecked to keep all photos private.
                      Remove or replace any image that includes details you
                      don’t want shared.
                    </small>
                  </span>
                </label>
              )}
              <div className="form-actions">
                <span className="muted small">
                  {evidence.length} photo{evidence.length === 1 ? '' : 's'}{' '}
                  attached
                </span>
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={async () => {
                    if (!(await form.trigger('description'))) return;
                    if (!evidence.length) {
                      setError(
                        new Error(
                          'Add a photo to continue. You can use the illustrative demo image.',
                        ),
                      );
                      return;
                    }
                    setError(undefined);
                    setStep(1);
                  }}
                >
                  Continue
                  <ArrowRight size={17} />
                </button>
              </div>
            </>
          )}
          {step === 1 && (
            <>
              <h2>Confirm the location</h2>
              <p className="section-intro">
                Move the pin or enter coordinates. A location does not establish
                who owns the asset.
              </p>
              <NeighborhoodMap
                pin={[values.longitude, values.latitude]}
                onPin={([lng, lat]) => {
                  form.setValue('longitude', lng);
                  form.setValue('latitude', lat);
                  form.setValue('location_confirmed', false);
                }}
                height={265}
              />
              <button className="text-button" onClick={geolocate}>
                <Crosshair size={17} />
                Use my location
              </button>
              {locationMessage && (
                <p role="status" className="muted small">
                  {locationMessage}
                </p>
              )}
              <div className="field">
                <label htmlFor="location">
                  Street, intersection or landmark
                </label>
                <input
                  id="location"
                  placeholder="Maple Street & Alder Avenue, Demo Borough"
                  {...form.register('location_label')}
                />
                {form.formState.errors.location_label && (
                  <span className="field-error">
                    {form.formState.errors.location_label.message}
                  </span>
                )}
              </div>
              <div className="field-grid">
                <div className="field">
                  <label htmlFor="latitude">Latitude</label>
                  <input
                    id="latitude"
                    type="number"
                    step="any"
                    {...form.register('latitude', { valueAsNumber: true })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="longitude">Longitude</label>
                  <input
                    id="longitude"
                    type="number"
                    step="any"
                    {...form.register('longitude', { valueAsNumber: true })}
                  />
                </div>
              </div>
              <div className="field">
                <label htmlFor="asset-public">
                  Is this a public street or walkway?
                </label>
                <select id="asset-public" {...form.register('asset_public')}>
                  <option value="unknown">I’m not sure</option>
                  <option value="yes">Yes, a public street or walkway</option>
                  <option value="no">No, private property</option>
                </select>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  {...form.register('location_confirmed')}
                />
                <span>
                  I checked the pin and confirm this is the issue’s location.
                </span>
              </label>
              {form.formState.errors.location_confirmed && (
                <span className="field-error">
                  {form.formState.errors.location_confirmed.message}
                </span>
              )}
              <div className="form-actions">
                <button className="button secondary" onClick={() => setStep(0)}>
                  <ArrowLeft size={16} />
                  Back
                </button>
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() => void analyze()}
                >
                  {busy ? 'Saving…' : 'Review observations'}
                  <ArrowRight size={16} />
                </button>
              </div>
            </>
          )}
          {step === 2 && (
            <>
              {waiting ? (
                <Loading>Checking evidence and nearby cases…</Loading>
              ) : analysis ? (
                <>
                  <h2>Here’s what we know</h2>
                  <p className="section-intro">
                    Review the observations. Uncertainty stays visible.
                  </p>
                  <AnalysisView analysis={analysis} />
                  {analysis.missing_information?.length > 0 && (
                    <div className="notice amber">
                      <strong>A little more information is needed</strong>
                      <ul>
                        {analysis.missing_information.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                      <button
                        className="text-button"
                        onClick={() => setStep(1)}
                      >
                        Update location or ownership answer
                        <ArrowRight size={15} />
                      </button>
                    </div>
                  )}
                  <div className="form-actions">
                    <button
                      className="button secondary"
                      onClick={() => setStep(0)}
                    >
                      Edit observation
                    </button>
                    <button
                      className="button primary"
                      onClick={() => setStep(3)}
                    >
                      Check nearby cases
                      <ArrowRight size={16} />
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <h2>Your observation is saved</h2>
                  <p>Run the check again when you’re ready.</p>
                  <button
                    className="button primary"
                    disabled={busy}
                    onClick={() => void analyze()}
                  >
                    Check evidence
                  </button>
                </>
              )}
            </>
          )}
          {step === 3 && (
            <>
              {waiting ? (
                <Loading>Saving your decision and preparing the case…</Loading>
              ) : (
                <>
                  <h2>
                    {candidates.length
                      ? 'Could this be the same issue?'
                      : 'Start a new case'}
                  </h2>
                  <p className="section-intro">
                    {candidates.length
                      ? 'Nearby does not always mean the same. Compare the location and description before linking your observation.'
                      : 'No matching case found in Neighborhood Fixer. We’ll prepare a report from your observation.'}
                  </p>
                  {candidates.map((candidate) => (
                    <article className="duplicate-candidate" key={candidate.id}>
                      <h3>{candidate.title}</h3>
                      <p>{candidate.description}</p>
                      <p className="location">{candidate.location_label}</p>
                      <StatusPair incident={candidate} />
                      <button
                        className="button primary"
                        disabled={busy}
                        onClick={() => void decide(candidate.id)}
                      >
                        Add my observation
                        <ArrowRight size={16} />
                      </button>
                    </article>
                  ))}
                  <div className="notice">
                    <strong>One issue, one agency report.</strong>
                    <p>
                      Adding your observation preserves your evidence and
                      follows the existing case. It won’t start another
                      submission.
                    </p>
                  </div>
                  <div className="form-actions">
                    <button
                      className="button secondary"
                      onClick={() => setStep(2)}
                    >
                      Back
                    </button>
                    <button
                      className={`button ${candidates.length ? 'secondary' : 'primary'}`}
                      disabled={busy}
                      onClick={() => void decide()}
                    >
                      {candidates.length
                        ? 'Different issue'
                        : 'Prepare my report'}
                      <ArrowRight size={16} />
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </section>
        <aside className="report-aside">
          <Camera size={27} strokeWidth={1.6} />
          <h3>A useful report starts with what you can see.</h3>
          <p>
            Describe the problem from a place you already feel comfortable. You
            don’t need to measure it or move anything.
          </p>
          <div className="aside-divider" />
          <LockKeyhole size={20} />
          <h4>You stay in control.</h4>
          <p>
            Photos and contact information stay private. You’ll review the exact
            report before anything is sent, and choose separately whether to
            share a community summary.
          </p>
          <p className="small muted">
            For non-emergency public-space maintenance only.
          </p>
        </aside>
      </div>
    </main>
  );
}
export function AnalysisView({ analysis }: { analysis: Analysis }) {
  return (
    <div className="analysis-grid">
      {[
        ['Visible observations', analysis.observed_facts],
        ['Resident statements', analysis.resident_claims],
        ['Not established', analysis.unknowns],
      ].map(([title, items]) => (
        <section key={title as string}>
          <h3>{title as string}</h3>
          {(items as string[])?.length ? (
            <ul>
              {(items as string[]).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : (
            <p className="muted">No additional information recorded.</p>
          )}
        </section>
      ))}
      <p className="provenance">{analysis.provenance}</p>
    </div>
  );
}
