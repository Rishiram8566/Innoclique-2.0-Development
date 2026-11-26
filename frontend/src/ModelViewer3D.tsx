import { Component, Suspense, createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Canvas, useLoader, useThree } from '@react-three/fiber';
import { Bounds, ContactShadows, Html, OrbitControls, useBounds } from '@react-three/drei';
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

const API = 'http://127.0.0.1:8000/api';
const PAGE_NUMBER = 1;
const MM_PER_M = 1000;

export type LOD = '100' | '200' | '300' | '400' | '500';
const LODS: LOD[] = ['100', '200', '300', '400', '500'];

type MeshElement = {
  element_id: string;
  component?: string;
  element_type?: string;
  detected_by?: string;
  wall_type?: string;
  joined_mesh?: { vertices: number[][]; faces: number[][] };
  joins?: { end: string; to: string; join: string; extended_mm: number }[];
  review_flags?: string[];
  color?: string | null;
  name?: string | null;
  mesh_parts?: { color: string | null; opacity: number; faces: number[][] }[] | null;
  element_type_label?: string;
  host_component?: string | null;
  is_external?: boolean | null;
  roof?: { form: string; pitch_deg: number; ridge_axis: 'x' | 'y'; overhang_mm: number; sheet: string; colour?: string | null };
  stair?: { risers?: number; rise_mm?: number; going_mm?: number; width_mm?: number; landing_level_mm?: number } | null;
  lift?: { capacity_kg?: number | null; stops?: string[]; door_ids?: string[] } | null;
  lift_door?: boolean;
  furniture?: { room: string; room_type: string } | null;
  source_note?: string | null;
  between?: string[];
  opening?: {
    axis: 'h' | 'v'; width_mm: number; thickness_mm: number; sill_mm: number; head_mm: number;
    wall_height_mm: number; tag: string | null; height_source: string; symbol_evidence: string[];
  };
  dimensions_mm: { length: number | null; thickness: number | null; height: number | null; volume?: number };
  mesh: { vertices: number[][]; faces: number[][] };
  lod?: { minimum_lod: number; maximum_lod?: number };
  envelope?: boolean;
  height_verification?: {
    verification_id: string;
    reviewer?: string;
    verified_at?: string;
    printed_measurements?: string[];
  };
  provenance?: {
    source_lines?: Record<string, { path_index: number; item_index: number }>;
    millimetres_per_pdf_point?: number;
  };
};

type MeshResponse = {
  stage: string;
  drawing_title?: string;
  page?: { size_mm: number[] | null };
  calibration?: { millimetres_per_pdf_point: number; verification_required?: boolean };
  elements: MeshElement[];
  withheld_elements: { element_id: string | null; reasons: string[] }[];
  serving_gate: { served: number; withheld: number; elements_in_artifact: number };
  limitations?: string[];
};

const LOD_NOTES: Record<LOD, string> = {
  '100': 'LOD 100 — structure only: columns on the grid, full storey height, and the stairs. Beams are not on the architectural drawings, so none are shown.',
  '200': 'LOD 200 — structure with floors: columns, stairs and floor slabs at each storey level, with the opening over each stair (Whole building view).',
  '300': 'LOD 300 — architectural model: walls as drawn, with door and window openings, door leaves and glazing (heights from the drawing’s schedule, or typical values marked assumed).',
  '400': 'LOD 400 — detailed: walls joined at corners, tees and columns; door and window frames; construction edges.',
  '500': 'LOD 500 — complete building: walls in their finishes (brick outside, plaster inside, concrete or block where the drawing says so), the roof with the parapet the drawing specifies, doors and windows with frames, furniture in each named room, and room names (pick a floor, or hide the roof, to look inside). Hover or click any element for its details.',
};

const COMPONENT_COLORS: Record<string, string> = {
  parapet: '#e2c799',
  'stair flight': '#b9b2a3',
  'stair landing': '#b9b2a3',
  'stair handrail': '#3d4148',
  'lift shaft': '#8fb3cf',
  'lift car': '#b7bcc2',
  'full-height wall': '#c3cdd8',
  column: '#7d8793',
  wall: '#cfcac0',
  furniture: '#b98c5c',
};
const STOREY_COLORS = ['#5b8def', '#e8a33d', '#4fb286', '#c86dd7', '#e06666', '#3fb8c9'];

/** Colour by component; legend-hatch walls are matched by material keywords. */
function componentColor(component: string): string {
  if (COMPONENT_COLORS[component]) return COMPONENT_COLORS[component];
  const c = component.toLowerCase();
  if (c.includes('thermal')) return '#d9a066';
  if (c.includes('fire')) return '#c8584f';
  if (c.includes('hollow')) return '#93b45c';
  if (c === 'door') return '#8a5a33';
  if (c === 'window') return '#8fc9ef';
  if (c === 'opening') return '#cbd5e1';
  if (c.includes('concrete')) return '#a9adb3';
  if (c.includes('partition')) return '#e3b59b';
  if (c.includes('brick')) return '#c27a58';
  if (c.includes('block')) return '#d8c9a8';
  return COMPONENT_COLORS.wall;
}
const CONCRETE = '#c9c3b6';
const COLUMN_GREY = '#8e98a3';
const REVIEW = '#f59e0b';
const SELECTED = '#f97316';

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => (d as { msg?: string })?.msg ?? String(d)).join('; ');
  return '';
}

function mm(value: number | null | undefined) {
  return value == null ? '—' : `${Math.round(value).toLocaleString()} mm`;
}

/** Page-local mm (x right, y down, z up) -> three.js metres (x, up, toward viewer). */
function toThree([x, y, z]: number[]): [number, number, number] {
  return [x / MM_PER_M, z / MM_PER_M, y / MM_PER_M];
}

