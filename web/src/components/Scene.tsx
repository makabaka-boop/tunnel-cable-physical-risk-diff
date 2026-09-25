import type { CompoundIntrusionSegment, PrecheckResponse } from "../types";

interface Props {
  result: PrecheckResponse;
}

// 复合侵入段（同时落入 ≥2 个扩张圈）统一醒目标记：红色虚线 + 空心圆环，
// 与单圈侵入的彩色实线区分。
const COMPOUND_COLOR = "#ef4444";

// 每个禁入圈一个高亮色（循环取色，与后端 circle_index 一致）。
const HIGHLIGHT_COLORS = [
  "#fbbf24",
  "#fb923c",
  "#f472b6",
  "#a78bfa",
  "#2dd4bf",
  "#facc15",
  "#4ade80",
  "#38bdf8",
];

export function highlightColor(circleIndex: number): string {
  return HIGHLIGHT_COLORS[circleIndex % HIGHLIGHT_COLORS.length];
}

const W = 880;
const H = 560;
const PAD = 40;

interface View {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  k: number; // mm -> px
}

function computeView(result: PrecheckResponse): View {
  const xs: number[] = [];
  const ys: number[] = [];
  result.nodes.forEach((p) => {
    xs.push(p.x);
    ys.push(p.y);
  });
  result.circles.forEach((c) => {
    // 视窗必须容纳扩张安全圈；只按孔本身取边界会把电缆安全边界裁到画布外。
    xs.push(c.center.x - c.expanded_radius, c.center.x + c.expanded_radius);
    ys.push(c.center.y - c.expanded_radius, c.center.y + c.expanded_radius);
  });

  let minX = Math.min(...xs);
  let maxX = Math.max(...xs);
  let minY = Math.min(...ys);
  let maxY = Math.max(...ys);
  if (![minX, maxX, minY, maxY].every(Number.isFinite)) {
    minX = 0;
    maxX = 1;
    minY = 0;
    maxY = 1;
  }
  // 防止零宽/零高
  if (maxX - minX < 1) {
    minX -= 0.5;
    maxX += 0.5;
  }
  if (maxY - minY < 1) {
    minY -= 0.5;
    maxY += 0.5;
  }

  const k = Math.min((W - 2 * PAD) / (maxX - minX), (H - 2 * PAD) / (maxY - minY));
  // 按几何内容居中
  return { minX, minY, maxX, maxY, k };
}

function Marker({
  cx,
  cy,
  r,
  fill,
  stroke,
  label,
  testid,
}: {
  cx: number;
  cy: number;
  r: number;
  fill: string;
  stroke: string;
  label?: string;
  testid?: string;
}) {
  return (
    <g>
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={fill}
        stroke={stroke}
        strokeWidth={2}
        data-testid={testid}
      />
      {label !== undefined && (
        <text x={cx + r + 4} y={cy - r - 4} className="svg-label">
          {label}
        </text>
      )}
    </g>
  );
}

