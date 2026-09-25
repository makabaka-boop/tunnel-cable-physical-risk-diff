import type { FieldErrors, PrecheckPayload, ReroutePayload } from "../types";

/** 表单中的原始字段都以字符串保存，提交时统一解析、校验。 */
export interface NodeDraft {
  x: string;
  y: string;
}
export interface CircleDraft {
  x: string;
  y: string;
  radius: string;
}
/** 一对现场控制点：survey（全站仪坐标）↔ path（施工局部坐标）。 */
export interface CalibrationPairDraft {
  surveyX: string;
  surveyY: string;
  pathX: string;
  pathY: string;
}
/** 一次性改线：替换节点区间 + 接入两端的替代折点（含两端点）。 */
export interface RerouteDraft {
  enabled: boolean;
  startIndex: string;
  endIndex: string;
  /** 替代折点；首尾两个固定为被替换区间的边界节点（由 App 同步）。 */
  points: NodeDraft[];
}
export interface FormDraft {
  cableRadius: string;
  nodes: NodeDraft[];
  circles: CircleDraft[];
  calibrationEnabled: boolean;
  maxRmsError: string;
  calibrationPairs: CalibrationPairDraft[];
  reroute: RerouteDraft;
}

function parseFiniteInt(raw: string): number {
  const t = raw.trim();
  if (!/^[+-]?\d+$/.test(t)) {
    // 拒绝空串、小数、NaN、Infinity、字母等
    throw new Error("必须是整数毫米");
  }
  const n = Number(t);
  if (!Number.isSafeInteger(n)) throw new Error("整数超出安全范围");
  return n;
}

function parsePositiveFinite(raw: string): number {
  const t = raw.trim();
  if (t === "") throw new Error("不能为空");
  const n = Number(t);
  if (!Number.isFinite(n)) throw new Error("必须是有限数值");
  if (n <= 0) throw new Error("必须为正数");
  return n;
}

function parseNonNegInt(raw: string): number {
  const t = raw.trim();
  const n = parseFiniteInt(t);
  if (n < 0) throw new Error("不能为负数");
  return n;
}

function parseFiniteNumber(raw: string): number {
  const t = raw.trim();
  if (t === "") throw new Error("不能为空");
  const n = Number(t);
  if (!Number.isFinite(n)) throw new Error("必须是有限数值（不能是 NaN 或无穷）");
  return n;
}

