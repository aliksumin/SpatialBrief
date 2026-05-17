import React, { Suspense, useState, useEffect, useMemo, useRef, useCallback } from 'react';
import * as THREE from 'three';
import { Canvas, useThree, useFrame } from '@react-three/fiber';
import { OrbitControls, Line, Html } from '@react-three/drei';

interface ThreeSceneProps {
  selectedNode: number | null;
  activeNode: number;
  onSelectNode?: (nodeId: number | null) => void;
  onSelectVector?: (vec: any | null) => void;
  projectData?: any;
}

const ZONE_COLORS: Record<string, string> = {
  plot_boundary: '#3b82f6',
  buildable_envelope: '#f97316',
  infrastructure_zone: '#94a3b8',
  landscape_zone: '#22c55e',
  restriction_line: '#ef4444',
  zone_boundary: '#f59e0b',
  parcel_line: '#06b6d4',
  sub_zone: '#8b5cf6',
  major_boundary: '#e2e8f0',
  filled_zone: '#fb923c',
  no_build_zone: '#ef4444',
  uncategorized_zone: '#f97316',
  traffic_zone: '#64748b',
  cad_context: '#64748b',
  context_line: '#64748b',
  minor_context: '#475569',
};

const ZONE_LABELS: Record<string, string> = {
  plot_boundary: 'Plot Boundary',
  buildable_envelope: 'Buildable',
  infrastructure_zone: 'Infrastructure',
  landscape_zone: 'Landscape',
  restriction_line: 'Restriction',
  zone_boundary: 'Zone Boundary',
  parcel_line: 'Parcel',
  sub_zone: 'Building',
  major_boundary: 'Boundary',
  filled_zone: 'Zone',
  no_build_zone: 'No-Build',
  uncategorized_zone: 'Zone',
  traffic_zone: 'Traffic',
  cad_context: 'CAD Context',
  context_line: 'Context',
  minor_context: 'Minor',
};

/* Line style config per zone type */
const LINE_STYLES: Record<string, { width: number; dashed?: boolean; dashScale?: number }> = {
  plot_boundary:      { width: 3.5 },
  zone_boundary:      { width: 2.2 },
  parcel_line:        { width: 1.8, dashed: true, dashScale: 8 },
  restriction_line:   { width: 2.2 },
  major_boundary:     { width: 1.2 },
  sub_zone:           { width: 1.5, dashed: true, dashScale: 8 },
  infrastructure_zone:{ width: 1.5, dashed: true, dashScale: 6 },
  context_line:       { width: 1.0, dashed: true, dashScale: 10 },
  minor_context:      { width: 0.8 },
};

/* Category buckets — matches extraction hierarchy: Boundaries > Zones > Buildings */
const BOUNDARY_TYPES = ['plot_boundary', 'zone_boundary', 'parcel_line', 'major_boundary', 'restriction_line'];
const ZONE_TYPES = ['buildable_envelope', 'landscape_zone', 'infrastructure_zone', 'filled_zone', 'no_build_zone', 'uncategorized_zone', 'traffic_zone'];
const BUILDING_TYPES = ['sub_zone'];
const INFRA_TYPES = ['context_line', 'minor_context', 'cad_context'];

function categorize(zt: string): 'BOUNDARIES' | 'ZONES' | 'BUILDINGS' | 'INFRASTRUCTURE' {
  if (BOUNDARY_TYPES.includes(zt)) return 'BOUNDARIES';
  if (BUILDING_TYPES.includes(zt)) return 'BUILDINGS';
  if (INFRA_TYPES.includes(zt)) return 'INFRASTRUCTURE';
  return 'ZONES';
}