/** Box-projected UVs in metres, so a texture keeps its real size on every face. */
function addWorldUVs(geometry: THREE.BufferGeometry) {
  const pos = geometry.getAttribute('position');
  const normal = geometry.getAttribute('normal');
  const uv = new Float32Array(pos.count * 2);
  for (let i = 0; i < pos.count; i += 1) {
    const ax = Math.abs(normal.getX(i)), ay = Math.abs(normal.getY(i)), az = Math.abs(normal.getZ(i));
    const [u, v] = ax >= ay && ax >= az ? [pos.getZ(i), pos.getY(i)] : ay >= az ? [pos.getX(i), pos.getZ(i)] : [pos.getX(i), pos.getY(i)];
    uv[i * 2] = u; uv[i * 2 + 1] = v;
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
}

function useWallGeometry(element: MeshElement, joined: boolean) {
  const mesh = joined && element.joined_mesh ? element.joined_mesh : element.mesh;
  return useMemo(() => {
    const positions = new Float32Array(mesh.vertices.flatMap(v => toThree(v)));
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    indexed.setIndex(mesh.faces.flat());
    const flat = indexed.toNonIndexed();
    flat.computeVertexNormals();
    addWorldUVs(flat);
    indexed.dispose();
    return flat;
  }, [mesh]);
}

/**
 * Brick in running bond, drawn once: 4 bricks x 12 courses per 0.9 m tile (215 x 65 mm bricks, 10 mm joints),
 * tinted from the wall's own colour with a little variation per brick.
 */
const brickTextures = new Map<string, THREE.CanvasTexture>();
function brickTexture(base: string): THREE.CanvasTexture {
  const cached = brickTextures.get(base);
  if (cached) return cached;
  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#cfc8bd';                              // mortar
  ctx.fillRect(0, 0, size, size);
  const courses = 12, bricks = 4;
  const ch = size / courses, bw = size / bricks, joint = size * (10 / 900);
  const colour = new THREE.Color(base);
  let seed = 7;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let row = 0; row < courses; row += 1) {
    const offset = row % 2 ? bw / 2 : 0;
    for (let b = -1; b <= bricks; b += 1) {
      const shade = colour.clone().offsetHSL(0, (random() - 0.5) * 0.06, (random() - 0.5) * 0.08);
      ctx.fillStyle = `#${shade.getHexString()}`;
      ctx.fillRect(b * bw + offset + joint / 2, row * ch + joint / 2, bw - joint, ch - joint);
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(1 / 0.9, 1 / 0.9);                    // UVs are metres; one tile is 0.9 m
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  brickTextures.set(base, texture);
  return texture;
}

const isBrick = (component: string, material?: string | null) =>
  /brick/i.test(component) || /brick/i.test(material ?? '');

type Look = 'realistic' | 'type' | 'storey';

/** Whether a wall faces outside: the model's IsExternal flag, else its type name, else a brick material. */
function isExteriorWall(element: MeshElement): boolean {
  const e = element as any;
  if (e.is_external === true) return true;
  if (e.is_external === false) return false;
  const text = `${element.name ?? ''} ${element.component ?? ''} ${e.material ?? ''}`;
  if (/interior|internal|partition/i.test(text)) return false;
  return /exterior|external|brick/i.test(text);
}

const REAL = {
  brick: '#9a4a3c', plaster: '#ece6da', render: '#e4dccd', timber: '#8a5a33', white: '#f1f0ec', metal: '#3d4148',
  roof: '#8f2f2b', concrete: '#a9adb3', floor: '#cfc8bb', ceiling: '#f4f1ea', other: '#b8b2a7',
  stone: '#7f7b74', band: '#e7e3da', stair: '#d6cfc2',
};

/**
 * The building's facade at LOD 500, from the model: 'glass' for an office building (its outside walls read as a
 * curtain wall), else the drawing's wall finish (brick when the drawings are silent). Display only.
 */
const FacadeContext = createContext<string | null>(null);
const CURTAIN = { mullion: '#3a424e', panel: '#c9d0d8', spandrel: '#6788ae', membrane: '#c3c8ce' };
let curtainTexture: THREE.CanvasTexture | null = null;
/** Curtain wall: tinted glass in 1.5 m modules with dark aluminium mullions and transoms, drawn once. */
function curtainWallTexture(): THREE.CanvasTexture {
  if (curtainTexture) return curtainTexture;
  const size = 256;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const sky = ctx.createLinearGradient(0, 0, size, size);
  sky.addColorStop(0, '#b2cbe4');
  sky.addColorStop(0.55, '#7fa2c7');
  sky.addColorStop(1, '#5d80a8');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = 'rgba(255,255,255,0.12)';                        // a soft reflection across each pane
  ctx.beginPath();
  ctx.moveTo(size * 0.18, 0); ctx.lineTo(size * 0.48, 0); ctx.lineTo(size * 0.22, size); ctx.lineTo(0, size);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = CURTAIN.mullion;
  ctx.fillRect(0, 0, 9, size);                                     // mullion
  ctx.fillRect(0, 0, size, 7);                                     // transom
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(1 / 1.5, 1 / 1.5);                            // UVs are metres: one 1.5 m module
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  curtainTexture = texture;
  return texture;
}

/** Standing-seam roof sheet: a raised seam every 450 mm down the slope, drawn once per colour and direction. */
const seamTextures = new Map<string, THREE.CanvasTexture>();
function seamTexture(base: string, ridgeAxis: 'x' | 'y'): THREE.CanvasTexture {
  const key = `${base}:${ridgeAxis}`;
  const cached = seamTextures.get(key);
  if (cached) return cached;
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const colour = new THREE.Color(base);
  ctx.fillStyle = `#${colour.getHexString()}`;
  ctx.fillRect(0, 0, size, size);
  ctx.fillStyle = `#${colour.clone().offsetHSL(0, -0.05, 0.12).getHexString()}`;   // lit side of the seam
  ctx.fillRect(size / 2 - 5, 0, 4, size);
  ctx.fillStyle = `#${colour.clone().offsetHSL(0, 0, -0.14).getHexString()}`;      // shadow side
  ctx.fillRect(size / 2 - 1, 0, 4, size);
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(1 / 0.45, 1 / 0.45);                 // UVs are metres; one seam every 450 mm
  if (ridgeAxis === 'y') { texture.center.set(0.5, 0.5); texture.rotation = Math.PI / 2; }
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  seamTextures.set(key, texture);
  return texture;
}

/**
 * LOD 500 'Realistic' finish of a wall line: the drawing's material where it names one
 * (concrete; block is rendered outside), plaster inside, brick outside otherwise. null = brick texture.
 */
function wallFinish(material: string, external: boolean): string | null {
  if (/concrete|\brcc\b/i.test(material)) return REAL.concrete;
  if (!external) return REAL.plaster;
  if (/block/i.test(material)) return REAL.render;
  return null;
}

/** Roof finish: square clay tiles (300 mm), drawn once per colour. */
const tileTextures = new Map<string, THREE.CanvasTexture>();
function roofTileTexture(base: string): THREE.CanvasTexture {
  const cached = tileTextures.get(base);
  if (cached) return cached;
  const size = 256, tiles = 4;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#5a2a24';
  ctx.fillRect(0, 0, size, size);
  const step = size / tiles, joint = 3;
  const colour = new THREE.Color(base);
  let seed = 11;
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  for (let i = 0; i < tiles; i += 1) {
    for (let j = 0; j < tiles; j += 1) {
      const shade = colour.clone().offsetHSL(0, (random() - 0.5) * 0.05, (random() - 0.5) * 0.07);
      ctx.fillStyle = `#${shade.getHexString()}`;
      ctx.fillRect(j * step + joint / 2, i * step + joint / 2, step - joint, step - joint);
    }
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(1 / 1.2, 1 / 1.2);                    // UVs are metres; one tile of 4 x 4 is 1.2 m
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  tileTextures.set(base, texture);
  return texture;
}

/** Presentation colour for LOD 500 'Realistic'; null means 'use the brick texture'. */
function realisticColor(element: MeshElement): string | null {
  const type = element.element_type ?? 'wall';
  const component = element.component ?? '';
  const material = `${component} ${element.host_component ?? ''} ${(element as any).material ?? ''}`;
  // A BIM (IFC) model states the material of its trims, mullions, columns and slabs: keep that colour
  // (e.g. dark aluminium fins) instead of the generic finish used for drawings.
  if (element.detected_by === 'ifc' && element.color
      && ['window muntin', 'trim', 'fascia', 'column', 'beam', 'slab', 'member'].includes(type)) return element.color;
  if (type === 'wall' || type === 'parapet') return wallFinish(material, isExteriorWall(element));
  if (type === 'column' && element.host_component) return wallFinish(element.host_component, isExteriorWall(element));
  if (type === 'chimney') return null;
  if (type === 'door') return REAL.timber;
  if (type === 'window' || type === 'window muntin' || type === 'trim' || type === 'fascia') return REAL.white;
  if (type === 'gutter' || type === 'downpipe' || type === 'railing' || type === 'ridge') return REAL.metal;
  if (type === 'plinth') return REAL.stone;
  if (type === 'stair') return REAL.stair;
  if (type === 'lift car') return LIFT_STEEL;
  if (type === 'lift shaft') return LIFT_GLASS;
  if (type === 'roof' || type === 'roof seam') return element.color ?? REAL.roof;
  if (type === 'column' || type === 'beam') return REAL.concrete;
  if (type === 'slab') return REAL.floor;
  if (type === 'ceiling') return REAL.ceiling;
  if (isBrick(component, (element as any).material)) return null;
  return element.color ?? REAL.other;
}

const Wall = memo(function Wall({
  element,
  lod,
  selected,
  onSelect,
  hovered = false,
  onHover,
  colorOverride,
  onFocus,
  showLabel = true,
  look = 'type',
  inward,
}: {
  element: MeshElement;
  lod: LOD;
  selected: boolean;
  onSelect: (id: string) => void;
  hovered?: boolean;
  onHover?: (id: string | null) => void;
  colorOverride?: string;
  onFocus?: (point: THREE.Vector3) => void;
  showLabel?: boolean;
  look?: Look;
  /** Centre of the storey (three.js units): faces of an outside wall that look towards it are its inner side. */
  inward?: [number, number, number];
}) {
  const geometry = useWallGeometry(element, lod === '400' || lod === '500');
  // An outside wall is brick on its outer face and plaster on its inner face: split its triangles into two groups.
  const twoFaced = useMemo(() => {
    if (!inward) return null;
    const g = geometry.clone();
    const pos = g.getAttribute('position'), nor = g.getAttribute('normal');
    g.computeBoundingBox();
    const mid = g.boundingBox!.getCenter(new THREE.Vector3());
    const size = g.boundingBox!.getSize(new THREE.Vector3());
    // Judge only across the wall's thickness: which of its two long faces a triangle lies on (from the wall's
    // middle, not its winding), and whether that face is on the side of the storey's centre.
    const acrossX = size.x <= size.z;
    const thickness = acrossX ? size.x : size.z;
    const toward = acrossX ? inward[0] - mid.x : inward[2] - mid.z;
    const kinds: number[] = [];
    for (let t = 0; t < pos.count / 3; t += 1) {
      const i = t * 3;
      const d = acrossX
        ? (pos.getX(i) + pos.getX(i + 1) + pos.getX(i + 2)) / 3 - mid.x
        : (pos.getZ(i) + pos.getZ(i + 1) + pos.getZ(i + 2)) / 3 - mid.z;
      kinds.push(Math.abs(nor.getY(i)) < 0.5 && Math.abs(d) > thickness * 0.25 && d * toward > 0 ? 1 : 0);
    }
    let start = 0;
    for (let t = 1; t <= kinds.length; t += 1) {
      if (t === kinds.length || kinds[t] !== kinds[start]) {
        g.addGroup(start * 3, (t - start) * 3, kinds[start]);
        start = t;
      }
    }
    return g;
  }, [geometry, inward]);
  // Models with materials (IFC) keep each surface's own colour and opacity, e.g. a window's frame and glass.
  const partGeometries = useMemo(() => (element.mesh_parts ?? []).map(part => {
    const positions = new Float32Array(element.mesh.vertices.flatMap(v => toThree(v)));
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    indexed.setIndex(part.faces.flat());
    const flat = indexed.toNonIndexed();
    flat.computeVertexNormals();
    indexed.dispose();
    return { geometry: flat, color: part.color, opacity: part.opacity };
  }), [element]);
  const edges = useMemo(() => new THREE.EdgesGeometry(geometry), [geometry]);
  const component = element.component ?? 'wall';
  const flagged = !!element.review_flags?.length;
  const type = element.element_type;
  // Doors read as timber and windows as glass at every LOD; at LOD 500 models with materials (IFC) show them.
  const steel = /tower|antenna|platform|ladder|gate|steel|gantry|transformer|pipe|hoist|stack/.test(type ?? '');
  const byType = type === 'door' ? '#8a5a33' : type === 'window' ? '#8fc9ef'
    : type === 'column' || type === 'beam' || steel ? COLUMN_GREY
    : type === 'slab' || type === 'roof' ? '#d9dee4' : type === 'water' ? '#5b8fb9' : CONCRETE;
  const realistic = lod === '500' && look === 'realistic' && !colorOverride;
  const realColor = realistic ? realisticColor(element) : undefined;
  // An office building's outside walls read as glass curtain wall; its parapets as aluminium panels and the
  // columns in its facade as mullions.
  const facade = useContext(FacadeContext);
  const curtain = realistic && facade === 'glass' && element.detected_by !== 'ifc' && isExteriorWall(element)
    && (type === 'wall' || type === 'parapet' || type === 'column');
  const curtainWall = curtain && type === 'wall';
  const curtainTrim = curtain && !curtainWall ? (type === 'parapet' ? CURTAIN.panel : CURTAIN.mullion) : null;
  // At LOD 500 a selected element keeps its finish and glows orange; below 500 it turns solid orange.
  const glow = selected && lod === '500';
  const glowProps = { emissive: glow ? SELECTED : '#000000', emissiveIntensity: glow ? 0.45 : 0 };
  const color = selected && !glow
    ? SELECTED
    : lod === '500'
      ? (flagged ? REVIEW : colorOverride ?? (realistic ? curtainTrim ?? realColor ?? REAL.brick : element.color ?? componentColor(component)))
      : byType;
  const seamLook = lod === '500' && !flagged && !colorOverride && type === 'roof' && !!element.roof
    && /standing seam/i.test(element.roof.sheet ?? '');
  const brickLook = lod === '500' && !flagged && !colorOverride && !curtain
    && (realistic ? realColor === null : isBrick(component, (element as any).material));
  const glass = type === 'window' || component.includes('glass') || component.includes('curtain');
  const shaft = type === 'lift shaft';
  const seeThrough = !!element.envelope;
  // Thin members (tower steel, gantries, pipes) and curved shells read better without construction edges.
  const slender = element.detected_by === 'structure_parameters' && !/dam block|building|terrain/.test(type ?? '');

  const { top, coping } = useMemo(() => {
    const box = new THREE.Box3().setFromBufferAttribute(
      geometry.getAttribute('position') as THREE.BufferAttribute,
    );
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    // Presentation only: a light cap along the top of a parapet (no dimension is claimed for it).
    const cap = { position: [center.x, box.max.y + 0.03, center.z] as [number, number, number],
                  size: [size.x + 0.06, 0.06, size.z + 0.06] as [number, number, number] };
    return { top: [center.x, box.max.y + 0.6, center.z] as [number, number, number], coping: cap };
  }, [geometry]);

  const handlers = {
    onClick: (event: any) => {
      event.stopPropagation();
      if ((event.delta ?? 0) <= CLICK_SLOP_PX) onSelect(element.element_id);
    },
    onDoubleClick: (event: any) => { event.stopPropagation(); onFocus?.(event.point); },
    onPointerOver: (event: any) => {
      event.stopPropagation();
      document.body.style.cursor = 'pointer';
      onHover?.(element.element_id);
    },
    onPointerOut: () => { document.body.style.cursor = ''; onHover?.(null); },
  };
  const usesParts = partGeometries.length > 0 && lod !== '100' && lod !== '200';

  return (
    <group>
      {usesParts ? partGeometries.map((part, i) => {
        const clear = part.opacity < 1;
        const partColor = selected && !glow ? SELECTED : clear ? (lod === '500' ? part.color ?? '#8fc9ef' : '#8fc9ef')
          : lod === '500' ? (realistic && type === 'door' ? (part.color ?? REAL.timber) : part.color ?? color) : byType;
        return (
          <mesh key={i} geometry={part.geometry} castShadow={!clear} receiveShadow {...handlers}>
            <meshStandardMaterial color={partColor} roughness={clear ? 0.1 : 0.8} metalness={clear ? 0.1 : 0.02}
              side={THREE.DoubleSide} transparent={clear} opacity={clear ? Math.min(part.opacity, 0.45) : 1}
              depthWrite={!clear} {...glowProps} />
          </mesh>
        );
      }) : curtainWall ? (
        <mesh geometry={geometry} receiveShadow {...handlers}>
          <meshStandardMaterial map={curtainWallTexture()} roughness={0.12} metalness={0.45} side={THREE.DoubleSide}
            transparent opacity={0.86} depthWrite={false} {...glowProps} />
        </mesh>
      ) : brickLook && twoFaced && realistic ? (
        <mesh geometry={twoFaced} castShadow receiveShadow {...handlers}>
          <meshStandardMaterial attach="material-0" map={brickTexture(REAL.brick)} roughness={0.9} metalness={0}
            side={THREE.DoubleSide} {...glowProps} />
          <meshStandardMaterial attach="material-1" color={REAL.plaster} roughness={0.9} metalness={0}
            side={THREE.DoubleSide} {...glowProps} />
        </mesh>
      ) : (
        <mesh geometry={geometry} castShadow receiveShadow {...handlers}>
          {seamLook ? (
            <meshStandardMaterial map={seamTexture(element.color ?? REAL.roof, element.roof!.ridge_axis)}
              roughness={0.55} metalness={0.35} side={THREE.DoubleSide} {...glowProps} />
          ) : brickLook && twoFaced && realistic ? null : brickLook ? (
            <meshStandardMaterial map={brickTexture(realistic ? REAL.brick : element.color ?? componentColor(component))}
              roughness={0.9} metalness={0} side={THREE.DoubleSide} {...glowProps} />
          ) : seeThrough ? (
            // A LOD 100 envelope: the structure's overall volume, see-through.
            <meshStandardMaterial color={selected ? SELECTED : '#9fb3c8'} roughness={0.6} side={THREE.DoubleSide}
              transparent opacity={0.3} depthWrite={false} />
          ) : shaft ? (
            // The lift shaft reads as a light glass volume at every LOD, so the car and floors show through it.
            <meshStandardMaterial color={selected ? SELECTED : LIFT_GLASS} roughness={0.1} metalness={0.1}
              side={THREE.DoubleSide} transparent opacity={0.16} depthWrite={false} />
          ) : (
            <meshStandardMaterial color={type === 'lift car' && !selected ? LIFT_STEEL : color}
              roughness={glass ? 0.2 : type === 'lift car' ? 0.35 : 0.85} metalness={type === 'lift car' ? 0.6 : 0.02}
              side={THREE.DoubleSide} transparent={glass} opacity={glass ? 0.5 : 1} depthWrite={!glass} {...glowProps} />
          )}
        </mesh>
      )}
      {shaft && lod !== '400' && lod !== '500' && (
        <lineSegments geometry={edges}>
          <lineBasicMaterial color="#5f86a6" />
        </lineSegments>
      )}
      {lod === '500' && type === 'parapet' && (
        <mesh position={coping.position} castShadow receiveShadow {...handlers}>
          <boxGeometry args={coping.size} />
          <meshStandardMaterial color={selected ? SELECTED : REAL.white} roughness={0.6} />
        </mesh>
      )}
      {(lod === '400' || lod === '500') && type !== 'furniture' && !slender && (
        <lineSegments geometry={edges}>
          <lineBasicMaterial color={lod === '500' ? '#475569' : '#334155'} transparent opacity={0.85} />
        </lineSegments>
      )}
      {showLabel && lod === '500' && (hovered || selected) && (
        <Html position={top} center style={{ pointerEvents: 'none' }}>
          <div style={{
            background: 'rgba(15,23,42,.85)', color: '#f8fafc', fontSize: 10,
            padding: '2px 6px', borderRadius: 4, whiteSpace: 'nowrap',
          }}>
            {component} · {mm(element.dimensions_mm.height)}
          </div>
        </Html>
      )}
    </group>
  );
});

type ViewPreset = 'overview' | 'front' | 'back' | 'plan' | 'interior';
const WORLD_UP = new THREE.Vector3(0, 1, 0);
/** Pixels the pointer may move between press and release and still count as a click (not an orbit). */
const CLICK_SLOP_PX = 4;

/** Camera presets and double-click focus, driven through drei Bounds (animated). */
function CameraRig({ preset, nonce, focusRef, roomRef }: {
  preset: ViewPreset; nonce: number; focusRef: { current: ((p: THREE.Vector3) => void) | null };
  roomRef: { current: ((eye: THREE.Vector3, look: THREE.Vector3) => void) | null };
}) {
  const api = useBounds();
  const camera = useThree(state => state.camera);
  const controls = useThree(state => state.controls) as (THREE.EventDispatcher<any> & { update: () => void }) | null;
  useEffect(() => {
    focusRef.current = (point: THREE.Vector3) => { api.lookAt({ target: point, up: WORLD_UP }); };
    // Standing in a room: the orbit target sits 5 cm ahead of the eye, so dragging turns the view round 360 degrees.
    roomRef.current = (eye: THREE.Vector3, look: THREE.Vector3) => {
      const ahead = eye.clone().add(look.clone().sub(eye).normalize().multiplyScalar(0.05));
      api.moveTo(eye).lookAt({ target: ahead, up: WORLD_UP });
    };
    return () => { focusRef.current = null; roomRef.current = null; };
  }, [api, focusRef, roomRef]);
  // A camera move (preset, double-click, LOD change) tilts camera.up while it animates; if the user grabs the
  // model mid-move that tilt would stay and every orbit would roll the building. Level it on every grab.
  useEffect(() => {
    if (!controls) return;
    const level = () => {
      if (camera.up.distanceTo(WORLD_UP) > 1e-6) {
        camera.up.copy(WORLD_UP);
        controls.update();
      }
    };
    controls.addEventListener('start', level);
    return () => controls.removeEventListener('start', level);
  }, [camera, controls]);
  useEffect(() => {
    if (!nonce) return;
    api.refresh();
    const { center, distance } = api.getSize();
    const d = distance * 1.05;
    const position: [number, number, number] =
      preset === 'front' ? [center.x, center.y + d * 0.12, center.z + d]
        : preset === 'back' ? [center.x, center.y + d * 0.12, center.z - d]
          // Almost straight down, a touch to the south, so world up still gives north at the top of the screen.
          : preset === 'plan' ? [center.x, center.y + d * 1.3, center.z + d * 0.03]
          // Looking down into an opened floor: steep enough to see over the walls into every room.
          : preset === 'interior' ? [center.x - d * 0.35, center.y + d * 1.0, center.z + d * 0.5]
            : [center.x - d * 0.7, center.y + d * 0.55, center.z + d * 0.75];
    api.moveTo(position).lookAt({ target: center, up: WORLD_UP });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);
  return null;
}

const TYPE_LABEL: Record<string, string> = {
  wall: 'Wall', column: 'Column', beam: 'Beam', slab: 'Floor', roof: 'Roof', door: 'Door', window: 'Window',
  opening: 'Opening', ceiling: 'Ceiling', 'roof seam': 'Roof', 'window muntin': 'Window', trim: 'Trim',
  gutter: 'Gutter', downpipe: 'Downpipe', fascia: 'Fascia', chimney: 'Chimney', stair: 'Stair', railing: 'Railing',
  parapet: 'Parapet', ridge: 'Ridge', plinth: 'Plinth', 'lift shaft': 'Lift shaft', 'lift car': 'Lift car',
  furniture: 'Furniture',
};

/** Elements hidden by 'Roof: Hide' at LOD 500, so the rooms underneath can be seen. */
const ROOF_TYPES = new Set(['roof', 'roof seam', 'ridge', 'fascia', 'gutter', 'parapet', 'ceiling']);

/** The hovered element, in the style of a BIM viewer's info card. */
function HoverCard({ element }: { element: MeshElement | null }) {
  if (!element) return null;
  const type = element.element_type ?? 'element';
  const d = element.dimensions_mm;
  const m = (v: number | null | undefined) => (v == null ? '–' : (v / 1000).toFixed(2));
  const title = element.name ? element.name.split(':')[0] : (element.component ?? type);
  return (
    <div style={{
      position: 'absolute', left: 14, bottom: 14, pointerEvents: 'none', minWidth: 220,
      background: 'rgba(15,15,18,.88)', color: '#f1f5f9', borderRadius: 10, padding: '8px 12px',
      border: '1px solid rgba(255,255,255,.08)', fontSize: 12, boxShadow: '0 4px 18px rgba(0,0,0,.35)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--muted)', fontSize: 11 }}>
        <span style={{ width: 8, height: 8, borderRadius: 2, background: element.color ?? componentColor(element.component ?? type) }} />
        {TYPE_LABEL[type] ?? type}
      </div>
      <div style={{ fontWeight: 700, margin: '2px 0' }}>{title}</div>
      <div style={{ fontFamily: 'ui-monospace, monospace', color: 'var(--ink-2)' }}>
        {element.opening
          ? `${element.opening.tag ?? title} · ${m(element.opening.width_mm)} × ${m(element.opening.head_mm - element.opening.sill_mm)} m`
            + (element.opening.height_source === 'assumed_typical' ? ' (height assumed)' : '')
          : element.lift
            ? `${element.lift.capacity_kg ? `${element.lift.capacity_kg} kg · ` : ''}${element.lift.stops?.length ?? 0} stops · ${m(d.length)} × ${m(d.thickness)} m shaft`
          : element.stair?.risers
            ? `${element.stair.risers} risers × ${Math.round(element.stair.rise_mm ?? 0)} mm · going ${Math.round(element.stair.going_mm ?? 0)} mm · ${m(element.stair.width_mm)} m wide`
            : `${m(d.length)} × ${m(d.thickness)} × ${m(d.height)} m`}
      </div>
      {element.furniture && (
        <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 2 }}>
          {element.furniture.room} · indicative (typical size, not on the drawing)
        </div>
      )}
      {element.source_note && (
        <div style={{ color: 'var(--muted)', fontSize: 11, marginTop: 2 }}>From the drawing note “{element.source_note}”</div>
      )}
    </div>
  );
}

const DOOR_WOOD = '#8a5a33';
const DOOR_WOOD_PANEL = '#77492a';
const LIFT_STEEL = '#b7bcc2';
const LIFT_GLASS = '#8fb3cf';
const DOOR_PAINT = '#ece8df';
const DOOR_PAINT_PANEL = '#ddd7ca';
const HANDLE = '#9aa0a6';
const SILL_STONE = '#e6e1d6';
// LOD 500 presentation details (typical joinery sizes; the drawings give only the openings' sizes).
const TRIM_MM = 70;
const TRIM_PROUD_MM = 20;
const PANEL_PROUD_MM = 12;
const HANDLE_MM = 55;
const MUNTIN_MM = 30;
const SILL_OVERHANG_MM = 50;
const SILL_HEIGHT_MM = 40;
const DOOR_FRAME = '#4b3423';
const GLASS = '#8fc9ef';
const WINDOW_FRAME = '#e2e8f0';
const FRAME_MM = 50;

type Part = { from: number[]; to: number[]; color: string; opacity?: number; brick?: boolean };

/** One box between two corners given in model mm (x, y plan; z up). */
function PartBox({ part, onClick, onOver, onOut }: {
  part: Part; onClick: (e: any) => void; onOver: (e: any) => void; onOut: () => void;
}) {
  const [x0, y0, z0] = part.from;
  const [x1, y1, z1] = part.to;
  const size: [number, number, number] = [
    Math.max(Math.abs(x1 - x0), 1) / MM_PER_M, Math.max(Math.abs(z1 - z0), 1) / MM_PER_M, Math.max(Math.abs(y1 - y0), 1) / MM_PER_M,
  ];
  const position = toThree([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]);
  const geometry = useMemo(() => {
    const box = new THREE.BoxGeometry(...size);
    if (part.brick) addWorldUVs(box);
    return box;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size[0], size[1], size[2], part.brick]);
  return (
    <mesh geometry={geometry} position={position} castShadow receiveShadow onClick={onClick} onPointerOver={onOver} onPointerOut={onOut}>
      {part.brick ? (
        <meshStandardMaterial map={brickTexture(part.color)} roughness={0.9} />
      ) : (
        <meshStandardMaterial color={part.color} roughness={0.7} transparent={part.opacity !== undefined}
          opacity={part.opacity ?? 1} depthWrite={part.opacity === undefined} />
      )}
    </mesh>
  );
}

/**
 * A door, window or plain opening in a wall gap: the wall above (lintel) and below (sill) in the
 * wall's material, the door leaf or glass from LOD 300, frames from LOD 400, details on hover at 500.
 */
const Opening = memo(function Opening({
  element, lod, selected, onSelect, hovered = false, onHover, hostColor, inward,
}: {
  element: MeshElement; lod: LOD; selected: boolean; onSelect: (id: string) => void;
  hovered?: boolean; onHover?: (id: string | null) => void; hostColor: string;
  /** Storey centre (three.js units): the half of a brick lintel or sill towards it is plastered. */
  inward?: [number, number, number];
}) {
  const o = element.opening!;
  // A window in an office building's glass facade has dark aluminium frames, like the curtain wall round it.
  const facade = useContext(FacadeContext);
  const windowFrame = facade === 'glass' && element.is_external ? CURTAIN.mullion : WINDOW_FRAME;
  const parts = useMemo(() => {
    const vs = element.mesh.vertices;
    const xs = vs.map(v => v[0]), ys = vs.map(v => v[1]), zs = vs.map(v => v[2]);
    const [x0, x1, y0, y1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys), Math.min(...zs), Math.max(...zs)];
    const sill = z0 + o.sill_mm, head = z0 + o.head_mm;
    const alongX = o.axis === 'h';
    // Slices across the wall thickness and along the gap.
    const across = (frac: number) => {
      const lo = alongX ? y0 : x0, hi = alongX ? y1 : x1, mid = (lo + hi) / 2, half = ((hi - lo) * frac) / 2;
      return [mid - half, mid + half];
    };
    const box = (a0: number, a1: number, c: number[], za: number, zb: number, color: string, opacity?: number): Part =>
      alongX ? { from: [a0, c[0], za], to: [a1, c[1], zb], color, opacity } : { from: [c[0], a0, za], to: [c[1], a1, zb], color, opacity };
    const a0 = alongX ? x0 : y0, a1 = alongX ? x1 : y1;
    const full = across(1);
    const list: Part[] = [];
    const color = selected ? SELECTED : hostColor;
    const brick = !selected && lod === '500' && hostColor === REAL.brick;
    // Wall above (lintel) and below (sill): brick outside; with a storey centre, the inner half is plaster.
    const wallPart = (za: number, zb: number) => {
      if (!brick || !inward) return [{ ...box(a0, a1, full, za, zb, color), brick }];
      const middle = (full[0] + full[1]) / 2;
      const centreAcross = (alongX ? inward[2] : inward[0]) * MM_PER_M;
      const innerHigh = centreAcross > middle;
      return [{ ...box(a0, a1, innerHigh ? [full[0], middle] : [middle, full[1]], za, zb, color), brick },
        box(a0, a1, innerHigh ? [middle, full[1]] : [full[0], middle], za, zb, REAL.plaster)];
    };
    if (head < z1 - 1) list.push(...wallPart(head, z1));
    if (element.element_type === 'window' && sill > z0 + 1) list.push(...wallPart(z0, sill));
    const framed = lod === '400' || lod === '500';
    const inset = framed ? FRAME_MM : 0;
    const detailed = lod === '500';
    // LOD 500 trims: a board round the opening on both wall faces (presentation detail, not a drawing size).
    const trims = (bottom: number, color: string) => {
      const [lo, hi] = full;
      for (const [f0, f1] of [[hi, hi + TRIM_PROUD_MM], [lo - TRIM_PROUD_MM, lo]]) {
        list.push(box(a0 - TRIM_MM, a0, [f0, f1], bottom, head + TRIM_MM, color),
          box(a1, a1 + TRIM_MM, [f0, f1], bottom, head + TRIM_MM, color),
          box(a0 - TRIM_MM, a1 + TRIM_MM, [f0, f1], head, head + TRIM_MM, color));
      }
    };
    if (element.lift_door && lod !== '100' && lod !== '200') {
      const doorHead = Math.min(head, z0 + 2100);
      const mid = (a0 + a1) / 2;
      const leaf = across(0.2);
      list.push(box(a0 + inset, mid - 3, leaf, z0, doorHead - inset, selected ? SELECTED : LIFT_STEEL),
        box(mid + 3, a1 - inset, leaf, z0, doorHead - inset, selected ? SELECTED : LIFT_STEEL));
      if (doorHead < head - 1) list.push(box(a0, a1, full, doorHead, head, selected ? SELECTED : hostColor));
      if (framed) {
        const depth = across(0.6);
        list.push(box(a0, a0 + FRAME_MM, depth, z0, doorHead, HANDLE), box(a1 - FRAME_MM, a1, depth, z0, doorHead, HANDLE),
          box(a0, a1, depth, doorHead - FRAME_MM, doorHead, HANDLE));
      }
    } else if (element.element_type === 'door' && detailed) {
      const exterior = !!element.is_external;
      const leafColor = selected ? SELECTED : exterior ? DOOR_PAINT : DOOR_WOOD;
      const panelColor = selected ? SELECTED : exterior ? DOOR_PAINT_PANEL : DOOR_WOOD_PANEL;
      const frameColor = exterior ? windowFrame : DOOR_FRAME;
      const leaves = a1 - a0 >= 1100 ? 2 : 1;
      const leafWidth = (a1 - a0 - 2 * FRAME_MM) / leaves;
      const leaf = across(0.18);
      for (let n = 0; n < leaves; n += 1) {
        const l0 = a0 + FRAME_MM + n * leafWidth, l1 = l0 + leafWidth;
        list.push(box(l0 + 2, l1 - 2, leaf, z0 + 10, head - FRAME_MM, leafColor));
        // Raised panels on both faces: 2 x 3 on a single leaf, 1 x 3 on each of a pair.
        const cols = leaves === 1 ? 2 : 1, rows = 3, margin = 110, gap = 90;
        const pw = (leafWidth - 2 * margin - (cols - 1) * gap) / cols;
        const ph = (head - FRAME_MM - z0 - 2 * margin - (rows - 1) * gap) / rows;
        for (let c = 0; c < cols; c += 1) {
          for (let r = 0; r < rows; r += 1) {
            const p0 = l0 + margin + c * (pw + gap), q0 = z0 + margin + r * (ph + gap);
            list.push(box(p0, p0 + pw, [leaf[1], leaf[1] + PANEL_PROUD_MM], q0, q0 + ph, panelColor),
              box(p0, p0 + pw, [leaf[0] - PANEL_PROUD_MM, leaf[0]], q0, q0 + ph, panelColor));
          }
        }
        // Handle at the latch side, on both faces.
        const latch = leaves === 1 ? l1 - 90 : (n === 0 ? l1 - 70 : l0 + 40);
        list.push(box(latch, latch + 30, [leaf[0] - HANDLE_MM, leaf[1] + HANDLE_MM], z0 + 1000, z0 + 1030, HANDLE));
      }
      const depth = across(0.7);
      list.push(box(a0, a0 + FRAME_MM, depth, z0, head, frameColor), box(a1 - FRAME_MM, a1, depth, z0, head, frameColor),
        box(a0, a1, depth, head - FRAME_MM, head, frameColor));
      trims(z0, frameColor);
    } else if (element.element_type === 'window' && detailed) {
      const glass = across(0.1);
      list.push(box(a0 + FRAME_MM, a1 - FRAME_MM, glass, sill + FRAME_MM, head - FRAME_MM, selected ? SELECTED : GLASS, 0.4));
      const depth = across(0.5);
      list.push(box(a0, a0 + FRAME_MM, depth, sill, head, windowFrame), box(a1 - FRAME_MM, a1, depth, sill, head, windowFrame),
        box(a0, a1, depth, head - FRAME_MM, head, windowFrame), box(a0, a1, depth, sill, sill + FRAME_MM, windowFrame));
      // Glazing bars: a vertical bar about every 600 mm and one across the middle.
      const bars = across(0.25);
      const bays = Math.max(2, Math.round((a1 - a0) / 600));
      for (let k = 1; k < bays; k += 1) {
        const at = a0 + ((a1 - a0) * k) / bays;
        list.push(box(at - MUNTIN_MM / 2, at + MUNTIN_MM / 2, bars, sill + FRAME_MM, head - FRAME_MM, windowFrame));
      }
      if (head - sill > 700) {
        const mid = (sill + head) / 2;
        list.push(box(a0 + FRAME_MM, a1 - FRAME_MM, bars, mid - MUNTIN_MM / 2, mid + MUNTIN_MM / 2, windowFrame));
      }
      // Sill projecting from both wall faces, and trims round the opening.
      const [lo, hi] = full;
      list.push(box(a0 - SILL_OVERHANG_MM, a1 + SILL_OVERHANG_MM, [lo - SILL_OVERHANG_MM, hi + SILL_OVERHANG_MM],
        sill - SILL_HEIGHT_MM, sill, SILL_STONE));
      trims(sill, windowFrame);
    } else if (element.element_type === 'door') {
      list.push(box(a0 + inset, a1 - inset, across(0.18), z0, head - inset, selected ? SELECTED : DOOR_WOOD));
      if (framed) {
        const depth = across(0.7);
        list.push(box(a0, a0 + FRAME_MM, depth, z0, head, DOOR_FRAME), box(a1 - FRAME_MM, a1, depth, z0, head, DOOR_FRAME),
          box(a0, a1, depth, head - FRAME_MM, head, DOOR_FRAME));
      }
    } else if (element.element_type === 'window') {
      list.push(box(a0 + inset, a1 - inset, across(0.1), sill + inset, head - inset, selected ? SELECTED : GLASS, 0.45));
      if (framed) {
        const depth = across(0.5);
        list.push(box(a0, a0 + FRAME_MM, depth, sill, head, windowFrame), box(a1 - FRAME_MM, a1, depth, sill, head, windowFrame),
          box(a0, a1, depth, head - FRAME_MM, head, windowFrame), box(a0, a1, depth, sill, sill + FRAME_MM, windowFrame));
        if (a1 - a0 > 1200) {
          const mid = (a0 + a1) / 2;
          list.push(box(mid - FRAME_MM / 2, mid + FRAME_MM / 2, depth, sill, head, windowFrame));
        }
      }
    }
    return { list, top: toThree([(x0 + x1) / 2, (y0 + y1) / 2, head + 400]) };
  }, [element, lod, selected, hostColor, o, inward, windowFrame]);

  const click = (event: any) => {
    event.stopPropagation();
    if ((event.delta ?? 0) <= CLICK_SLOP_PX) onSelect(element.element_id);
  };
  const over = (event: any) => { event.stopPropagation(); document.body.style.cursor = 'pointer'; onHover?.(element.element_id); };
  const out = () => { document.body.style.cursor = ''; onHover?.(null); };
  const kind = element.element_type === 'opening' ? 'Opening' : element.element_type === 'door' ? 'Door' : 'Window';
  return (
    <group>
      {parts.list.map((part, i) => <PartBox key={i} part={part} onClick={click} onOver={over} onOut={out} />)}
      {lod === '500' && (hovered || selected) && (
        <Html position={parts.top} center style={{ pointerEvents: 'none' }}>
          <div style={{ background: 'rgba(15,23,42,.85)', color: '#f8fafc', fontSize: 10, padding: '2px 6px', borderRadius: 4, whiteSpace: 'nowrap' }}>
            {kind}{o.tag ? ` ${o.tag}` : ''} · {Math.round(o.width_mm)} × {Math.round(o.head_mm - o.sill_mm)} mm
            {o.height_source === 'assumed_typical' ? ' (height assumed)' : ''}
          </div>
        </Html>
      )}
    </group>
  );
});

const isOpening = (e: MeshElement) => e.detected_by === 'wall_gap';

/** If the plan image cannot load, show a grid instead of crashing the page. */
class GroundFallback extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed
      ? <gridHelper args={[200, 40, '#94a3b8', '#cbd5e1']} />
      : this.props.children;
  }
}