/** 与后端一致的字段级校验；返回 errors 映射（空对象表示通过）。 */
export function validateDraft(draft: FormDraft): FieldErrors {
  const errors: FieldErrors = {};

  try {
    parsePositiveFinite(draft.cableRadius);
  } catch (e) {
    errors.cable_radius = `电缆半径${(e as Error).message}`;
  }

  if (draft.nodes.length < 2) {
    errors.nodes = "路径至少需要两个节点";
  }
  const parsedNodes: ParsedNode[] = draft.nodes.map((node) => {
    let x: number | null = null;
    let y: number | null = null;
    try {
      x = parseFiniteInt(node.x);
    } catch {
      x = null; // 错误在下方逐字段记录
    }
    try {
      y = parseFiniteInt(node.y);
    } catch {
      y = null;
    }
    return x === null || y === null ? null : { x, y };
  });
  draft.nodes.forEach((node, i) => {
    try {
      parseFiniteInt(node.x);
    } catch (e) {
      errors[`nodes[${i}].x`] = `X ${(e as Error).message}`;
    }
    try {
      parseFiniteInt(node.y);
    } catch (e) {
      errors[`nodes[${i}].y`] = `Y ${(e as Error).message}`;
    }
  });

  // 相邻重复节点
  for (let i = 0; i < draft.nodes.length - 1; i++) {
    const a = draft.nodes[i];
    const b = draft.nodes[i + 1];
    if (
      a.x.trim() !== "" &&
      a.y.trim() !== "" &&
      b.x.trim() !== "" &&
      b.y.trim() !== "" &&
      a.x.trim() === b.x.trim() &&
      a.y.trim() === b.y.trim()
    ) {
      errors[`nodes[${i + 1}].x`] = "与上一节点重合，禁止相邻重复节点";
    }
  }

  draft.circles.forEach((c, i) => {
    try {
      parseFiniteInt(c.x);
    } catch (e) {
      errors[`circles[${i}].x`] = `圆心 X ${(e as Error).message}`;
    }
    try {
      parseFiniteInt(c.y);
    } catch (e) {
      errors[`circles[${i}].y`] = `圆心 Y ${(e as Error).message}`;
    }
    try {
      parsePositiveFinite(c.radius);
    } catch (e) {
      errors[`circles[${i}].radius`] = `半径${(e as Error).message}`;
    }
  });

  // 可选现场标定：字段键与后端一致（calibration.*）；未启用时不校验不上送。
  if (draft.calibrationEnabled) {
    const pairs = draft.calibrationPairs;
    if (pairs.length < 2 || pairs.length > 20) {
      errors["calibration"] = `控制点对需要 2～20 对（当前 ${pairs.length} 对）`;
    }
    try {
      parsePositiveFinite(draft.maxRmsError);
    } catch (e) {
      errors["calibration.max_rms_error"] = `残差阈值${(e as Error).message}`;
    }
    const surveyVals: Array<{ x: number; y: number }> = [];
    const pathVals: Array<{ x: number; y: number }> = [];
    pairs.forEach((p, i) => {
      const read = (raw: string, key: string, label: string) => {
        try {
          return parseFiniteNumber(raw);
        } catch (e) {
          errors[key] = `${label} ${(e as Error).message}`;
          return null;
        }
      };
      const sx = read(p.surveyX, `calibration.survey_points[${i}].x`, "全站仪 X");
      const sy = read(p.surveyY, `calibration.survey_points[${i}].y`, "全站仪 Y");
      const px = read(p.pathX, `calibration.path_points[${i}].x`, "施工 X");
      const py = read(p.pathY, `calibration.path_points[${i}].y`, "施工 Y");
      if (sx !== null && sy !== null) surveyVals.push({ x: sx, y: sy });
      if (px !== null && py !== null) pathVals.push({ x: px, y: py });
    });
    // 退化构型：任一组全部重合都无法确定刚体变换（与后端一致）
    const allCoincident = (pts: Array<{ x: number; y: number }>) =>
      pts.length > 0 && pts.every((p) => p.x === pts[0].x && p.y === pts[0].y);
    if (surveyVals.length === pairs.length && allCoincident(surveyVals)) {
      errors["calibration.survey_points"] =
        "survey_points 全部重合，无法确定刚体变换";
    }
    if (pathVals.length === pairs.length && allCoincident(pathVals)) {
      errors["calibration.path_points"] =
        "path_points 全部重合，无法确定刚体变换";
    }
  }

  // 可选一次性改线：字段键与后端一致（reroute.*）；未启用时不校验不上送。
  if (draft.reroute.enabled) {
    validateRerouteDraft(draft, parsedNodes, errors);
  }

  return errors;
}

/** 已解析的路径节点（主校验通过时可用）；解析失败给 null。 */
type ParsedNode = { x: number; y: number } | null;