/* ────── Auto-fit camera — standard 3D perspective ────── */
function AutoFitCamera({ vectors }: { vectors: any[] }) {
  const { camera, controls } = useThree();
  const fitted = useRef(false);

  useEffect(() => {
    if (!vectors || vectors.length === 0 || fitted.current) return;
    const box = new THREE.Box3();
    for (const v of vectors) {
      if (!v.points) continue;
      for (const pt of v.points) {
        if (Array.isArray(pt) && pt.length >= 3) {
          box.expandByPoint(new THREE.Vector3(pt[0], pt[1], pt[2]));
        }
      }
    }
    if (box.isEmpty()) return;
    const center = new THREE.Vector3();
    const size = new THREE.Vector3();
    box.getCenter(center);
    box.getSize(size);
    const maxDim = Math.max(size.x, size.z, 10);
    // Standard 3D perspective: camera at ~55° from above, offset on Z
    // This gives a natural 3D view while clearly showing the plan layout
    const dist = maxDim * 1.2;
    const angle = Math.PI * 0.30; // ~55° from horizontal
    const camY = Math.sin(angle) * dist;
    const camZ = center.z + Math.cos(angle) * dist * 0.7;
    camera.position.set(center.x, camY, camZ);
    camera.up.set(0, 1, 0); // Standard Y-up
    (camera as THREE.PerspectiveCamera).lookAt(center.x, 0, center.z);
    (camera as THREE.PerspectiveCamera).updateProjectionMatrix();

    // Sync OrbitControls target
    if (controls) {
      const oc = controls as any;
      if (oc.target) oc.target.set(center.x, 0, center.z);
      if (oc.object) oc.object.up.set(0, 1, 0);
      if (oc.update) oc.update();
    }

    fitted.current = true;
  }, [vectors, camera, controls]);

  useEffect(() => { fitted.current = false; }, [vectors.length]);
  return null;
}

/* ────── Camera azimuth tracker — feeds the north compass ────── */
function CameraAzimuthTracker({ onAzimuthChange }: { onAzimuthChange: (deg: number) => void }) {
  const { camera, controls } = useThree();
  const prevDeg = useRef(0);

  useFrame(() => {
    const oc = controls as any;
    if (!oc?.target) return;
    // Compute the horizontal azimuth angle: atan2(dx, dz) from target to camera
    const dx = camera.position.x - oc.target.x;
    const dz = camera.position.z - oc.target.z;
    const azimuth = Math.atan2(dx, dz); // radians, 0 = looking from +Z
    const deg = (azimuth * 180) / Math.PI;
    // Only update if changed significantly (avoid re-render spam)
    if (Math.abs(deg - prevDeg.current) > 0.3) {
      prevDeg.current = deg;
      onAzimuthChange(deg);
    }
  });

  return null;
}

/* ────── Warm architectural grid ────── */
function ArchGrid() {
  const lines = useMemo(() => {
    const out: JSX.Element[] = [];
    const extent = 100;
    const step = 5;
    for (let i = -extent; i <= extent; i += step) {
      const major = i % 20 === 0;
      const c = major ? '#c9bfa8' : '#d8cebf';
      const w = major ? 1.0 : 0.4;
      const op = major ? 0.35 : 0.18;
      out.push(
        <Line key={`gx${i}`} points={[[i, -0.05, -extent], [i, -0.05, extent]]} color={c} lineWidth={w} transparent opacity={op} />,
        <Line key={`gz${i}`} points={[[-extent, -0.05, i], [extent, -0.05, i]]} color={c} lineWidth={w} transparent opacity={op} />
      );
    }
    return out;
  }, []);
  return <group>{lines}</group>;
}

/* ────── Origin cross (subtle +) at 0,0,0 ────── */
function OriginCross() {
  const size = 2.5;
  return (
    <group position={[0, 0.05, 0]}>
      <Line points={[[-size, 0, 0], [size, 0, 0]]} color="#94a3b8" lineWidth={1.2} transparent opacity={0.4} />
      <Line points={[[0, 0, -size], [0, 0, size]]} color="#94a3b8" lineWidth={1.2} transparent opacity={0.4} />
    </group>
  );
}

