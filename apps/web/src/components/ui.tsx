import {
  AlertCircle,
  ArrowRight,
  LoaderCircle,
  Check,
  MapPin,
  MessageSquare,
  Accessibility,
  Construction,
  Route,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';
import { ApiError, categories, humanize } from '../lib/api';
import type { Category, Incident } from '../lib/api';
import { useEvidenceUrl } from '../lib/useEvidenceUrl';
export function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as Error;
  return (
    <div className="error-banner" role="alert">
      <AlertCircle size={18} />
      <div>
        {e.message || 'Something went wrong. Please try again.'}
        {e instanceof ApiError && e.correlationId && (
          <small>Reference: {e.correlationId}</small>
        )}
      </div>
    </div>
  );
}
export function Loading({
  children = 'Loading your cases…',
}: {
  children?: ReactNode;
}) {
  return (
    <div className="loading-state" role="status">
      <LoaderCircle size={22} className="spin" />
      {children}
    </div>
  );
}
export function CategoryIcon({
  category,
  size = 16,
}: {
  category: Category;
  size?: number;
}) {
  const Icon =
    category === 'damaged_sidewalk'
      ? Accessibility
      : category === 'pothole'
        ? Route
        : Construction;
  return <Icon size={size} strokeWidth={1.8} />;
}
export function StatusBadge({ value }: { value: string }) {
  return (
    <span className={`status-badge status-${value.toLowerCase()}`}>
      {value === 'RESIDENT_CONFIRMED_FIXED' && <Check size={13} />}{' '}
      {humanize(value)}
    </span>
  );
}
export function StatusPair({ incident }: { incident: Incident }) {
  return (
    <dl className="status-pair">
      <div>
        <dt>Agency</dt>
        <dd>
          <StatusBadge value={incident.agency_status} />
        </dd>
      </div>
      <div>
        <dt>Resolution</dt>
        <dd>
          <StatusBadge value={incident.resolution_status} />
        </dd>
      </div>
    </dl>
  );
}
export function EvidenceImage({
  url,
  category,
  large = false,
}: {
  url?: string;
  category: Category;
  large?: boolean;
}) {
  const resolved = useEvidenceUrl(url);
  return resolved ? (
    <img
      className={`evidence-image ${large ? 'large' : ''}`}
      src={resolved}
      alt={
        url?.includes('/fixtures/')
          ? 'Illustrative synthetic evidence of the public-space issue'
          : 'Evidence photo of the reported public-space issue'
      }
    />
  ) : (
    <div className={`evidence-empty ${large ? 'large' : ''}`}>
      <CategoryIcon category={category} size={large ? 44 : 30} />
      <span>No shared photo</span>
    </div>
  );
}
export function IncidentRow({
  incident,
  selected,
  onSelect,
}: {
  incident: Incident;
  selected?: boolean;
  onSelect?: (id: string) => void;
}) {
  return (
    <article className={`incident-row ${selected ? 'selected' : ''}`}>
      <button
        className="row-select"
        onClick={() => onSelect?.(incident.id)}
        aria-label={`Show ${incident.title} on map`}
      >
        <EvidenceImage
          url={incident.thumbnail_url}
          category={incident.category}
        />
      </button>
      <div className="incident-copy">
        <div className="row-meta">
          <span className="category-label">
            <CategoryIcon category={incident.category} />
            {categories[incident.category]}
          </span>
          <span className="observation-count">
            <MessageSquare size={14} />
            {incident.observation_count}{' '}
            {incident.observation_count === 1 ? 'observation' : 'observations'}
          </span>
        </div>
        <Link className="case-title" to={`/cases/${incident.id}`}>
          {incident.title}
        </Link>
        <p className="location">
          <MapPin size={16} />
          {incident.location_label}
        </p>
        <StatusPair incident={incident} />
      </div>
      <Link
        className="row-arrow"
        to={`/cases/${incident.id}`}
        aria-label={`Open ${incident.title}`}
      >
        <ArrowRight size={19} />
      </Link>
    </article>
  );
}
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <MapPin size={30} strokeWidth={1.5} />
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}

export function EvidenceLink({
  url,
  children,
  download = false,
}: {
  url: string;
  children: ReactNode;
  download?: boolean;
}) {
  const href = useEvidenceUrl(url);
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      download={download || undefined}
      aria-disabled={!href}
    >
      {children}
    </a>
  );
}
