import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import type * as GeoJSON from 'geojson';
import type { Map as MapInstance, StyleSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
maplibregl.setWorkerUrl(mapWorkerUrl);
import { MapPin } from 'lucide-react';
import type { Incident } from '../lib/api';
import { useSession } from '../lib/session';
const CENTER: [number, number] = [-122.335, 47.615];
const streets: GeoJSON.Feature[] = [];
const blocks: GeoJSON.Feature[] = [];
const parks: GeoJSON.Feature[] = [];
const trees: GeoJSON.Feature[] = [];
function polygon(x: number, y: number, w: number, h: number): GeoJSON.Polygon {
  return {
    type: 'Polygon',
    coordinates: [
      [
        [x, y],
        [x + w, y],
        [x + w, y + h],
        [x, y + h],
        [x, y],
      ],
    ],
  };
}
for (let i = -6; i <= 6; i++) {
  streets.push({
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: [
        [CENTER[0] + i * 0.002, CENTER[1] - 0.012],
        [CENTER[0] + i * 0.002, CENTER[1] + 0.012],
      ],
    },
  });
  streets.push({
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'LineString',
      coordinates: [
        [CENTER[0] - 0.018, CENTER[1] + i * 0.0014],
        [CENTER[0] + 0.018, CENTER[1] + i * 0.0014],
      ],
    },
  });
  for (let j = -6; j <= 6; j++)
    for (let k = 0; k < 4; k++) {
      if ((i === 1 || i === 2) && (j === 0 || j === 1)) continue;
      if (k === 0)
        trees.push({
          type: 'Feature',
          properties: {},
          geometry: {
            type: 'Point',
            coordinates: [
              CENTER[0] + i * 0.002 + 0.00019,
              CENTER[1] + j * 0.0014 + 0.00042,
            ],
          },
        });
      blocks.push({
        type: 'Feature',
        properties: {},
        geometry: polygon(
          CENTER[0] + i * 0.002 + 0.00032 + (k % 2) * 0.00078,
          CENTER[1] + j * 0.0014 + 0.00022 + Math.floor(k / 2) * 0.00048,
          0.00058,
          0.00034,
        ),
      });
    }
}
parks.push({
  type: 'Feature',
  properties: {},
  geometry: polygon(CENTER[0] + 0.00225, CENTER[1] + 0.0002, 0.0035, 0.00235),
});
for (let x = 0; x < 4; x++)
  for (let y = 0; y < 4; y++)
    trees.push({
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Point',
        coordinates: [
          CENTER[0] + 0.0025 + x * 0.0009,
          CENTER[1] + 0.00035 + y * 0.00066,
        ],
      },
    });