/* ────── Boundary outline (no fill) ────── */
function BoundaryOutline({ vec, color, opacity, selected, onSelect }: { vec: any; color: string; opacity: number; selected: boolean; onSelect: () => void }) {
  const [hovered, setHovered] = useState(false);
  if (!vec.points || vec.points.length < 2) return null;
  const style = LINE_STYLES[vec.zone_type] || { width: 1.5 };
  const lw = selected ? style.width + 1.5 : hovered ? style.width + 0.8 : style.width;
  const op = (selected ? 1.0 : hovered ? 0.95 : 0.8) * opacity;

  return (
    <group onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)} onClick={(e) => { e.stopPropagation(); onSelect(); }}>
      <Line points={vec.points} color={color} lineWidth={lw} transparent opacity={op}
        dashed={style.dashed} dashScale={style.dashScale} dashSize={1} gapSize={0.5} />
      {selected && <Line points={vec.points} color="#ffffff" lineWidth={lw + 2} transparent opacity={0.15} />}
      {(hovered || selected) && vec.centroid && (
        <Html position={[vec.centroid[0], (vec.centroid[1] || 0) + 1.2, vec.centroid[2]]} center style={{ pointerEvents: 'none' }}>
          <div className="zone-pill-label" style={{ borderColor: `${color}60` }}>
            <span className="zone-pill-dot" style={{ background: color }} />
            {vec.zone_label || ZONE_LABELS[vec.zone_type] || vec.zone_type}
          </div>
        </Html>
      )}
    </group>
  );
}

/* ────── Douglas-Peucker simplification for clean polygon fills ────── */
function dpSimplify(pts: [number, number][], eps: number): [number, number][] {
  if (pts.length <= 3) return pts;
  function perpDist(p: [number, number], a: [number, number], b: [number, number]): number {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const lenSq = dx * dx + dy * dy;
    if (lenSq === 0) return Math.hypot(p[0] - a[0], p[1] - a[1]);
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lenSq));
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }
  function rdp(points: [number, number][], epsilon: number): [number, number][] {
    if (points.length <= 2) return points;
    let maxD = 0, maxI = 0;
    for (let i = 1; i < points.length - 1; i++) {
      const d = perpDist(points[i], points[0], points[points.length - 1]);
      if (d > maxD) { maxD = d; maxI = i; }
    }
    if (maxD > epsilon) {
      const left = rdp(points.slice(0, maxI + 1), epsilon);
      const right = rdp(points.slice(maxI), epsilon);
      return [...left.slice(0, -1), ...right];
    }
    return [points[0], points[points.length - 1]];
  }
  const r = rdp(pts, eps);
  return r.length >= 3 ? r : pts;
}

/* ────── Canvas-based diagonal hatching texture for schematic overlay ────── */
const hatchTextureCache = new Map<string, THREE.CanvasTexture>();

