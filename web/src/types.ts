export interface Point {
  x: number;
  y: number;
}

export interface Collision {
  segment_index: number;
  circle_index: number;
  nearest: Point;
  distance: number;
  expanded_radius: number;
  circle_center: Point;
  circle_radius: number;
  cable_radius: number;
}

export interface CircleView {
  center: Point;
  radius: number;
  expanded_radius: number;
}

export interface IntervalPiece {
  segment_index: number;
  circle_index: number;
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
}

export interface IntrusionInterval {
  circle_index: number;
  entry_segment_index: number;
  exit_segment_index: number;
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
  pieces: IntervalPiece[];
}

export interface CompoundPiece {
  segment_index: number;
  circle_indices: number[];
  entry: Point;
  exit: Point;
  start_mileage: number;
  end_mileage: number;
  length: number;
}

export interface CompoundIntrusionSegment {
  circle_indices: number[];
  start_mileage: number;
  end_mileage: number;
  start: Point;
  end: Point;
  start_inclusive: boolean;
  end_inclusive: boolean;
  length: number;
  pieces: CompoundPiece[];
}

export interface CalibrationResult {
  point_count: number;
  rotation: number[][]; // 2x2 行主序真旋转矩阵（det = +1）
  translation: Point;
  rms_error: number;
}

export interface CalibrationPayload {
  survey_points: Point[];
  path_points: Point[];
  max_rms_error: number;
}

// ---------- 一次性改线预览 ----------

export interface ReroutePayload {
  start_index: number;
  end_index: number;
  replacement_points: Point[];
}

export interface RiskEvent {
  segment_index: number;
  circle_index: number;
  nearest: Point;
  distance: number;
  expanded_radius: number;
  mileage: number;
}

export interface PersistedRisk {
  segment_index: number;
  circle_index: number;
  nearest: Point;
  distance: number;
  expanded_radius: number;
  original_mileage: number;
  candidate_mileage: number;
}

export interface CircleRisk {
  circle_index: number;
  eliminated: RiskEvent[];
  added: RiskEvent[];
  remaining: PersistedRisk[];
}

export interface CandidateRoute extends PrecheckResponseBase {
  // 候选线不携带 calibration/reroute_preview（二者是整次请求级字段）
}

export interface ReroutePreview {
  range: {
    start_index: number;
    end_index: number;
    replacement_point_count: number;
  };
  candidate: CandidateRoute;
  circle_risks: CircleRisk[];
  eliminated_count: number;
  added_count: number;
  remaining_count: number;
}

interface PrecheckResponseBase {
  feasible: boolean;
  cable_radius: number;
  nodes: Point[];
  circles: CircleView[];
  collision_count: number;
  first_collision: Collision | null;
  collisions: Collision[];
  intrusion_intervals: IntrusionInterval[];
  compound_intrusion_segments: CompoundIntrusionSegment[];
}

export interface PrecheckResponse extends PrecheckResponseBase {
  // 请求带 calibration 时给出标定摘要；省略时为 null/缺省（逐项兼容）
  calibration?: CalibrationResult | null;
  // 请求带 reroute 时给出一次性改线预览（原线结论仍在顶层）
  reroute_preview?: ReroutePreview | null;
}

export interface PrecheckPayload {
  nodes: Point[];
  cable_radius: number;
  circles: Array<Point & { radius: number }>;
  calibration?: CalibrationPayload;
  reroute?: ReroutePayload;
}

export type FieldErrors = Record<string, string>;