function validateRerouteDraft(
  draft: FormDraft,
  parsedNodes: ParsedNode[],
  errors: FieldErrors,
): void {
  const rr = draft.reroute;
  const nodeCount = draft.nodes.length;

  let start = -1;
  let end = -1;
  try {
    start = parseNonNegInt(rr.startIndex);
    if (start >= nodeCount) throw new Error("");
  } catch {
    errors["reroute.start_index"] = `起点下标必须是 [0, ${nodeCount - 1}] 内的整数`;
  }
  try {
    end = parseNonNegInt(rr.endIndex);
    if (end >= nodeCount) throw new Error("");
  } catch {
    errors["reroute.end_index"] = `终点下标必须是 [0, ${nodeCount - 1}] 内的整数`;
  }
  if (start >= 0 && end >= 0 && start >= end) {
    errors["reroute.start_index"] = "起点下标必须严格小于终点下标（至少替换一条线段）";
  }

  // 替代折点（整数毫米，至少 2 个，内部不允许相邻重合）
  const pts: ParsedNode[] = rr.points.map((p, i) => {
    let x: number | null = null;
    let y: number | null = null;
    try {
      x = parseFiniteInt(p.x);
    } catch (e) {
      errors[`reroute.replacement_points[${i}].x`] = `X ${(e as Error).message}`;
    }
    try {
      y = parseFiniteInt(p.y);
    } catch (e) {
      errors[`reroute.replacement_points[${i}].y`] = `Y ${(e as Error).message}`;
    }
    return x === null || y === null ? null : { x, y };
  });
  if (pts.length < 2) {
    errors["reroute.replacement_points"] = "替代折点至少包含区间两端两个点";
  }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    if (a && b && a.x === b.x && a.y === b.y) {
      errors[`reroute.replacement_points[${i + 1}].x`] =
        "与上一折点重合，禁止相邻重复节点";
    }
  }

  // 端点必须与被替换边界节点精确重合（整数毫米，直接比解析值，不容差）
  if (start >= 0 && end >= 0 && start < end && pts.length >= 2) {
    const a = parsedNodes[start];
    const p0 = pts[0];
    if (a && p0 && (a.x !== p0.x || a.y !== p0.y)) {
      errors["reroute.replacement_points[0].x"] =
        `首个接入折点必须与节点 #${start}（${a.x}, ${a.y}）精确重合`;
    }
    const b = parsedNodes[end];
    const pLast = pts[pts.length - 1];
    if (b && pLast && (b.x !== pLast.x || b.y !== pLast.y)) {
      errors[`reroute.replacement_points[${pts.length - 1}].x`] =
        `末个接入折点必须与节点 #${end}（${b.x}, ${b.y}）精确重合`;
    }
  }
}

/** 解析为后端载荷；调用前应已通过 validateDraft。 */
export function buildPayload(draft: FormDraft): PrecheckPayload {
  const payload: PrecheckPayload = {
    cable_radius: parsePositiveFinite(draft.cableRadius),
    nodes: draft.nodes.map((n) => ({
      x: parseFiniteInt(n.x),
      y: parseFiniteInt(n.y),
    })),
    circles: draft.circles.map((c) => ({
      x: parseFiniteInt(c.x),
      y: parseFiniteInt(c.y),
      radius: parsePositiveFinite(c.radius),
    })),
  };
  // 未启用标定时完全省略 calibration 键，请求与旧版逐项一致。
  if (draft.calibrationEnabled) {
    payload.calibration = {
      survey_points: draft.calibrationPairs.map((p) => ({
        x: parseFiniteNumber(p.surveyX),
        y: parseFiniteNumber(p.surveyY),
      })),
      path_points: draft.calibrationPairs.map((p) => ({
        x: parseFiniteNumber(p.pathX),
        y: parseFiniteNumber(p.pathY),
      })),
      max_rms_error: parsePositiveFinite(draft.maxRmsError),
    };
  }
  // 未启用改线预览时完全省略 reroute 键，请求与旧版逐项一致。
  if (draft.reroute.enabled) {
    payload.reroute = buildReroutePayload(draft);
  }
  return payload;
}

/** 解析改线段（调用前应已通过 validateDraft）。 */
export function buildReroutePayload(draft: FormDraft): ReroutePayload {
  return {
    start_index: parseNonNegInt(draft.reroute.startIndex),
    end_index: parseNonNegInt(draft.reroute.endIndex),
    replacement_points: draft.reroute.points.map((p) => ({
      x: parseFiniteInt(p.x),
      y: parseFiniteInt(p.y),
    })),
  };
}