function createHatchTexture(hexColor: string, lineSpacing = 6, lineWidth = 1): THREE.CanvasTexture {
  const cacheKey = `${hexColor}_${lineSpacing}_${lineWidth}`;
  if (hatchTextureCache.has(cacheKey)) return hatchTextureCache.get(cacheKey)!;

  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;

  // Transparent background
  ctx.clearRect(0, 0, size, size);

  // Draw diagonal lines
  ctx.strokeStyle = hexColor;
  ctx.lineWidth = lineWidth;
  ctx.lineCap = 'square';

  for (let i = -size; i < size * 2; i += lineSpacing) {
    ctx.beginPath();
    ctx.moveTo(i, 0);
    ctx.lineTo(i + size, size);
    ctx.stroke();
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(4, 4);
  texture.needsUpdate = true;

  hatchTextureCache.set(cacheKey, texture);
  return texture;
}

/* ────── Filled zone polygon — clean flat-colored patch with hatching overlay ────── */
function FilledZone({ vec, color, opacity, selected, onSelect }: { vec: any; color: string; opacity: number; selected: boolean; onSelect: () => void }) {
  const [hovered, setHovered] = useState(false);
  const fillMatRef = useRef<THREE.MeshBasicMaterial>(null);
  const hatchMatRef = useRef<THREE.MeshBasicMaterial>(null);
  const outlineRef = useRef<any>(null);

  // Smooth opacity interpolation
  const targetOpacity = useRef({ fill: 0.40, hatch: 0.08, outline: 0.7 });

  useEffect(() => {
    targetOpacity.current = {
      fill: selected ? 0.55 : hovered ? 0.48 : 0.38,
      hatch: selected ? 0.14 : hovered ? 0.12 : 0.07,
      outline: selected ? 1.0 : hovered ? 0.9 : 0.7,
    };
  }, [selected, hovered]);

  useFrame(() => {
    const lerp = 0.12;
    if (fillMatRef.current) {
      const target = targetOpacity.current.fill * opacity;
      fillMatRef.current.opacity += (target - fillMatRef.current.opacity) * lerp;
    }
    if (hatchMatRef.current) {
      const target = targetOpacity.current.hatch * opacity;
      hatchMatRef.current.opacity += (target - hatchMatRef.current.opacity) * lerp;
    }
  });

  const { geometry, outlinePts, hatchTexture } = useMemo(() => {
    if (!vec.closed || !vec.points || vec.points.length < 4) return { geometry: null, outlinePts: null, hatchTexture: null };
    // Collect 2D points (x, z) with deduplication
    let pts2d: [number, number][] = [];
    const seen = new Set<string>();
    for (const p of vec.points) {
      const key = `${p[0].toFixed(3)}_${p[2].toFixed(3)}`;
      if (!seen.has(key)) {
        seen.add(key);
        pts2d.push([p[0], p[2]]);
      }
    }
    if (pts2d.length < 3) return { geometry: null, outlinePts: null, hatchTexture: null };

    // Douglas-Peucker simplification to remove near-collinear vertices
    const xs = pts2d.map(p => p[0]), zs = pts2d.map(p => p[1]);
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs), 1);
    pts2d = dpSimplify(pts2d, span * 0.015);
    if (pts2d.length < 3) return { geometry: null, outlinePts: null, hatchTexture: null };

    // Ensure CCW winding for correct front-face rendering
    let shoelace = 0;
    for (let i = 0; i < pts2d.length; i++) {
      const j = (i + 1) % pts2d.length;
      shoelace += pts2d[i][0] * pts2d[j][1] - pts2d[j][0] * pts2d[i][1];
    }
    if (shoelace < 0) pts2d.reverse();

    const yPos = vec.points[0]?.[1] ?? 0;

    try {
      // Use THREE.Shape + ShapeGeometry for a single clean indexed mesh
      // This avoids visible internal triangle edges completely
      const shape = new THREE.Shape();
      shape.moveTo(pts2d[0][0], pts2d[0][1]);
      for (let i = 1; i < pts2d.length; i++) {
        shape.lineTo(pts2d[i][0], pts2d[i][1]);
      }
      shape.closePath();

      // ShapeGeometry produces a flat XY mesh — we rotate it to XZ
      const shapeGeo = new THREE.ShapeGeometry(shape);

      // Transform from XY plane to XZ plane (swap Y→Z, insert constant Y)
      const pos = shapeGeo.getAttribute('position');
      for (let i = 0; i < pos.count; i++) {
        const sx = pos.getX(i);
        const sy = pos.getY(i);
        pos.setXYZ(i, sx, yPos, sy);
      }
      pos.needsUpdate = true;
      shapeGeo.computeVertexNormals();

      // Build simplified 3D outline points for the boundary line
      const outline: [number, number, number][] = pts2d.map(p => [p[0], yPos, p[1]]);
      // Close the loop
      if (outline.length > 0) outline.push(outline[0]);

      // Create hatching pattern texture
      const hatch = createHatchTexture(color, 8, 1);

      return { geometry: shapeGeo, outlinePts: outline, hatchTexture: hatch };
    } catch { return { geometry: null, outlinePts: null, hatchTexture: null }; }
  }, [vec, color]);

  if (!geometry) {
    return <BoundaryOutline vec={vec} color={color} opacity={opacity} selected={selected} onSelect={onSelect} />;
  }

  const outlineW = selected ? 2.8 : hovered ? 2.2 : 1.5;
  const label = vec.zone_label || ZONE_LABELS[vec.zone_type] || vec.zone_type;

  return (
    <group onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)}
      onClick={(e) => { e.stopPropagation(); onSelect(); }}>
      {/* Solid base fill */}
      <mesh geometry={geometry}>
        <meshBasicMaterial ref={fillMatRef} color={color} transparent opacity={0.38 * opacity} side={THREE.DoubleSide} depthWrite={false}
          polygonOffset polygonOffsetFactor={1} polygonOffsetUnits={1} />
      </mesh>
      {/* Hatching pattern overlay — subtle architectural texture */}
      {hatchTexture && (
        <mesh geometry={geometry} position={[0, 0.01, 0]}>
          <meshBasicMaterial ref={hatchMatRef} map={hatchTexture} transparent opacity={0.07 * opacity} side={THREE.DoubleSide} depthWrite={false}
            polygonOffset polygonOffsetFactor={0.5} polygonOffsetUnits={0.5} />
        </mesh>
      )}
      {outlinePts && <Line points={outlinePts} color={color} lineWidth={outlineW} transparent opacity={(selected ? 1 : hovered ? 0.9 : 0.7) * opacity} />}
      {selected && outlinePts && <Line points={outlinePts} color="#ffffff" lineWidth={outlineW + 2} transparent opacity={0.15} />}
      {(hovered || selected) && vec.centroid && (
        <Html position={[vec.centroid[0], (vec.centroid[1] || 0) + 1.2, vec.centroid[2]]} center style={{ pointerEvents: 'none' }}>
          <div className="zone-pill-label" style={{ borderColor: `${color}60` }}>
            <span className="zone-pill-dot" style={{ background: color }} />
            {label}
            {vec.confidence < 0.8 && <span style={{ opacity: 0.5, marginLeft: 4, fontSize: '9px' }}>{Math.round(vec.confidence * 100)}%</span>}
          </div>
        </Html>
      )}
    </group>
  );
}