export function Scene({ result }: Props) {
  const view = computeView(result);

  // mm 坐标 -> svg 像素（y 翻转），保持纵横比一致
  const sx = (x: number) => PAD + (x - view.minX) * view.k;
  const sy = (y: number) => H - PAD - (y - view.minY) * view.k;
  const sr = (r: number) => r * view.k;

  const pathD = result.nodes
    .map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x).toFixed(2)} ${sy(p.y).toFixed(2)}`)
    .join(" ");

  // 侵入区间折线片段：与详情列表共用 result.intrusion_intervals 同一数组，
  // 保证“图上高亮”与“文案区间”严格同源。零长相切片段退化为端点圆点。
  const intervalFragments: Array<{
    key: string;
    d: string;
    color: string;
    zero: boolean;
    cx: number;
    cy: number;
    testid: string;
  }> = [];
  result.intrusion_intervals.forEach((iv, order) => {
    const color = highlightColor(iv.circle_index);
    iv.pieces.forEach((piece) => {
      const zero =
        piece.entry.x === piece.exit.x && piece.entry.y === piece.exit.y && piece.length === 0;
      const d =
        `M ${sx(piece.entry.x).toFixed(2)} ${sy(piece.entry.y).toFixed(2)} ` +
        `L ${sx(piece.exit.x).toFixed(2)} ${sy(piece.exit.y).toFixed(2)}`;
      intervalFragments.push({
        key: `iv-${order}-c${iv.circle_index}-s${piece.segment_index}`,
        d,
        color,
        zero,
        cx: sx(piece.entry.x),
        cy: sy(piece.entry.y),
        testid: `intrusion-c${iv.circle_index}-s${piece.segment_index}`,
      });
    });
  });

  // 复合侵入段片段：与复合段详情列表共用
  // result.compound_intrusion_segments 同一数组，逐原线段绘制；
  // 零长点（双圈/多圈相切）画空心圆环，跨拐点分段在节点处由
  // pieces 自动衔接。
  const compoundFragments: Array<{
    key: string;
    d: string;
    zero: boolean;
    cx: number;
    cy: number;
    testid: string;
    label: string;
  }> = [];
  const compoundKey = (seg: CompoundIntrusionSegment) => seg.circle_indices.join("-");
  result.compound_intrusion_segments.forEach((seg, order) => {
    const ckey = compoundKey(seg);
    seg.pieces.forEach((piece) => {
      const zero =
        piece.entry.x === piece.exit.x && piece.entry.y === piece.exit.y && piece.length === 0;
      const d =
        `M ${sx(piece.entry.x).toFixed(2)} ${sy(piece.entry.y).toFixed(2)} ` +
        `L ${sx(piece.exit.x).toFixed(2)} ${sy(piece.exit.y).toFixed(2)}`;
      compoundFragments.push({
        key: `cp-${order}-c${ckey}-s${piece.segment_index}`,
        d,
        zero,
        cx: sx(piece.entry.x),
        cy: sy(piece.entry.y),
        testid: `compound-c${ckey}-s${piece.segment_index}`,
        label: `圈 ${seg.circle_indices.join("/")}`,
      });
    });
  });

  // 图形高亮必须与接口的首个碰撞及明细顺序保持一致。
  const first = result.first_collision;
  const firstKey = first
    ? `${first.segment_index}-${first.circle_index}`
    : null;

  const cablePx = Math.max(2, sr(result.cable_radius));

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="scene"
      role="img"
      aria-label="电缆路径与禁入圈判定图"
      data-testid="scene"
    >
      <rect x={0} y={0} width={W} height={H} rx={12} className="scene-bg" />

      {/* 禁入圈：先扩张安全圈（虚线），再实体孔圈 */}
      {result.circles.map((c, i) => (
        <g key={`circle-${i}`}>
          <circle
            cx={sx(c.center.x)}
            cy={sy(c.center.y)}
            r={sr(c.expanded_radius)}
            className="expanded-circle"
            data-testid={`expanded-circle-${i}`}
          />
          <circle
            cx={sx(c.center.x)}
            cy={sy(c.center.y)}
            r={sr(c.radius)}
            className="forbidden-circle"
            data-testid={`forbidden-circle-${i}`}
          />
          <text x={sx(c.center.x)} y={sy(c.center.y) - sr(c.radius) - 6} className="svg-label circle-label">
            孔 #{i}
          </text>
        </g>
      ))}

      {/* 电缆折线路径（线宽体现电缆半径） */}
      <path d={pathD} className="cable-path" strokeWidth={cablePx} fill="none" />
      <path d={pathD} className="cable-axis" strokeWidth={1} fill="none" />

      {/* 连续侵入区间高亮：按 interval 同数组逐片段绘制折线，
          颜色区分禁入圈；零长相切点画为小圆点。 */}
      <g data-testid="intrusion-layer">
        {intervalFragments.map((f) =>
          f.zero ? (
            <circle
              key={f.key}
              cx={f.cx}
              cy={f.cy}
              r={4.5}
              fill="none"
              stroke={f.color}
              strokeWidth={2.5}
              className="intrusion-point"
              data-testid={f.testid}
            />
          ) : (
            <path
              key={f.key}
              d={f.d}
              className="intrusion-segment"
              stroke={f.color}
              fill="none"
              data-testid={f.testid}
            />
          ),
        )}
      </g>

      {/* 复合侵入段高亮：与详情共用 compound_intrusion_segments 同一数组，
          逐原线段绘制；零长多圈相切点画为醒目的空心圆环。 */}
      <g data-testid="compound-layer">
        {compoundFragments.map((f) =>
          f.zero ? (
            <circle
              key={f.key}
              cx={f.cx}
              cy={f.cy}
              r={7}
              fill="none"
              stroke={COMPOUND_COLOR}
              strokeWidth={3}
              className="compound-point"
              data-testid={f.testid}
              aria-label={f.label}
            >
              <title>{f.label}</title>
            </circle>
          ) : (
            <path
              key={f.key}
              d={f.d}
              className="compound-segment"
              stroke={COMPOUND_COLOR}
              strokeDasharray="6 4"
              fill="none"
              data-testid={f.testid}
            >
              <title>{f.label}</title>
            </path>
          ),
        )}
      </g>

      {/* 路径节点 */}
      {result.nodes.map((p, i) => (
        <Marker
          key={`node-${i}`}
          cx={sx(p.x)}
          cy={sy(p.y)}
          r={4}
          fill="#7dd3fc"
          stroke="#0369a1"
          label={`N${i}`}
          testid={`node-${i}`}
        />
      ))}

      {/* 判定位置（最近点）：首个突出，其余列出 */}
      {result.collisions.map((c) => {
        const key = `${c.segment_index}-${c.circle_index}`;
        const isFirst = key === firstKey;
        const cx = sx(c.nearest.x);
        const cy = sy(c.nearest.y);
        const size = isFirst ? 9 : 5;
        return (
          <g key={`hit-${key}`} data-testid={`collision-${key}`}>
            {/* 圆心 -> 判定位置 的连线，直观展示“距离” */}
            <line
              x1={sx(c.circle_center.x)}
              y1={sy(c.circle_center.y)}
              x2={cx}
              y2={cy}
              className="distance-line"
            />
            {isFirst ? (
              <polygon
                points={`${cx},${cy - size - 2} ${cx + size},${cy + size - 2} ${cx - size},${cy + size - 2}`}
                className="first-hit"
                data-testid="first-collision-marker"
              />
            ) : (
              <circle cx={cx} cy={cy} r={size} className="other-hit" />
            )}
            <text x={cx + size + 3} y={cy - size - 3} className="svg-label hit-label">
              线段{c.segment_index}/孔{c.circle_index}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
