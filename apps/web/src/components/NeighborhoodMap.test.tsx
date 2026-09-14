import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { NeighborhoodMap } from './NeighborhoodMap';

const { capturedOptions, session } = vi.hoisted(() => ({
  capturedOptions: [] as Record<string, unknown>[],
  session: { mode: 'local' },
}));

vi.mock('../lib/session', () => ({ useSession: () => ({ session }) }));
vi.mock('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url', () => ({
  default: 'test-worker-url',
}));
vi.mock('maplibre-gl', () => ({
  setWorkerUrl: vi.fn(),
  Map: class {
    constructor(options: Record<string, unknown>) {
      capturedOptions.push(options);
    }
    on() {}
    addControl() {}
    remove() {}
  },
  NavigationControl: class {},
  ScaleControl: class {},
}));

beforeEach(() => {
  capturedOptions.length = 0;
  session.mode = 'local';
  vi.stubEnv('VITE_AWS_REGION', 'us-east-1');
  vi.stubEnv('VITE_LOCATION_MAP_NAME', 'demo-map');
  vi.stubEnv('VITE_LOCATION_API_KEY', 'v1.public.test&key');
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe('Amazon Location map configuration', () => {
  it('keeps provider attribution expanded and clear of the app caption', () => {
    session.mode = 'aws';
    const { container } = render(<NeighborhoodMap />);
    expect(capturedOptions).toHaveLength(1);
    expect(capturedOptions[0].attributionControl).toEqual({
      compact: false,
      customAttribution: 'Amazon Location',
    });
    const styleUrl = new URL(String(capturedOptions[0].style));
    expect(styleUrl.origin).toBe('https://maps.geo.us-east-1.amazonaws.com');
    expect(styleUrl.pathname).toBe('/maps/v0/maps/demo-map/style-descriptor');
    expect(styleUrl.searchParams.get('key')).toBe('v1.public.test&key');
    expect(styleUrl.searchParams.size).toBe(1);
    expect(container.querySelector('.map-caption')).toBeNull();
    expect(capturedOptions[0].transformRequest).toBeUndefined();
  });

  it('preserves the original local map and illustrative caption', () => {
    render(<NeighborhoodMap />);
    expect(capturedOptions[0].attributionControl).toBe(false);
    expect(typeof capturedOptions[0].style).toBe('object');
    expect(
      screen.getByText('Illustrative neighborhood · not a navigation map'),
    ).toBeVisible();
  });

  it('does not construct an AWS map when its public key is missing', () => {
    session.mode = 'aws';
    vi.stubEnv('VITE_LOCATION_API_KEY', '');
    render(<NeighborhoodMap />);
    expect(capturedOptions).toHaveLength(0);
    expect(
      screen.getByText(/Amazon Location map is not configured/),
    ).toBeVisible();
  });
});