/* ────── Infrastructure dashed line ────── */
function InfraLine({ vec, color, opacity }: { vec: any; color: string; opacity: number }) {
  if (!vec.points || vec.points.length < 2) return null;
  return (
    <Line points={vec.points} color={color} lineWidth={1.2} transparent opacity={0.5 * opacity}
      dashed dashScale={8} dashSize={1} gapSize={0.6} />
  );
}

/* ────── Main scene manager ────── */
export const ThreeSceneManager: React.FC<ThreeSceneProps> = ({ selectedNode, activeNode, onSelectNode, onSelectVector, projectData }) => {
  const [legendOpen, setLegendOpen] = useState(true);
  const [selectedVectorId, setSelectedVectorId] = useState<string | null>(null);
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [compassAzimuth, setCompassAzimuth] = useState(0);

  const [visibleLayers, setVisibleLayers] = useState(() => {
    const saved = localStorage.getItem('omrt_visibleLayers');
    if (saved) { try { return JSON.parse(saved); } catch {} }
    return { BOUNDARIES: true, ZONES: true, BUILDINGS: true, INFRASTRUCTURE: true };
  });

  useEffect(() => { localStorage.setItem('omrt_visibleLayers', JSON.stringify(visibleLayers)); }, [visibleLayers]);

  const toggleLayer = useCallback((layer: string) => {
    setVisibleLayers((prev: any) => ({ ...prev, [layer]: !prev[layer] }));
  }, []);

  const toggleSection = useCallback((section: string) => {
    setCollapsedSections(prev => ({ ...prev, [section]: !prev[section] }));
  }, []);

  const rawVectors = projectData?.geometry?.raw_vector_objects;
  const vectors = Array.isArray(rawVectors) ? rawVectors : [];

  const handleSelectVector = useCallback((vec: any) => {
    const newId = selectedVectorId === vec.id ? null : vec.id;
    setSelectedVectorId(newId);
    onSelectVector?.(newId ? vec : null);
  }, [selectedVectorId, onSelectVector]);

  // Categorized + sublayer counts — matches hierarchy: Boundaries > Zones > Buildings
  const { categorized, sublayers } = useMemo(() => {
    const boundaries: any[] = [];
    const zones: any[] = [];
    const buildings: any[] = [];
    const infra: any[] = [];
    const sub: Record<string, Record<string, any[]>> = { BOUNDARIES: {}, ZONES: {}, BUILDINGS: {}, INFRASTRUCTURE: {} };

    for (const v of vectors) {
      const zt = v.zone_type || 'unknown';
      const cat = categorize(zt);
      if (cat === 'BOUNDARIES') boundaries.push(v);
      else if (cat === 'BUILDINGS') buildings.push(v);
      else if (cat === 'INFRASTRUCTURE') infra.push(v);
      else zones.push(v);

      if (!sub[cat][zt]) sub[cat][zt] = [];
      sub[cat][zt].push(v);
    }
    return { categorized: { boundaries, zones, buildings, infra }, sublayers: sub };
  }, [vectors]);

  const getOpacity = (defaultOpacity: number = 1.0) => {
    if (selectedNode === null) return defaultOpacity;
    if (selectedNode >= 6) return defaultOpacity;
    return defaultOpacity * 0.05;
  };

  const renderSublayer = (cat: string, zt: string, items: any[]) => {
    const color = ZONE_COLORS[zt] || '#94a3b8';
    return (
      <div key={zt} className="legend-item compact">
        <div className="legend-dot small" style={{ background: color }} />
        <span>{ZONE_LABELS[zt] || zt}</span>
        <span className="legend-count">{items.length}</span>
      </div>
    );
  };

  const SECTION_META: Record<string, { label: string; dot: string; icon: string }> = {
    BOUNDARIES: { label: 'Boundaries', dot: '#3b82f6', icon: '◻' },
    ZONES: { label: 'Zones', dot: '#f97316', icon: '◼' },
    BUILDINGS: { label: 'Buildings', dot: '#8b5cf6', icon: '⊞' },
    INFRASTRUCTURE: { label: 'Infrastructure', dot: '#94a3b8', icon: '┅' },
  };

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>

      {/* ──── North compass badge — TOP LEFT ──── */}
      <div className="north-compass">
        <div className="north-compass-arrow" style={{ transform: `rotate(${-compassAzimuth}deg)` }}>
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M12 2 L14.5 9 L12 7.5 L9.5 9 Z" fill="#e2e8f0" opacity="0.9" />
            <path d="M12 22 L14.5 15 L12 16.5 L9.5 15 Z" fill="#64748b" opacity="0.4" />
          </svg>
        </div>
        <span className="north-compass-label">N</span>
      </div>

      {/* ──── Layer panel — RIGHT side ──── */}
      <div className={`viewport-legend ${legendOpen ? 'open' : 'collapsed'}`}>
        <button className="legend-toggle" onClick={() => setLegendOpen(v => !v)}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" />
          </svg>
          <span>Layers</span>
          <svg className={`legend-chevron ${legendOpen ? 'open' : ''}`} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
        {legendOpen && (
          <div className="legend-body">
            {(['BOUNDARIES', 'ZONES', 'BUILDINGS', 'INFRASTRUCTURE'] as const).map(cat => {
              const meta = SECTION_META[cat];
              const subs = sublayers[cat];
              const subKeys = Object.keys(subs);
              if (subKeys.length === 0 && vectors.length > 0) return null;
              const isCollapsed = collapsedSections[cat];
              const totalCount = subKeys.reduce((s, k) => s + subs[k].length, 0);

              return (
                <div key={cat} className="legend-section">
                  <div className="legend-item clickable"
                    style={{ opacity: visibleLayers[cat] ? 1 : 0.35 }}
                    onClick={() => toggleLayer(cat)}>
                    <div className="legend-dot" style={{ background: meta.dot }} />
                    <span>{meta.label}</span>
                    {totalCount > 0 && <span className="legend-count">{totalCount}</span>}
                    {subKeys.length > 0 && (
                      <svg onClick={(e) => { e.stopPropagation(); toggleSection(cat); }}
                        className={`legend-chevron-mini ${isCollapsed ? '' : 'open'}`}
                        width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    )}
                  </div>
                  {!isCollapsed && subKeys.length > 0 && (
                    <div className="legend-sublayers">
                      {subKeys.map(zt => renderSublayer(cat, zt, subs[zt]))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <Canvas
        camera={{ position: [0, 80, 30], fov: 45, up: [0, 1, 0] }}
        gl={{ logarithmicDepthBuffer: true, antialias: true }}
        onPointerMissed={() => { onSelectNode?.(null); setSelectedVectorId(null); onSelectVector?.(null); }}
      >
        <color attach="background" args={['#f5f0e8']} />
        <ambientLight intensity={0.8} />
        <directionalLight position={[20, 30, 20]} intensity={0.6} />
        <directionalLight position={[-20, 10, -20]} intensity={0.3} />

        <Suspense fallback={null}>
          <ArchGrid />

          {/* Click plane */}
          <mesh position={[0, -1, 0]} rotation={[-Math.PI / 2, 0, 0]}
            onClick={(e) => { e.stopPropagation(); onSelectNode?.(null); setSelectedVectorId(null); onSelectVector?.(null); }}>
            <planeGeometry args={[2000, 2000]} />
            <meshBasicMaterial visible={false} />
          </mesh>

          <OriginCross />
          <AutoFitCamera vectors={vectors} />

          {activeNode >= 6 && (
            <group>
              {/* Boundaries — outline only */}
              {visibleLayers.BOUNDARIES && categorized.boundaries.map((vec: any, idx: number) => {
                const color = vec.color_hint || ZONE_COLORS[vec.zone_type] || '#3b82f6';
                return (
                  <BoundaryOutline key={`b-${idx}`} vec={vec} color={color} opacity={getOpacity(0.9)}
                    selected={selectedVectorId === vec.id} onSelect={() => handleSelectVector(vec)} />
                );
              })}

              {/* Zones — filled */}
              {visibleLayers.ZONES && categorized.zones.map((vec: any, idx: number) => {
                const color = vec.color_hint || ZONE_COLORS[vec.zone_type] || '#f97316';
                if (!vec.closed && vec.filled && vec.area_pdf_units === 0) return null;
                return vec.closed ? (
                  <FilledZone key={`z-${idx}`} vec={vec} color={color} opacity={getOpacity(0.9)}
                    selected={selectedVectorId === vec.id} onSelect={() => handleSelectVector(vec)} />
                ) : (
                  <BoundaryOutline key={`zl-${idx}`} vec={vec} color={color} opacity={getOpacity(0.7)}
                    selected={selectedVectorId === vec.id} onSelect={() => handleSelectVector(vec)} />
                );
              })}

              {/* Buildings — outline only (no fill, no hatching) */}
              {visibleLayers.BUILDINGS && categorized.buildings.map((vec: any, idx: number) => {
                const color = vec.color_hint || ZONE_COLORS[vec.zone_type] || '#8b5cf6';
                return (
                  <BoundaryOutline key={`bld-${idx}`} vec={vec} color={color} opacity={getOpacity(0.85)}
                    selected={selectedVectorId === vec.id} onSelect={() => handleSelectVector(vec)} />
                );
              })}

              {/* Infrastructure — dashed, muted */}
              {visibleLayers.INFRASTRUCTURE && categorized.infra.map((vec: any, idx: number) => {
                const color = vec.color_hint || '#94a3b8';
                return <InfraLine key={`i-${idx}`} vec={vec} color={color} opacity={getOpacity(0.5)} />;
              })}
            </group>
          )}

          {vectors.length === 0 && activeNode >= 6 && (
            <Html position={[0, 5, 0]} center>
              <div style={{ color: '#64748b', background: 'rgba(255,255,255,0.85)', padding: '1rem 1.5rem', borderRadius: '10px', fontSize: '0.85rem', boxShadow: '0 4px 20px rgba(0,0,0,0.08)' }}>
                No vectors extracted yet.
              </div>
            </Html>
          )}
        </Suspense>

        <OrbitControls makeDefault minPolarAngle={0.1} maxPolarAngle={Math.PI / 2.1} enableDamping dampingFactor={0.08} />
        <CameraAzimuthTracker onAzimuthChange={setCompassAzimuth} />
      </Canvas>
    </div>
  );
};