function PlanGround({ url, sizeMm }: { url: string; sizeMm: number[] }) {
  const texture = useLoader(THREE.TextureLoader, url);
  useEffect(() => {
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 8;
    texture.needsUpdate = true;
  }, [texture]);
  const width = sizeMm[0] / MM_PER_M;
  const depth = sizeMm[1] / MM_PER_M;
  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[width / 2, -0.01, depth / 2]} receiveShadow>
      <planeGeometry args={[width, depth]} />
      <meshStandardMaterial map={texture} roughness={1} />
    </mesh>
  );
}

function DrawingModelView({
  projectId,
  drawingId,
}: {
  projectId: string;
  drawingId: string;
}) {
  const [model, setModel] = useState<MeshResponse | null>(null);
  const [status, setStatus] = useState<'loading' | 'none' | 'ready' | 'error'>('loading');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [lod, setLod] = useState<LOD>('300');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Frame the confirmed walls by default; the whole sheet is one click away.
  const [frame, setFrame] = useState<'walls' | 'sheet'>('walls');
  const [showDrawing, setShowDrawing] = useState(true);
  const [frameNonce, setFrameNonce] = useState(0);

  const load = useCallback(async () => {
    setStatus('loading');
    setSelectedId(null);
    try {
      const response = await fetch(`${API}/projects/${projectId}/drawings/${drawingId}/meshes`);
      const body = await response.json().catch(() => ({}));
      if (response.status === 404) {
        setModel(null);
        setStatus('none');
        return;
      }
      if (!response.ok) throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
      setModel(body as MeshResponse);
      setStatus('ready');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Could not load the model.');
      setStatus('error');
    }
  }, [projectId, drawingId]);

  useEffect(() => {
    setMessage('');
    load();
  }, [load]);

  async function generate() {
    setBusy(true);
    setMessage('');
    try {
      const response = await fetch(
        `${API}/projects/${projectId}/geometry/stage5-4/generate?drawing_id=${drawingId}&page_number=${PAGE_NUMBER}`,
        { method: 'POST' },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatDetail(body?.detail) || `Generation failed (${response.status})`);
      const { meshes_built, failed } = body.summary;
      setMessage(`Generated ${meshes_built} wall mesh(es)${failed ? `; ${failed} failed checks` : ''}.`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Generation failed.');
    } finally {
      setBusy(false);
    }
  }

  const visible = useMemo(
    () => (model?.elements ?? []).filter(e => Number(lod) >= (e.lod?.minimum_lod ?? 300)),
    [model, lod],
  );
  const selected = model?.elements.find(e => e.element_id === selectedId) ?? null;
  const sizeMm = model?.page?.size_mm ?? null;
  const previewUrl = `${API}/projects/${projectId}/phase6/preview/${drawingId}?page_number=${PAGE_NUMBER}`;
  // With no walls visible (LOD 100/200) there is nothing to frame but the sheet.
  // Without the drawing underlay there is no sheet to frame: frame the model.
  const effectiveFrame = !showDrawing ? 'walls' : visible.length > 0 ? frame : 'sheet';
  const modelOnly = (
    <>
      <gridHelper args={[300, 60, '#b6c2cf', '#dbe2ea']} position={[30, -0.01, 40]} />
      <ContactShadows position={[30, 0, 40]} scale={200} blur={2.5} far={40} opacity={0.35} frames={1} resolution={1024} />
    </>
  );
  const ground = !showDrawing ? modelOnly : sizeMm ? (
    <GroundFallback>
      <Suspense fallback={null}>
        <PlanGround url={previewUrl} sizeMm={sizeMm} />
      </Suspense>
    </GroundFallback>
  ) : (
    <gridHelper args={[200, 40, '#94a3b8', '#cbd5e1']} />
  );

  const generateButton = (
    <button className="primary" disabled={busy} onClick={generate}>
      {busy ? 'Generating…' : model ? 'Regenerate from confirmed walls' : 'Generate 3D model from confirmed walls'}
    </button>
  );

  if (status === 'loading') return <div className="message">Loading 3D model...</div>;

  if (status !== 'ready' || !model) {
    return (
      <div style={{ display: 'grid', gap: 12 }}>
        <div className="message">
          {status === 'error'
            ? message
            : 'No 3D model has been generated for this drawing yet. Confirm walls in section 04, then generate.'}
        </div>
        <div>{generateButton}</div>
        {status === 'none' && message && <div className="message" style={{ color: 'var(--warn)' }}>{message}</div>}
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 14, color: 'var(--ink-2)', fontSize: 13 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, alignItems: 'center' }}>
        <span>Walls in model: {model.serving_gate.served}</span>
        <span style={{ color: model.serving_gate.withheld ? 'var(--warn)' : undefined }}>
          Withheld: {model.serving_gate.withheld}
        </span>
        <span>Stage {model.stage}</span>
        <div style={{ marginLeft: 'auto' }}>{generateButton}</div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>Level of detail</span>
        {LODS.map(level => (
          <button key={level} onClick={() => setLod(level)} style={lodButton(lod === level)}>
            LOD {level}
          </button>
        ))}
        <span style={{ color: 'var(--muted)', fontSize: 12, marginLeft: 12 }}>Frame</span>
        <button
          onClick={() => { setFrame('walls'); setFrameNonce(n => n + 1); }}
          disabled={visible.length === 0}
          style={lodButton(effectiveFrame === 'walls')}
        >
          Walls
        </button>
        <button
          onClick={() => { setFrame('sheet'); setFrameNonce(n => n + 1); }}
          disabled={!showDrawing}
          style={lodButton(effectiveFrame === 'sheet')}
        >
          Whole sheet
        </button>
        <span style={{ color: 'var(--muted)', fontSize: 12, marginLeft: 12 }}>Drawing underlay</span>
        <button onClick={() => { setShowDrawing(true); setFrameNonce(n => n + 1); }} style={lodButton(showDrawing)}>
          Show
        </button>
        <button onClick={() => { setShowDrawing(false); setFrameNonce(n => n + 1); }} style={lodButton(!showDrawing)}>
          Hide (model only)
        </button>
      </div>

      <div style={noteStyle}>{LOD_NOTES[lod]}</div>

      <div style={{
        height: 560, width: '100%', overflow: 'hidden', borderRadius: 14,
        border: '1px solid var(--line)', background: 'linear-gradient(#eef3fa, #fbfcfe)',
      }}>
        <Canvas shadows camera={{ position: [60, 90, 140], fov: 45, near: 0.1, far: 5000 }}>
          <hemisphereLight args={['#ffffff', '#b0a998', 0.6]} />
          <ambientLight intensity={0.35} />
          <directionalLight
            position={[120, 180, 60]}
            intensity={1.4}
            castShadow
            shadow-mapSize-width={2048}
            shadow-mapSize-height={2048}
            shadow-camera-left={-200}
            shadow-camera-right={200}
            shadow-camera-top={200}
            shadow-camera-bottom={-200}
            shadow-camera-far={600}
          />
          <Suspense fallback={null}>
            {/* Bounds frames only what is inside it: the walls by default, or the whole sheet.
                The key remounts it so switching target or LOD re-frames the camera. */}
            <Bounds
              key={`${effectiveFrame}-${frameNonce}`}
              fit
              clip
              observe
              margin={effectiveFrame === 'walls' ? 1.6 : 1.15}
            >
              <group onPointerMissed={() => setSelectedId(null)}>
                {effectiveFrame === 'sheet' && ground}
                {visible.map(element => isOpening(element) ? (
                  <Opening
                    key={element.element_id}
                    element={element}
                    lod={lod}
                    selected={element.element_id === selectedId}
                    onSelect={setSelectedId}
                    hostColor={lod === '500' ? componentColor(element.host_component ?? 'wall') : CONCRETE}
                  />
                ) : (
                  <Wall
                    key={element.element_id}
                    element={element}
                    lod={lod}
                    selected={element.element_id === selectedId}
                    onSelect={setSelectedId}
                  />
                ))}
              </group>
            </Bounds>
            {effectiveFrame === 'walls' && ground}
          </Suspense>
          <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.05} />
        </Canvas>
      </div>

      <small style={{ color: 'var(--muted)' }}>
        {model.drawing_title} · Page {PAGE_NUMBER} · page-local metres from a calibration that still
        requires verification
        {model.calibration ? ` (${model.calibration.millimetres_per_pdf_point.toFixed(3)} mm per PDF point)` : ''}
        {' '}· wall bases at page-local zero; floor levels not yet associated · drag to orbit, right-drag to pan,
        scroll to zoom, click a wall to inspect.
      </small>

      {message && <div className="message">{message}</div>}

      {selected && (
        <div style={{ ...noteStyle, display: 'grid', gap: 6 }}>
          <strong style={{ color: 'var(--ink)' }}>
            {(selected.component ?? 'wall').replace(/^./, c => c.toUpperCase())} · {selected.element_id}
          </strong>
          <span>
            Length {mm(selected.dimensions_mm.length)} · Thickness {mm(selected.dimensions_mm.thickness)} ·
            Height {mm(selected.dimensions_mm.height)} · Volume{' '}
            {selected.dimensions_mm.volume == null ? '—' : (selected.dimensions_mm.volume / 1e9).toFixed(2)} m³
          </span>
          {selected.height_verification && (
            <span>
              Height confirmed by {selected.height_verification.reviewer ?? 'reviewer'} from “
              {(selected.height_verification.printed_measurements ?? []).join(', ')}” · verification{' '}
              {selected.height_verification.verification_id}
            </span>
          )}
          {selected.provenance?.source_lines && (
            <span>
              Source lines:{' '}
              {Object.entries(selected.provenance.source_lines)
                .map(([key, ref]) => `${key} (${ref.path_index}, ${ref.item_index})`)
                .join(' · ')}
            </span>
          )}
        </div>
      )}

      {model.withheld_elements.length > 0 && (
        <details>
          <summary>{model.withheld_elements.length} element(s) withheld by the height gate</summary>
          <ul>
            {model.withheld_elements.map((item, index) => (
              <li key={index}>
                {item.element_id ?? 'unknown element'}: {item.reasons.join(' ')}
              </li>
            ))}
          </ul>
        </details>
      )}

      {model.limitations && (
        <details>
          <summary>Model limitations</summary>
          <ul>{model.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

const noteStyle: React.CSSProperties = {
  padding: '10px 12px',
  border: '1px solid var(--line)',
  borderRadius: 12,
  background: 'var(--surface-2)',
  fontSize: 12,
  lineHeight: 1.6,
};

// Innoclique brand: the chosen option is outlined in orange on a warm tint, the others sit on navy.
function lodButton(active: boolean): React.CSSProperties {
  return {
    padding: '8px 13px',
    borderRadius: 999,
    border: `1px solid ${active ? 'var(--orange)' : 'var(--line-2)'}`,
    background: active ? 'var(--orange-soft)' : 'var(--surface)',
    color: active ? 'var(--ink)' : 'var(--ink-2)',
    fontWeight: active ? 700 : 500,
    cursor: 'pointer',
    fontSize: 12,
    transition: 'background .15s, border-color .15s',
  };
}

type BuildingElement = MeshElement & {
  drawing_id: string;
  drawing_title: string | null;
  storey: { placement_id: string; base_level_key: string; base_elevation_mm: number };
};

type BuildingResponse = {
  facade?: { style: string; basis: string } | null;
  summary: {
    placed_plans: number; plans_in_model: number; walls_served: number; walls_withheld: number; plans_excluded: number;
    walls_superseded?: number; walls_joined?: number; columns_to_review?: number;
    doors?: number; windows?: number; openings?: number; rooms?: number;
  };
  storeys: {
    placement_id: string;
    drawing_title: string | null;
    base_level_key: string;
    base_elevation_mm: number;
    top_level_key: string | null;
    storey_height_mm: number | null;
    reference_label: string;
    walls_served: number;
    walls_withheld: number;
  }[];
  elements: BuildingElement[];
  superseded_elements?: { element_id: string; superseded_by: string; reason: string }[];
  rooms?: RoomLabel[];
  source?: { type: string; files: string[]; structure?: string };
  parameters?: { name: string; value: unknown; text: string | null; drawing?: string | null; assumed?: string }[];
  lod_notes?: Record<string, string>;
  slabs?: SlabData[];
  summary_note?: string;
  withheld_elements: { element_id: string | null; drawing_id: string; reasons: string[] }[];
  excluded_plans: { drawing_id: string; drawing_title: string | null; reason: string }[];
  limitations: string[];
};

/** A short floor name from the plan's title ("GROUND FLOOR", "BASEMENT - UPPER LEVEL"), else its level. */
function floorName(storey: { drawing_title: string | null; base_level_key: string; base_elevation_mm: number }) {
  const title = `${storey.drawing_title ?? ''} ${storey.base_level_key ?? ''}`.toUpperCase()
    .replace(/\.(PDF|DXF|IFC)\b/g, ' ').replace(/[_]+/g, ' ');
  const match = title.match(
    /\b(?:LOWER |UPPER )?(?:BASEMENT|GROUND|FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|MEZZANINE|TERRACE|ROOF|TYPICAL|PODIUM)(?: FLOOR)?(?: PLAN)?(?: - (?:UPPER|LOWER) LEVEL)?/);
  const name = match ? match[0].replace(' PLAN', '') : `Level ${elevation(storey.base_elevation_mm)}`;
  return name.toLowerCase().replace(/(^|\s|-)\w/g, c => c.toUpperCase());
}

type RoomLabel = {
  name: string; size_mm: number[] | null; position_mm: number[]; storey: { placement_id: string };
  bounds_mm?: number[];
};

const ROOM_FOV = 85;
const ROOM_FOV_RANGE: [number, number] = [45, 100];

/**
 * Inside a room the lens widens (85 degrees, like a wide-angle photo) and the near plane comes in. The mouse
 * wheel widens or narrows the view (45-100 degrees) instead of moving the camera.
 */
function RoomLens({ inside }: { inside: boolean }) {
  const camera = useThree(state => state.camera) as THREE.PerspectiveCamera;
  const element = useThree(state => state.gl.domElement);
  useEffect(() => {
    const saved = { fov: camera.fov, near: camera.near };
    if (!inside) return undefined;
    camera.fov = ROOM_FOV;
    camera.near = 0.05;
    camera.updateProjectionMatrix();
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      camera.fov = Math.min(ROOM_FOV_RANGE[1], Math.max(ROOM_FOV_RANGE[0], camera.fov + event.deltaY * 0.04));
      camera.updateProjectionMatrix();
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => {
      element.removeEventListener('wheel', onWheel);
      camera.fov = saved.fov;
      camera.near = saved.near;
      camera.updateProjectionMatrix();
    };
  }, [inside, camera, element]);
  return null;
}

function elevation(mmValue: number) {
  const sign = mmValue >= 0 ? '+' : '−';
  return `${sign}${Math.abs(mmValue / 1000).toFixed(2)} m`;
}

type SlabData = {
  element_id: string;
  level_key: string;
  elevation_mm: number;
  outline_mm: number[][];
  outline_method?: string;
  // polygons_mm: the same cells, cut along slanted walls (present only where a cut was needed).
  footprint?: { rects_mm: number[][]; edges_mm: number[][]; area_m2: number; method: string;
    polygons_mm?: number[][][] } | null;
  approximate: boolean;
  thickness_mm?: number | null;
  role?: 'roof' | 'floor' | 'ceiling';
  lod: { minimum_lod: number };
};

/**
 * Approximate slab: a flat plate at the slab level (thickness not established),
 * over the area enclosed by the storey's walls and columns, with its outline.
 */
function SlabBand({ slab, finish }: { slab: SlabData; finish: 'floor' | 'ceiling' }) {
  // Slab edge from the noted thickness, standing a hair proud of the wall faces so it reads as a band.
  const t = (slab.thickness_mm ?? 0) / MM_PER_M, proud = 0.05, top = slab.elevation_mm / MM_PER_M;
  const materials = useMemo(() => {
    const side = new THREE.MeshStandardMaterial({ color: REAL.band, roughness: 0.8 });
    const upper = new THREE.MeshStandardMaterial({ color: finish === 'floor' ? REAL.floor : REAL.ceiling, roughness: 0.9 });
    const lower = new THREE.MeshStandardMaterial({ color: REAL.ceiling, roughness: 0.9 });
    return [side, side, upper, lower, side, side];
  }, [finish]);
  // Cells cut along a slanted wall: extruded as drawn (no proud edge), so they end at the wall's face.
  const cut = useMemo(() => {
    const polygons = slab.footprint?.polygons_mm;
    if (!polygons?.length) return null;
    const geometries = polygons.map(poly => {
      const shape = new THREE.Shape(poly.map(([x, y]) => new THREE.Vector2(x / MM_PER_M, y / MM_PER_M)));
      const g = new THREE.ExtrudeGeometry(shape, { depth: t, bevelEnabled: false });
      g.rotateX(Math.PI / 2);                       // shape (x, plan y) -> three (x, z); extrusion goes down
      g.translate(0, top, 0);
      return g;
    });
    // One mesh for the whole slab: a slab cut along slanted walls has a thousand or more pieces.
    const merged = mergeGeometries(geometries, false);
    geometries.forEach(g => g.dispose());
    return merged;
  }, [slab, t, top]);
  if (cut) {
    return (
      <mesh geometry={cut} castShadow receiveShadow>
        <meshStandardMaterial color={finish === 'floor' ? REAL.floor : REAL.ceiling} roughness={0.9} />
      </mesh>
    );
  }
  return (
    <group>
      {(slab.footprint?.rects_mm ?? []).map(([x0, y0, x1, y1], i) => (
        <mesh key={i} material={materials} castShadow receiveShadow
          position={[(x0 + x1) / 2 / MM_PER_M, top - t / 2, (y0 + y1) / 2 / MM_PER_M]}>
          <boxGeometry args={[(x1 - x0) / MM_PER_M + 2 * proud, t, (y1 - y0) / MM_PER_M + 2 * proud]} />
        </mesh>
      ))}
    </group>
  );
}

function Slab({ slab, opacity, finish }: { slab: SlabData; opacity: number; finish?: 'roof' | 'floor' | 'ceiling' }) {
  if ((finish === 'floor' || finish === 'ceiling') && slab.thickness_mm && slab.footprint?.rects_mm?.length) {
    return <SlabBand slab={slab} finish={finish} />;
  }
  return <SlabPlate slab={slab} opacity={opacity} finish={finish} />;
}

function SlabPlate({ slab, opacity, finish }: { slab: SlabData; opacity: number; finish?: 'roof' | 'floor' | 'ceiling' }) {
  // An office building's flat roof is a grey membrane, not clay tiles.
  const facade = useContext(FacadeContext);
  // At LOD 500 the roof sits a hair above the wall tops so their outlines do not show through it.
  const y = slab.elevation_mm / MM_PER_M + (finish === 'roof' ? 0.02 : 0);
  const { plate, outline } = useMemo(() => {
    const positions: number[] = [];
    const lines: number[] = [];
    if (slab.footprint?.polygons_mm?.length) {
      // Cells cut along slanted walls: convex, so a fan from the first corner covers each.
      for (const poly of slab.footprint.polygons_mm) {
        for (let i = 1; i + 1 < poly.length; i += 1) {
          positions.push(poly[0][0] / MM_PER_M, y, poly[0][1] / MM_PER_M,
            poly[i][0] / MM_PER_M, y, poly[i][1] / MM_PER_M,
            poly[i + 1][0] / MM_PER_M, y, poly[i + 1][1] / MM_PER_M);
        }
      }
      for (const [x0, y0, x1, y1] of slab.footprint.edges_mm) {
        lines.push(x0 / MM_PER_M, y + 0.02, y0 / MM_PER_M, x1 / MM_PER_M, y + 0.02, y1 / MM_PER_M);
      }
    } else if (slab.footprint) {
      for (const [x0, y0, x1, y1] of slab.footprint.rects_mm) {
        const a = [x0 / MM_PER_M, y, y0 / MM_PER_M];
        const b = [x1 / MM_PER_M, y, y0 / MM_PER_M];
        const c = [x1 / MM_PER_M, y, y1 / MM_PER_M];
        const d = [x0 / MM_PER_M, y, y1 / MM_PER_M];
        positions.push(...a, ...b, ...c, ...a, ...c, ...d);
      }
      for (const [x0, y0, x1, y1] of slab.footprint.edges_mm) {
        lines.push(x0 / MM_PER_M, y + 0.02, y0 / MM_PER_M, x1 / MM_PER_M, y + 0.02, y1 / MM_PER_M);
      }
    } else {
      const pts = slab.outline_mm;
      for (let i = 1; i + 1 < pts.length; i += 1) {
        positions.push(pts[0][0] / MM_PER_M, y, pts[0][1] / MM_PER_M,
          pts[i][0] / MM_PER_M, y, pts[i][1] / MM_PER_M,
          pts[i + 1][0] / MM_PER_M, y, pts[i + 1][1] / MM_PER_M);
      }
      pts.forEach((p, i) => {
        const q = pts[(i + 1) % pts.length];
        lines.push(p[0] / MM_PER_M, y + 0.02, p[1] / MM_PER_M, q[0] / MM_PER_M, y + 0.02, q[1] / MM_PER_M);
      });
    }
    const plateGeometry = new THREE.BufferGeometry();
    plateGeometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    plateGeometry.setAttribute('uv', new THREE.Float32BufferAttribute(
      positions.flatMap((v, i) => (i % 3 === 1 ? [] : [v])), 2));
    plateGeometry.computeVertexNormals();
    const outlineGeometry = new THREE.BufferGeometry();
    outlineGeometry.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3));
    return { plate: plateGeometry, outline: outlineGeometry };
  }, [slab, y]);
  if (finish) {
    return (
      <mesh geometry={plate} receiveShadow castShadow={finish === 'roof'}>
        {finish === 'roof' && facade === 'glass' ? (
          <meshStandardMaterial color={CURTAIN.membrane} roughness={0.95} side={THREE.DoubleSide} />
        ) : finish === 'roof' ? (
          <meshStandardMaterial map={roofTileTexture(REAL.roof)} roughness={0.85} side={THREE.DoubleSide} />
        ) : (
          <meshStandardMaterial color={finish === 'ceiling' ? REAL.ceiling : REAL.floor} roughness={0.9}
            side={THREE.DoubleSide} polygonOffset polygonOffsetFactor={1} polygonOffsetUnits={1} />
        )}
      </mesh>
    );
  }
  return (
    <group>
      <mesh geometry={plate} receiveShadow>
        <meshStandardMaterial color="#d9dee4" transparent opacity={opacity} side={THREE.DoubleSide}
          depthWrite={false} polygonOffset polygonOffsetFactor={1} polygonOffsetUnits={1} />
      </mesh>
      <lineSegments geometry={outline}>
        <lineBasicMaterial color="#64748b" />
      </lineSegments>
    </group>
  );
}

// Plan centre of each storey (three.js units), to tell an outside wall's inner face from its outer face.
function storeyCentres(elements: BuildingElement[]): Record<string, [number, number, number]> {
  const boxes: Record<string, number[]> = {};
  elements.forEach(e => {
    if (e.element_type !== 'wall' || !e.is_external) return;
    const b = boxes[e.storey.placement_id] ?? [Infinity, Infinity, -Infinity, -Infinity];
    e.mesh.vertices.forEach(v => { b[0] = Math.min(b[0], v[0]); b[1] = Math.min(b[1], v[1]); b[2] = Math.max(b[2], v[0]); b[3] = Math.max(b[3], v[1]); });
    boxes[e.storey.placement_id] = b;
  });
  const centres: Record<string, [number, number, number]> = {};
  Object.entries(boxes).forEach(([id, b]) => { centres[id] = toThree([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 0]); });
  return centres;
}

export type ShowcaseSummary = { elements: number; storeys: number; rooms: number; furniture: number };

/**
 * The landing page's showcase: a built model turning slowly at the chosen LOD, with the same look as the
 * viewer but no editing tools. Drag to turn it; it keeps turning by itself unless the visitor prefers less motion.
 */
export function ShowcaseModel({ url, lod = '500', onLoaded }: {
  url: string; lod?: LOD; onLoaded?: (summary: ShowcaseSummary) => void;
}) {
  const [data, setData] = useState<BuildingResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const loaded = useRef(onLoaded);
  loaded.current = onLoaded;
  useEffect(() => {
    let cancelled = false;
    fetch(url)
      .then(response => { if (!response.ok) throw new Error(String(response.status)); return response.json(); })
      .then((body: BuildingResponse) => {
        if (cancelled) return;
        setData(body);
        loaded.current?.({
          elements: body.elements.length, storeys: body.storeys.length, rooms: (body.rooms ?? []).length,
          furniture: body.elements.filter(e => e.element_type === 'furniture').length,
        });
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [url]);

  const visible = useMemo(() => (data?.elements ?? []).filter(
    e => Number(lod) >= (e.lod?.minimum_lod ?? 300) && Number(lod) <= (e.lod?.maximum_lod ?? 500)), [data, lod]);
  const centres = useMemo(() => storeyCentres(data?.elements ?? []), [data]);
  // Where the model stands and how big it is (three.js units), for the ground shadow and the light.
  const ground = useMemo(() => {
    const vs = (data?.elements ?? []).flatMap(e => e.mesh.vertices);
    if (!vs.length) return { centre: [0, 0, 0] as [number, number, number], size: 20 };
    const xs = vs.map(v => v[0]), ys = vs.map(v => v[1]), zs = vs.map(v => v[2]);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    return {
      centre: toThree([(x0 + x1) / 2, (y0 + y1) / 2, Math.min(...zs)]),
      size: Math.max(x1 - x0, y1 - y0) / MM_PER_M,
    };
  }, [data]);
  const calm = useMemo(() => typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches, []);
  const noop = useCallback(() => {}, []);

  if (failed) return <div className="showcase-state">The sample model could not be loaded.</div>;
  if (!data) return <div className="showcase-state"><span className="loader" /> Loading the sample model…</div>;

  const [cx, gy, cz] = ground.centre;
  const reach = ground.size * 1.5 + 10;
  return (
    <Canvas shadows dpr={[1, 1.75]} gl={{ alpha: true, antialias: true }}
      camera={{ position: [cx - 40, gy + 26, cz + 58], fov: 34, near: 0.1, far: 3000 }}>
      <hemisphereLight args={['#ffffff', '#9fb3d6', 0.8]} />
      <ambientLight intensity={0.25} />
      <directionalLight position={[cx - 30, gy + 60, cz + 40]} intensity={1.6} castShadow shadow-bias={-0.0004}
        shadow-mapSize-width={2048} shadow-mapSize-height={2048}
        shadow-camera-left={-reach} shadow-camera-right={reach} shadow-camera-top={reach} shadow-camera-bottom={-reach}
        shadow-camera-far={400} />
      <directionalLight position={[cx + 50, gy + 25, cz - 40]} intensity={0.45} color="#ffd9b8" />
      <Suspense fallback={null}>
        <Bounds key={`showcase-${lod}`} fit clip margin={1.22}>
          <group>
            {visible.map(element => isOpening(element) ? (
              <Opening key={element.element_id} element={element} lod={lod} selected={false} onSelect={noop}
                inward={lod === '500' && element.is_external ? centres[element.storey.placement_id] : undefined}
                hostColor={lod !== '500' ? CONCRETE
                  : (wallFinish(element.host_component ?? '',
                      element.is_external ?? /brick|exterior|external/i.test(element.host_component ?? '')) ?? REAL.brick)} />
            ) : (
              <Wall key={element.element_id} element={element} lod={lod} selected={false} onSelect={noop}
                showLabel={false} look="realistic"
                inward={lod === '500' && element.is_external ? centres[element.storey.placement_id] : undefined} />
            ))}
            {(data.slabs ?? []).filter(slab => Number(lod) >= slab.lod.minimum_lod).map(slab => (
              <Slab key={slab.element_id} slab={slab} opacity={lod === '200' ? 0.8 : 0.4}
                finish={lod === '500' ? slab.role ?? 'floor' : undefined} />
            ))}
          </group>
        </Bounds>
        <ContactShadows position={[cx, gy + 0.01, cz]} scale={ground.size * 2.2 + 10} blur={2.4} far={20}
          opacity={0.55} frames={1} resolution={1024} color="#020817" />
      </Suspense>
      <OrbitControls makeDefault autoRotate={!calm} autoRotateSpeed={0.8} enableDamping dampingFactor={0.08}
        enableZoom={false} enablePan={false} minPolarAngle={Math.PI * 0.18} maxPolarAngle={Math.PI * 0.46} />
    </Canvas>
  );
}

function BuildingView({ projectId }: { projectId: string }) {
  const [data, setData] = useState<BuildingResponse | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [runReport, setRunReport] = useState<string[]>([]);

  async function generateAll() {
    setGenerating(true);
    setRunReport([]);
    try {
      const response = await fetch(`${API}/projects/${projectId}/model/building/generate`, { method: 'POST' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
      setRunReport((body.plans ?? []).map((plan: { drawing_title: string; status: string; confirmed_walls?: number; confirmed_hatch_walls?: number; confirmed_columns?: number; failed?: number; reason?: string }) =>
        plan.status === 'generated'
          ? `${plan.drawing_title}: ${(plan.confirmed_walls ?? 0) + (plan.confirmed_hatch_walls ?? 0)} wall(s), ${plan.confirmed_columns ?? 0} column(s) built${plan.failed ? `, ${plan.failed} failed checks` : ''}`
          : `${plan.drawing_title}: ${plan.reason}`));
      setReload(n => n + 1);
    } catch (err) {
      setRunReport([err instanceof Error ? err.message : 'Generation failed.']);
    } finally {
      setGenerating(false);
    }
  }
  // A project opens on its finished look (LOD 500); the simpler levels are one click away.
  const [lod, setLod] = useState<LOD>('500');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [colorBy, setColorBy] = useState<Look>('realistic');
  const [showRooms, setShowRooms] = useState(true);
  const [showRoof, setShowRoof] = useState(true);
  const [showFurniture, setShowFurniture] = useState(true);
  const [preset, setPreset] = useState<ViewPreset>('overview');
  const [presetNonce, setPresetNonce] = useState(0);
  const focusRef = useRef<((p: THREE.Vector3) => void) | null>(null);
  const roomRef = useRef<((eye: THREE.Vector3, look: THREE.Vector3) => void) | null>(null);
  const [roomView, setRoomView] = useState<{ room: RoomLabel; restore: { hidden: Set<string>; showRoof: boolean } } | null>(null);
  const goTo = (view: ViewPreset) => { setRoomView(null); setPreset(view); setPresetNonce(n => n + 1); };
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const floors = useMemo(
    () => [...(data?.storeys ?? [])].sort((a, b) => a.base_elevation_mm - b.base_elevation_mm), [data]);
  // The picked floor: every floor above it is hidden, so it is seen from above with its rooms and furniture.
  const pickedFloor = useMemo(() => {
    if (!hidden.size) return 'all';
    const index = floors.findIndex((f, i) => !hidden.has(f.placement_id)
      && floors.slice(i + 1).every(above => hidden.has(above.placement_id))
      && floors.slice(0, i).every(below => !hidden.has(below.placement_id)));
    return index >= 0 && index < floors.length - 1 ? floors[index].placement_id : 'custom';
  }, [floors, hidden]);
  const pickFloor = (placementId: string) => {
    if (placementId === 'all') {
      setHidden(new Set());
      setShowRoof(true);
      return;
    }
    const index = floors.findIndex(f => f.placement_id === placementId);
    setHidden(new Set(floors.slice(index + 1).map(f => f.placement_id)));
    if (index === floors.length - 1) setShowRoof(false);     // the top floor: lift its roof off
    goTo('interior');
  };
  // Walk into a room: stand at eye height a little in from one end of its longer side, facing the other end.
  // Every floor comes back on, so the floor above is its ceiling.
  const enterRoom = (room: RoomLabel) => {
    const [x, y, floorZ] = room.position_mm;
    const [b0, b1, b2, b3] = room.bounds_mm ?? [x - 1000, y - 1000, x + 1000, y + 1000];
    const alongX = b2 - b0 >= b3 - b1;
    const back = (span: number) => Math.min(Math.max(500, span * 0.1), span / 2);
    const eyeMm = alongX ? [b0 + back(b2 - b0), (b1 + b3) / 2, floorZ + 1600] : [(b0 + b2) / 2, b1 + back(b3 - b1), floorZ + 1600];
    const lookMm = alongX ? [b2, (b1 + b3) / 2, floorZ + 1300] : [(b0 + b2) / 2, b3, floorZ + 1300];
    setRoomView({ room, restore: { hidden: new Set(hidden), showRoof } });
    setHidden(new Set());
    setShowRoof(true);
    setSelectedId(null);
    roomRef.current?.(new THREE.Vector3(...toThree(eyeMm)), new THREE.Vector3(...toThree(lookMm)));
  };
  const leaveRoom = () => {
    if (!roomView) return;
    const { hidden: before, showRoof: roofBefore } = roomView.restore;
    setHidden(before);
    setShowRoof(roofBefore);
    goTo(before.size || !roofBefore ? 'interior' : 'overview');
  };
  const toggleStorey = (placementId: string) => setHidden(prev => {
    const next = new Set(prev);
    if (next.has(placementId)) next.delete(placementId); else next.add(placementId);
    return next;
  });

  // A Generate run is in progress and the server sent the model built before it (reloaded when the run ends).
  const [updating, setUpdating] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError('');
    fetch(`${API}/projects/${projectId}/model/building`)
      .then(async response => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(formatDetail(body?.detail) || `Request failed (${response.status})`);
        if (!cancelled) {
          setUpdating(response.headers.get('X-Model-Updating') === '1');
          setData(body as BuildingResponse);
        }
      })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the building.'); });
    return () => { cancelled = true; };
  }, [projectId, reload]);

  const visible = useMemo(
    () => (data?.elements ?? []).filter(
      e => Number(lod) >= (e.lod?.minimum_lod ?? 300) && Number(lod) <= (e.lod?.maximum_lod ?? 500)
        && !hidden.has(e.storey.placement_id)
        && !(lod === '500' && !showRoof && (ROOF_TYPES.has(e.element_type ?? '') || e.wall_type === 'gable'))
        && !(!showFurniture && e.element_type === 'furniture')),
    [data, lod, hidden, showRoof, showFurniture],
  );
  useEffect(() => {
    if (!roomView) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') leaveRoom(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomView]);
  useEffect(() => { setRoomView(null); }, [lod]);
  const furnitureCount = useMemo(() => (data?.elements ?? []).filter(e => e.element_type === 'furniture').length, [data]);
  const hasRoof = useMemo(
    () => (data?.elements ?? []).some(e => ROOF_TYPES.has(e.element_type ?? ''))
      || (data?.slabs ?? []).some(slab => slab.role === 'roof' || slab.role === 'ceiling'),
    [data],
  );
  const topFloorHidden = floors.length > 0 && hidden.has(floors[floors.length - 1].placement_id);
  const roofOff = !showRoof || !hasRoof || topFloorHidden;
  const roomsVisible = lod === '500' && showRooms && roofOff;

  // Room names for one storey at a time (the highest one shown), so floors do not overlap.
  const roomStorey = useMemo(() => {
    const shown = (data?.storeys ?? []).filter(s => !hidden.has(s.placement_id));
    return shown.length ? shown.reduce((a, b) => (b.base_elevation_mm > a.base_elevation_mm ? b : a)).placement_id : null;
  }, [data, hidden]);

  const storeyCentre = useMemo(() => storeyCentres(data?.elements ?? []), [data]);
  const storeyColor = useMemo(() => {
    const map: Record<string, string> = {};
    (data?.storeys ?? []).forEach((s, i) => { map[s.placement_id] = STOREY_COLORS[i % STOREY_COLORS.length]; });
    return map;
  }, [data]);

  const legend = useMemo(() => {
    const counts = new Map<string, number>();
    visible.forEach(e => {
      const key = colorBy === 'storey' ? e.storey.placement_id
        : e.element_type === 'furniture' ? 'furniture' : (e.component ?? 'wall');
      counts.set(key, (counts.get(key) ?? 0) + 1);
    });
    return [...counts.entries()].map(([key, count]) => {
      const storey = data?.storeys.find(s => s.placement_id === key);
      return {
        key,
        label: colorBy === 'storey'
          ? `${storey?.base_level_key ?? key} · ${(storey?.drawing_title ?? '').replace(/^.*?(ARC-\d+).*$/, '$1')}`
          : key,
        color: colorBy === 'storey' ? storeyColor[key] : componentColor(key),
        count,
      };
    }).sort((a, b) => b.count - a.count).concat(
      visible.some(e => e.review_flags?.length)
        ? [{ key: 'review', label: 'column to review (isolated)', color: REVIEW,
             count: visible.filter(e => e.review_flags?.length).length }]
        : [],
    );
  }, [visible, colorBy, data, storeyColor]);

  const levelMarkers = useMemo(() => {
    const xs = (data?.elements ?? []).flatMap(e => e.mesh.vertices.map(v => v[0]));
    const ys = (data?.elements ?? []).flatMap(e => e.mesh.vertices.map(v => v[1]));
    if (!xs.length) return [];
    const x = Math.max(...xs) + 3000;
    const y = Math.min(...ys);
    return (data?.storeys ?? [])
      .filter(s => !hidden.has(s.placement_id))
      .map(s => ({
        key: s.placement_id,
        label: `${s.base_level_key.split('@')[0]} ${elevation(s.base_elevation_mm)}`,
        position: toThree([x, y, s.base_elevation_mm]),
      }));
  }, [data, hidden]);

  const groundY = useMemo(() => {
    const zs = (data?.elements ?? []).flatMap(e => e.mesh.vertices.map(v => v[2]));
    return zs.length ? Math.min(...zs) / MM_PER_M - 0.02 : 0;
  }, [data]);

  if (error) return <div className="message" style={{ color: 'var(--warn)' }}>{error}</div>;
  if (!data) return <div className="message">Loading building model...</div>;

  const selected = data.elements.find(e => e.element_id === selectedId) ?? null;
  const countFor = (placementId: string | null, columns: boolean) =>
    data.elements.filter(e => (placementId === null || e.storey.placement_id === placementId)
      && e.element_type === (columns ? 'column' : 'wall')).length;

  const storeyCount = data.source?.type === 'structure' ? (data.elements.length ? 1 : 0) : data.summary.plans_in_model;
  const doorCount = data.summary.doors ?? 0;
  const windowCount = data.summary.windows ?? 0;

  return (
    <div style={{ display: 'grid', gap: 14, color: 'var(--ink-2)', fontSize: 13 }}>
      {updating && (
        <div className="note warn">Generate is running: showing the last model built. It reloads when the run finishes.</div>
      )}
      {data.source?.type === 'structure' && data.elements.length === 0 && (
        <div className="note warn">{data.limitations[0]}</div>
      )}

      {data.elements.length === 0 ? (
        <div className="empty">No 3D model yet. Add drawings or a model file and press “Generate 3D model”.</div>
      ) : (
        <>
          <div className="model-stats">
            <span><strong>{storeyCount}</strong> {storeyCount === 1 ? 'floor' : 'floors'}</span>
            <span><strong>{data.elements.length}</strong> elements</span>
            {!!data.summary.rooms && <span><strong>{data.summary.rooms}</strong> rooms</span>}
            {doorCount + windowCount > 0 && <span><strong>{doorCount}</strong> doors · <strong>{windowCount}</strong> windows</span>}
            {furnitureCount > 0 && <span><strong>{furnitureCount}</strong> furniture &amp; equipment</span>}
            {data.source && (
              <span className="model-source">
                {data.source.type === 'structure' ? 'from the dimensions printed on the drawings' : `from the ${data.source.type.toUpperCase()} model`}
              </span>
            )}
          </div>
          <div className="viewer-toolbar">
            <div className="seg" role="group" aria-label="Level of detail">
              <span className="seg-label">LOD</span>
              {LODS.map(level => (
                <button key={level} className={lod === level ? 'on' : ''} onClick={() => setLod(level)}>{level}</button>
              ))}
            </div>
            <div className="seg" role="group" aria-label="View">
              <span className="seg-label">View</span>
              {(['overview', 'front', 'back', 'plan'] as ViewPreset[]).map(view => (
                <button key={view} className={presetNonce > 0 && preset === view ? 'on' : ''} onClick={() => goTo(view)}>
                  {view[0].toUpperCase() + view.slice(1)}
                </button>
              ))}
            </div>
            {floors.length > 1 && (
              <div className="seg" role="group" aria-label="Floor">
                <span className="seg-label">Floor</span>
                <button className={pickedFloor === 'all' && showRoof ? 'on' : ''} onClick={() => pickFloor('all')}>All</button>
                {floors.map((floor, i) => (
                  <button key={floor.placement_id}
                    className={pickedFloor === floor.placement_id || (i === floors.length - 1 && pickedFloor === 'all' && !showRoof) ? 'on' : ''}
                    onClick={() => pickFloor(floor.placement_id)} title={`${floor.base_level_key} · ${floor.drawing_title ?? ''}`}>
                    {floorName(floor)}
                  </button>
                ))}
              </div>
            )}
            {lod === '500' && hasRoof && (
              <div className="seg" role="group" aria-label="Roof">
                <span className="seg-label">Roof</span>
                <button className={showRoof ? 'on' : ''} onClick={() => setShowRoof(true)}>Show</button>
                <button className={!showRoof ? 'on' : ''} onClick={() => setShowRoof(false)}>Hide</button>
              </div>
            )}
            {lod === '500' && (data.rooms ?? []).length > 0 && (
              <div className="seg" role="group" aria-label="Room names">
                <span className="seg-label">Room names</span>
                {/* The names stand inside the rooms: showing them lifts the roof off. */}
                <button className={roomsVisible ? 'on' : ''}
                  onClick={() => { setShowRooms(true); if (!roofOff) setShowRoof(false); }}>Show</button>
                <button className={!roomsVisible ? 'on' : ''} onClick={() => setShowRooms(false)}>Hide</button>
              </div>
            )}
            {lod === '500' && furnitureCount > 0 && (
              <div className="seg" role="group" aria-label="Furniture">
                <span className="seg-label">Furniture</span>
                <button className={showFurniture ? 'on' : ''} onClick={() => setShowFurniture(true)}>Show</button>
                <button className={!showFurniture ? 'on' : ''} onClick={() => setShowFurniture(false)}>Hide</button>
              </div>
            )}
          </div>
          {lod === '500' && (data.rooms ?? []).length > 0 && !roomView && (
            <div className="viewer-hint">
              {roomsVisible
                ? 'Click a room name to walk into that room and look around 360°. Press Esc to come back out.'
                : 'Show the room names to see every room, then click one to walk in and look around 360°.'}
            </div>
          )}
        </>
      )}

      {data.elements.length > 0 && (
        <div style={{
          position: 'relative', height: 600, width: '100%', overflow: 'hidden', borderRadius: 16,
          border: '1px solid var(--line)',
          background: lod === '500' ? 'radial-gradient(circle at 50% 26%, #ffffff 0%, #eef3fa 55%, #dbe4f1 100%)' : 'linear-gradient(#eef3fa, #fbfcfe)',
        }}>
          <HoverCard element={data.elements.find(e => e.element_id === (hoveredId ?? selectedId)) ?? null} />
          <div style={{
            position: 'absolute', right: 14, bottom: 10, zIndex: 1, pointerEvents: 'none', fontSize: 11,
            fontFamily: 'ui-monospace, monospace', color: lod === '500' ? '#8b8b93' : '#64748b',
          }}>
            {roomView ? 'drag to look around 360° · scroll to widen or narrow the view · Esc to leave'
              : `drag orbit · scroll zoom · right-drag pan · double-click to centre${lod === '500' ? ' · click a room name to walk in' : ''}`}
          </div>
          {roomView && (
            <div style={{
              position: 'absolute', left: 14, top: 12, zIndex: 2, display: 'flex', alignItems: 'center', gap: 10,
              background: 'rgba(15,15,18,.86)', color: '#f1f5f9', padding: '6px 10px', borderRadius: 10, fontSize: 12,
              border: '1px solid rgba(255,255,255,.08)',
            }}>
              <span>Inside <strong>{roomView.room.name}</strong>
                {roomView.room.size_mm && ` · ${(roomView.room.size_mm[0] / 1000).toFixed(1)} m × ${(roomView.room.size_mm[1] / 1000).toFixed(1)} m`}
              </span>
              <button onClick={leaveRoom} style={lodButton(false)}>Exit room view</button>
            </div>
          )}
          <Canvas shadows camera={{ position: [-70, 80, 150], fov: 40, near: 0.1, far: 5000 }}>
            <hemisphereLight args={['#ffffff', '#c9c2b4', 0.75]} />
            <ambientLight intensity={0.25} />
            <directionalLight
              position={[-90, 160, 110]}
              intensity={1.5}
              castShadow
              shadow-bias={-0.0004}
              shadow-mapSize-width={4096}
              shadow-mapSize-height={4096}
              shadow-camera-left={-150}
              shadow-camera-right={150}
              shadow-camera-top={150}
              shadow-camera-bottom={-150}
              shadow-camera-far={600}
            />
            <directionalLight position={[120, 60, -80]} intensity={0.35} />
            <Suspense fallback={null}>
              {/* Re-frame on every LOD change so each level shows the whole model. */}
              <Bounds key={`frame-${lod}`} fit clip margin={1.15}>
                <CameraRig preset={preset} nonce={presetNonce} focusRef={focusRef} roomRef={roomRef} />
                <RoomLens inside={!!roomView} />
                {roomView && (() => {
                  const [x, y, floorZ] = roomView.room.position_mm;
                  const [b0, b1, b2, b3] = roomView.room.bounds_mm ?? [x, y, x, y];
                  return <pointLight position={toThree([(b0 + b2) / 2, (b1 + b3) / 2, floorZ + 2500])}
                    intensity={18} distance={14} decay={2} color="#fff3e0" />;
                })()}
                <FacadeContext.Provider value={data.facade?.style ?? null}>
                <group onPointerMissed={() => setSelectedId(null)}>
                  {visible.map(element => isOpening(element) ? (
                    <Opening
                      key={element.element_id}
                      element={element}
                      lod={lod}
                      selected={element.element_id === selectedId}
                      onSelect={setSelectedId}
                      hovered={element.element_id === hoveredId}
                      onHover={setHoveredId}
                      inward={lod === '500' && colorBy === 'realistic' && element.is_external && data.facade?.style !== 'glass'
                        ? storeyCentre[element.storey.placement_id] : undefined}
                      hostColor={lod !== '500' ? CONCRETE
                        : colorBy === 'storey' ? storeyColor[element.storey.placement_id]
                        : colorBy === 'realistic' && data.facade?.style === 'glass' && element.is_external ? CURTAIN.spandrel
                        : colorBy === 'realistic'
                          ? (wallFinish(element.host_component ?? '',
                              element.is_external ?? /brick|exterior|external/i.test(element.host_component ?? '')) ?? REAL.brick)
                          : componentColor(element.host_component ?? 'wall')}
                    />
                  ) : (
                    <Wall
                      key={element.element_id}
                      element={element}
                      lod={lod}
                      selected={element.element_id === selectedId}
                      onSelect={setSelectedId}
                      hovered={element.element_id === hoveredId}
                      onHover={setHoveredId}
                      colorOverride={colorBy === 'storey' ? storeyColor[element.storey.placement_id] : undefined}
                      onFocus={point => focusRef.current?.(point)}
                      showLabel={false}
                      look={colorBy}
                      inward={lod === '500' && colorBy === 'realistic' && element.is_external
                        ? storeyCentre[element.storey.placement_id] : undefined}
                    />
                  ))}
                  {roomsVisible && !roomView && (data.rooms ?? [])
                    .filter(room => room.storey.placement_id === roomStorey)
                    .map((room, i) => (
                      <Html key={`room-${i}`} position={toThree([room.position_mm[0], room.position_mm[1], room.position_mm[2] + 1600])}
                        center style={{ pointerEvents: 'auto' }}>
                        <div onClick={() => enterRoom(room)} title="Walk into this room (360° view)" style={{
                          background: 'rgba(255,255,255,.92)', color: '#0f172a', fontSize: 11, padding: '3px 8px',
                          borderRadius: 6, whiteSpace: 'nowrap', boxShadow: '0 1px 4px rgba(0,0,0,.25)', textAlign: 'center',
                          cursor: 'pointer', userSelect: 'none',
                        }}>
                          <strong>{room.name}</strong>
                          {room.size_mm && <div style={{ fontSize: 10 }}>{(room.size_mm[0] / 1000).toFixed(1)} m × {(room.size_mm[1] / 1000).toFixed(1)} m</div>}
                        </div>
                      </Html>
                    ))}
                  {(data.slabs ?? [])
                    .filter(slab => Number(lod) >= slab.lod.minimum_lod)
                    .filter(slab => ![...hidden].some(pid => slab.element_id.startsWith(`slab:${pid}:`)))
                    .filter(slab => !(lod === '500' && !showRoof && (slab.role === 'roof' || slab.role === 'ceiling')))
                    .map(slab => <Slab key={slab.element_id} slab={slab} opacity={lod === '200' ? 0.8 : 0.4}
                      finish={lod === '500' && colorBy === 'realistic' ? slab.role ?? 'floor' : undefined} />)}
                  {lod === '500' && colorBy !== 'realistic' && levelMarkers.map(marker => (
                    <Html key={marker.key} position={marker.position} center style={{ pointerEvents: 'none' }}>
                      <div style={{
                        background: 'rgba(15,23,42,.8)', color: '#f8fafc', fontSize: 11,
                        padding: '2px 8px', borderRadius: 4, whiteSpace: 'nowrap',
                      }}>
                        {marker.label}
                      </div>
                    </Html>
                  ))}
                </group>
                </FacadeContext.Provider>
              </Bounds>
              {lod !== '500' && <gridHelper args={[300, 60, '#b6c2cf', '#dbe2ea']} position={[30, groundY, 40]} />}
              <ContactShadows position={[30, groundY + 0.01, 40]} scale={200} blur={2.5} far={40}
                opacity={lod === '500' ? 0.6 : 0.35} frames={1} resolution={1024} />
            </Suspense>
            <OrbitControls makeDefault enableDamping dampingFactor={0.1} enableZoom={!roomView} enablePan={!roomView}
              rotateSpeed={roomView ? 0.45 : 1} minPolarAngle={roomView ? Math.PI * 0.08 : 0}
              maxPolarAngle={roomView ? Math.PI * 0.92 : Math.PI * 0.495} />
          </Canvas>
        </div>
      )}

      {selected && (
        <div style={{ ...noteStyle, display: 'grid', gap: 6 }}>
          <strong style={{ color: 'var(--ink)' }}>
            {(selected.component ?? 'wall').replace(/^./, c => c.toUpperCase())} · {selected.drawing_title}
          </strong>
          <span>
            Length {mm(selected.dimensions_mm.length)} · Thickness {mm(selected.dimensions_mm.thickness)} ·
            Height {mm(selected.dimensions_mm.height)} · base {selected.storey.base_level_key}
          </span>
          {selected.review_flags?.map(flag => <span key={flag} style={{ color: REVIEW }}>{flag}</span>)}
          {selected.opening && (
            <span>
              {selected.element_type}{selected.opening.tag ? ` ${selected.opening.tag}` : ''}: {Math.round(selected.opening.width_mm)} mm wide,
              {' '}sill {Math.round(selected.opening.sill_mm)} mm, head {Math.round(selected.opening.head_mm)} mm
              {' '}({selected.opening.height_source === 'assumed_typical' ? 'heights assumed — not on the drawing' : selected.opening.height_source});
              {' '}symbol: {selected.opening.symbol_evidence.join(', ') || 'none (plain opening)'}
            </span>
          )}
          {selected.detected_by === 'legend_hatch' && (
            <span>Wall type from the sheet's hatch legend: {selected.wall_type}</span>
          )}
          {selected.joins && selected.joins.length > 0 && (
            <span>
              LOD 400 joins: {selected.joins.map(j => `${j.end} ${j.join} (+${Math.round(j.extended_mm)} mm)`).join('; ')}
            </span>
          )}
          {selected.height_verification && (
            <span>
              Confirmed by {selected.height_verification.reviewer ?? 'reviewer'} · verification{' '}
              {selected.height_verification.verification_id}
            </span>
          )}
          <span style={{ color: 'var(--muted)' }}>{selected.element_id}</span>
        </div>
      )}

      <details className="more">
        <summary>More options and model details</summary>
        <div className="more-body">
          {lod === '500' && (
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ color: 'var(--muted)', fontSize: 12 }}>Colour by</span>
                <button onClick={() => setColorBy('realistic')} style={lodButton(colorBy === 'realistic')}>Realistic</button>
                <button onClick={() => setColorBy('type')} style={lodButton(colorBy === 'type')}>Element type</button>
                <button onClick={() => setColorBy('storey')} style={lodButton(colorBy === 'storey')}>Storey</button>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', fontSize: 12 }}>
                {legend.map(item => (
                  <span key={item.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 12, height: 12, borderRadius: 3, background: item.color, display: 'inline-block' }} />
                    {item.label} ({item.count})
                  </span>
                ))}
              </div>
            </div>
          )}
          <div style={noteStyle}>{data.lod_notes?.[lod] ?? LOD_NOTES[lod]}</div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18 }}>
            {data.source && data.source.type === 'structure' ? (
              <span style={{ color: data.source.files.length ? 'var(--ok)' : 'var(--warn)' }}>
                {(data.source.structure ?? 'structure').replace(/^./, c => c.toUpperCase())}
                {data.source.files.length
                  ? ` built from the dimensions printed on: ${data.source.files.join(', ')}`
                  : ' project: no drawings uploaded yet'}
              </span>
            ) : data.source && (
              <span style={{ color: 'var(--ok)' }}>Built from the {data.source.type.toUpperCase()} model: {data.source.files.join(', ')}</span>
            )}
            {data.source?.type === 'structure' ? (
              <span>Elements: {data.elements.length}</span>
            ) : (
              <>
                <span>Placed plans: {data.summary.placed_plans}</span>
                <span>Storeys in model: {data.summary.plans_in_model}</span>
                <span>Walls: {countFor(null, false)}</span>
                <span>Columns: {countFor(null, true)}</span>
                <span>Approximate slabs: {(data.slabs ?? []).length}</span>
              </>
            )}
            {(data.summary.doors ?? 0) + (data.summary.windows ?? 0) > 0 && (
              <span>Doors: {data.summary.doors ?? 0} · Windows: {data.summary.windows ?? 0}</span>
            )}
            {!!data.summary.rooms && <span>Rooms: {data.summary.rooms}</span>}
            {!!data.summary.walls_joined && <span>Joined at LOD 400: {data.summary.walls_joined}</span>}
            {!!data.summary.columns_to_review && (
              <span style={{ color: REVIEW }} title="Isolated columns: no other column or wall within 10 m on their storey. Shown in amber at LOD 500; check them in section 05.">
                Columns to review: {data.summary.columns_to_review}
              </span>
            )}
            {!!data.summary.walls_superseded && (
              <span title="Line-pair walls lying on a confirmed hatch wall are replaced by it.">
                Superseded: {data.summary.walls_superseded}
              </span>
            )}
            {data.source?.type !== 'structure' && (
              <span style={{ color: data.summary.walls_withheld ? 'var(--warn)' : undefined }}>
                Withheld: {data.summary.walls_withheld}
              </span>
            )}
          </div>
          {!!data.parameters?.length && (
            <details style={{ fontSize: 12 }}>
              <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
                Values read from the drawings ({data.parameters.filter(p => !p.assumed).length})
                {data.parameters.some(p => p.assumed) && ` · ${data.parameters.filter(p => p.assumed).length} display value(s) where the drawings are silent`}
              </summary>
              <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
                <tbody>
                  {data.parameters.map((p, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--line)' }}>
                      <td style={{ padding: '3px 8px', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{p.name}</td>
                      <td style={{ padding: '3px 8px', color: p.assumed ? 'var(--warn)' : 'var(--ink)', whiteSpace: 'nowrap' }}>{String(p.value)}</td>
                      <td style={{ padding: '3px 8px', color: 'var(--muted)' }}>{p.assumed ? `assumed — ${p.assumed}` : `“${p.text}”`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {data.source?.type !== 'structure' && (
            <div>
              <button className="primary" disabled={generating || data.summary.placed_plans === 0} onClick={generateAll}>
                {generating ? 'Generating…' : 'Generate model for all placed plans'}
              </button>
            </div>
          )}
          {runReport.length > 0 && (
            <ul style={{ margin: 0, fontSize: 12 }}>{runReport.map((line, i) => <li key={i}>{line}</li>)}</ul>
          )}
          <small style={{ color: 'var(--muted)' }}>
            Building metres from each plan's shared reference point; heights from the annotated levels you chose.
            Plans are aligned by translation only (same orientation assumed). Click a wall or column to inspect.
          </small>

          {data.storeys.length > 0 && (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Show', 'Plan', 'Base level', 'Storey height', 'Reference point', 'Walls', 'Columns', 'Withheld'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '6px 8px', color: 'var(--muted)', borderBottom: '1px solid var(--line)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.storeys.map(storey => (
                  <tr key={storey.placement_id}>
                    <td style={cell}>
                      <input type="checkbox" checked={!hidden.has(storey.placement_id)}
                        onChange={() => toggleStorey(storey.placement_id)} aria-label={`Show ${storey.drawing_title}`} />
                    </td>
                    <td style={cell}>
                      <span style={{
                        display: 'inline-block', width: 10, height: 10, borderRadius: 2, marginRight: 6,
                        background: storeyColor[storey.placement_id],
                      }} />
                      {storey.drawing_title}
                    </td>
                    <td style={cell}>{storey.base_level_key} ({elevation(storey.base_elevation_mm)})</td>
                    <td style={cell}>{storey.storey_height_mm ? mm(storey.storey_height_mm) : '—'}</td>
                    <td style={cell}>{storey.reference_label}</td>
                    <td style={cell}>{countFor(storey.placement_id, false)}</td>
                    <td style={cell}>{countFor(storey.placement_id, true)}</td>
                    <td style={cell}>{storey.walls_withheld}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {data.excluded_plans.length > 0 && (
            <details open>
              <summary>{data.excluded_plans.length} plan(s) not in the building yet</summary>
              <ul>
                {data.excluded_plans.map((item, index) => (
                  <li key={index}>{item.drawing_title ?? item.drawing_id}: {item.reason}</li>
                ))}
              </ul>
            </details>
          )}
          {data.withheld_elements.length > 0 && (
            <details>
              <summary>{data.withheld_elements.length} wall(s) withheld by the height gate</summary>
              <ul>
                {data.withheld_elements.map((item, index) => (
                  <li key={index}>{item.element_id ?? 'unknown'}: {item.reasons.join(' ')}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </details>
    </div>
  );
}

const cell: React.CSSProperties = { padding: '6px 8px', borderBottom: '1px solid var(--line)' };

export default function ModelViewer3D({
  projectId,
  drawingId,
  drawings = [],
}: {
  projectId: string;
  drawingId?: string;
  drawings?: { id: string; filename: string }[];
}) {
  const [scope, setScope] = useState<'drawing' | 'building'>('building');
  const [picked, setPicked] = useState(drawingId || drawings[0]?.id || '');
  useEffect(() => { if (drawingId) setPicked(drawingId); }, [drawingId]);
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>Show</span>
        <button onClick={() => setScope('building')} style={lodButton(scope === 'building')}>
          Whole building
        </button>
        <button onClick={() => setScope('drawing')} style={lodButton(scope === 'drawing')} disabled={!drawings.length && !drawingId}>
          One drawing
        </button>
        {scope === 'drawing' && drawings.length > 0 && (
          <select value={picked} onChange={e => setPicked(e.target.value)} style={{ maxWidth: 420 }}>
            {drawings.map(d => <option key={d.id} value={d.id}>{d.filename}</option>)}
          </select>
        )}
      </div>
      {scope === 'drawing' && picked
        ? <DrawingModelView key={picked} projectId={projectId} drawingId={picked} />
        : <BuildingView projectId={projectId} />}
    </div>
  );
}