const localStyle: StyleSpecification = {
  version: 8,
  sources: {
    trees: {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: trees },
    },
    streets: {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: streets },
    },
    blocks: {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: blocks },
    },
    parks: {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: parks },
    },
  },
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#efefeb' },
    },
    {
      id: 'park',
      type: 'fill',
      source: 'parks',
      paint: { 'fill-color': '#cfdcc8' },
    },
    {
      id: 'buildings',
      type: 'fill',
      source: 'blocks',
      paint: { 'fill-color': '#deded8' },
    },
    {
      id: 'road-outline',
      type: 'line',
      source: 'streets',
      paint: { 'line-color': '#ddded9', 'line-width': 24 },
    },
    {
      id: 'roads',
      type: 'line',
      source: 'streets',
      paint: { 'line-color': '#fafaf7', 'line-width': 21 },
    },
    {
      id: 'trees',
      type: 'circle',
      source: 'trees',
      paint: {
        'circle-color': '#b8d0ad',
        'circle-opacity': 0.75,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 4, 17, 12],
      },
    },
  ],
};
export function NeighborhoodMap({
  incidents = [],
  selected,
  onSelect,
  pin,
  onPin,
  height,
}: {
  incidents?: Incident[];
  selected?: string;
  onSelect?: (id: string) => void;
  pin?: [number, number];
  onPin?: (pin: [number, number]) => void;
  height?: number;
}) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<MapInstance | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const callback = useRef(onPin);
  callback.current = onPin;
  const [error, setError] = useState('');
  const { session } = useSession();
  const aws = !session.mode.toLowerCase().includes('local');
  useEffect(() => {
    if (!host.current) return;
    const region = import.meta.env.VITE_AWS_REGION;
    const mapName = import.meta.env.VITE_LOCATION_MAP_NAME;
    const apiKey = import.meta.env.VITE_LOCATION_API_KEY;
    // Amazon Location's API-key MapLibre flow authenticates through the
    // style-descriptor URL. Do not forward this key to arbitrary style assets.
    const awsStyle =
      region && mapName && apiKey
        ? `https://maps.geo.${region}.amazonaws.com/maps/v0/maps/${encodeURIComponent(mapName)}/style-descriptor?key=${encodeURIComponent(apiKey)}`
        : undefined;
    if (aws && !awsStyle) {
      setError(
        'Amazon Location map is not configured. Use the confirmed location below.',
      );
      return;
    }
    try {
      const instance = new maplibregl.Map({
        container: host.current,
        style: aws ? awsStyle! : localStyle,
        center: pin || CENTER,
        zoom: 15.6,
        attributionControl: aws
          ? { compact: false, customAttribution: 'Amazon Location' }
          : false,
        canvasContextAttributes: { preserveDrawingBuffer: true },
      });
      instance.on('idle', () => {
        if (host.current) {
          host.current.dataset.mapReady = String(instance.isStyleLoaded());
          host.current.dataset.featureCount = String(
            instance.queryRenderedFeatures().length,
          );
        }
      });
      instance.addControl(
        new maplibregl.NavigationControl({ showCompass: false }),
        'top-right',
      );
      instance.addControl(
        new maplibregl.ScaleControl({ unit: 'metric' }),
        'bottom-left',
      );
      instance.on('click', (e) =>
        callback.current?.([
          Number(e.lngLat.lng.toFixed(6)),
          Number(e.lngLat.lat.toFixed(6)),
        ]),
      );
      instance.on('error', () =>
        setError(
          'Map could not load. You can still enter the location manually and use the case list.',
        ),
      );
      map.current = instance;
      return () => {
        markers.current.forEach((marker) => marker.remove());
        instance.remove();
        map.current = null;
      };
    } catch {
      setError(
        'Map is unavailable in this browser. Use the case list or enter a location manually.',
      );
    }
  }, [aws]);
  useEffect(() => {
    if (!map.current) return;
    markers.current.forEach((marker) => marker.remove());
    markers.current = [];
    const points = pin
      ? [
          {
            id: 'pin',
            longitude: pin[0],
            latitude: pin[1],
            title: 'Confirmed issue location',
          },
        ]
      : incidents;
    points.forEach((incident) => {
      const el = document.createElement('button');
      el.className = `map-marker ${incident.id === selected ? 'selected' : ''}`;
      el.setAttribute('aria-label', incident.title);
      el.innerHTML = '<span></span>';
      el.onclick = (e) => {
        e.stopPropagation();
        onSelect?.(incident.id);
      };
      const coincident = points.filter(
        (p) =>
          p.longitude === incident.longitude &&
          p.latitude === incident.latitude,
      );
      const offset =
        coincident.length > 1
          ? (coincident.findIndex((p) => p.id === incident.id) -
              (coincident.length - 1) / 2) *
            30
          : 0;
      const marker = new maplibregl.Marker({
        element: el,
        anchor: 'bottom',
        offset: [offset, 0],
      })
        .setLngLat([incident.longitude, incident.latitude])
        .addTo(map.current!);
      markers.current.push(marker);
    });
  }, [incidents, selected, pin?.[0], pin?.[1], onSelect]);
  useEffect(() => {
    const point = incidents.find((i) => i.id === selected);
    if (point)
      map.current?.easeTo({
        center: [point.longitude, point.latitude],
        duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 0
          : 350,
      });
  }, [selected]);
  return (
    <div
      className={`neighborhood-map ${onPin ? 'pick-map' : ''}`}
      style={height ? { height } : undefined}
    >
      <div
        ref={host}
        className="map-canvas"
        aria-label={
          onPin ? 'Select the issue location on the map' : 'Map of nearby cases'
        }
      />
      {!aws && !error && (
        <div className="map-labels" aria-hidden="true">
          <span className="street-label street-one">Maple Street</span>
          <span className="street-label street-two">Alder Avenue</span>
          <span className="street-label street-three">Pine Street</span>
          <span className="park-label">
            Maple Grove
            <br />
            Community Garden
          </span>
        </div>
      )}
      {error && (
        <div className="map-error">
          <MapPin size={30} />
          <p>{error}</p>
        </div>
      )}
      {!aws && (
        <span className="map-caption">
          Illustrative neighborhood · not a navigation map
        </span>
      )}
      {onPin && (
        <span className="map-instruction">Click the map to move your pin</span>
      )}
    </div>
  );
}
