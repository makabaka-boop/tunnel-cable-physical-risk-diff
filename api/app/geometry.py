"""二维碰撞几何计算（双精度）。

判定规则：
对每条闭线段（含端点），求圆心到该线段的**唯一最近点**及其距离；
当双精度 ``distance <= 禁入圈半径 + 电缆半径`` 时判定碰撞，
最近点统一作为判定位置。相切（恰好相等）亦判碰撞。

除逐线段碰撞外，本模块还给出可施工定位的**连续侵入区间**：

1. 对每条闭线段与扩张闭圆盘求一维闭交集 ``[t0, t1] ⊆ [0,1]``（二次方程精确
   求交，端点在圆内时把端点吸附到 0/1，相切退化为 t0==t1 的零长点，整段在
   圈内时为 [0,1]）；
2. 映射为累计里程区间；仅当相邻线段属于**同一**禁入圈、且前段以 t1==1
   确实到达公共拐点、后段以 t0==0 从该点继续时，才跨拐点合并；
3. 全部交点、里程与合并判断使用未舍入双精度，三位小数只用于接口展示。

此外给出**复合侵入段**（``compound_intrusion_segments``）：沿累计里程同时
落入至少两个扩张圈的最大分段。在各圈**舍入前的精确闭区间**端点事件上做一次
扫描，逐事件区分左邻域/点态/右邻域活动集合（位掩码），活动圈集合相同的连续
“点单元格/开区间单元格”合并为最大分段——
A=[0,5]、B=[5,10] 在 5 保留双圈零长点；A=[0,10]、B=[2,8]、C=[8,12] 在 8
产生独立三圈点且不计入左右两圈段闭端点。时间 O(P log P + R)、
空间 O(P + A + R)。

长路径性能：候选对先经 x 扫描线 + y 包围盒粗筛（不漏候选，比较带与坐标
尺度匹配的容差以保住数值相切），只对粗筛命中的组合做精确求交。
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


@dataclass(frozen=True)
class Collision:
    segment_index: int
    circle_index: int
    nearest: Point       # 判定位置：圆心到线段的最近点
    distance: float      # 圆心到最近点的距离（双精度）
    expanded_radius: float  # 禁入圈半径 + 电缆半径


@dataclass(frozen=True)
class SegmentPiece:
    """单条线段与某扩张圆盘的闭交集（未舍入双精度）。"""

    segment_index: int
    circle_index: int
    t0: float                # 进入参数 ∈ [0,1]
    t1: float                # 离开参数 ∈ [0,1]（t0==t1 即相切零长点）
    entry_point: Point       # a + t0*(b-a)
    exit_point: Point        # a + t1*(b-a)
    start_mileage: float     # 进入点累计里程
    end_mileage: float       # 离开点累计里程

    @property
    def length(self) -> float:
        return self.end_mileage - self.start_mileage


@dataclass(frozen=True)
class IntrusionInterval:
    """跨拐点合并后的连续侵入区间（未舍入双精度）。"""

    circle_index: int
    entry_segment_index: int
    exit_segment_index: int
    entry_point: Point
    exit_point: Point
    start_mileage: float
    end_mileage: float
    length: float
    pieces: Tuple[SegmentPiece, ...]


@dataclass(frozen=True)
class CompoundPiece:
    """复合侵入段在**一条原线段**上的部分（未舍入双精度）。

    与普通区间片段相同，pieces 按原线段切分，使详情边界与 SVG 逐段一致；
    零长点落在拐点时，相邻两条原线段各得一个同坐标零长片段。
    """

    segment_index: int
    circle_indices: Tuple[int, ...]  # 该片段覆盖到的全部圈（升序）
    entry_point: Point
    exit_point: Point
    start_mileage: float
    end_mileage: float
    length: float


@dataclass(frozen=True)
class CompoundIntrusionSegment:
    """沿累计里程**同时落入至少两个扩张圈**的最大分段（未舍入双精度）。

    里程轴被各圈精确闭区间的端点切成“点态单元格”（单个事件里程）与
    “开区间单元格”（相邻事件里程之间）；活动集合（在该单元格中逐点
    成立的圈集）基数 ≥ 2 且**完全相同**的连续单元格合并为一个最大分段，
    故相邻两圈段闭端点处的独立三圈点不会被并入任一侧。

    ``start_inclusive``/``end_inclusive`` 表示分段是否包含自身端点里程：
    端点是事件点时按“该里程上 ≥2 圈闭集成立”判定；开区间起点
    （端点处不足两圈）为 False。
    """

    circle_indices: Tuple[int, ...]  # 恒为该分段的活动圈集（升序）
    start_mileage: float
    end_mileage: float
    start_point: Point
    end_point: Point
    start_inclusive: bool
    end_inclusive: bool
    length: float
    pieces: Tuple[CompoundPiece, ...]


def cumulative_mileage(nodes: Sequence[Point]) -> List[float]:
    """各节点的累计里程（从 0 起按段长顺序累加）。

    原线与改线候选线必须共用同一段长度累加逻辑：候选线未改动前缀沿用
    原里程、改线后缀按新路径长度重新累计，里程比较一律以此未舍入结果
    为准，三位小数只用于展示。
    """
    n = len(nodes) - 1
    cum = [0.0] * (n + 1)
    for i in range(n):
        cum[i + 1] = cum[i] + math.hypot(
            nodes[i + 1][0] - nodes[i][0],
            nodes[i + 1][1] - nodes[i][1],
        )
    return cum


def nearest_point_on_segment(p: Point, a: Point, b: Point) -> Tuple[Point, float]:
    """返回点 ``p`` 到闭线段 ``a-b`` 的唯一最近点与距离。

    参数 t 为最近点在线段上的比例，裁剪到 [0, 1]：
    t<=0 取端点 a，t>=1 取端点 b，否则为线段内部的垂足。
    端点/整段位于圈内时，最近点同样落在线段上，规则统一适用。
    """
    px, py = p
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    # 调用方已保证相邻节点不重复，这里对退化线段做防御处理。
    if length_sq == 0.0:
        qx, qy = ax, ay
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / length_sq
        if t <= 0.0:
            qx, qy = ax, ay
        elif t >= 1.0:
            qx, qy = bx, by
        else:
            qx = ax + t * dx
            qy = ay + t * dy
    ddx = px - qx
    ddy = py - qy
    distance = math.hypot(ddx, ddy)
    return (qx, qy), distance


def segment_disk_interval(
    a: Point,
    b: Point,
    center: Point,
    radius: float,
) -> Optional[Tuple[float, float]]:
    """闭线段 a-b 与闭圆盘 (center, radius) 的交集参数区间。

    返回 ``(t0, t1)``（0 ≤ t0 ≤ t1 ≤ 1）；无交集返回 ``None``。
    相切为零长 ``t0 == t1``；整段在圈内为 ``(0, 1)``；端点恰在边界上时
    参数严格吸附为 0/1（闭集），使跨拐点合并不依赖浮点误差方向。

    判定与 :func:`nearest_point_on_segment` 的碰撞规则完全一致
    （端点用 ``hypot <= R``，含相切）。线与圆求根以圆心到无限长线的垂距
    （叉积 / 段长）进行，避免大坐标平移下 ``|o|² - R²`` 的灾难性抵消；
    1e6 mm 尺度上判定不确定性仍小于 1e-8 mm，远小于三位小数展示粒度，
    故“近相切未命中”不会被抬成相切。
    """
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    A = dx * dx + dy * dy
    if A == 0.0:  # 防御：调用方已拒绝相邻重复节点
        return (0.0, 0.0) if math.hypot(ax - center[0], ay - center[1]) <= radius else None

    ox, oy = ax - center[0], ay - center[1]
    cross = dx * oy - dy * ox          # 整数坐标下为精确整数（|<2^53| 时）
    perp_sq = (cross * cross) / A      # 圆心到无限长线垂距的平方
    rhs = radius * radius - perp_sq

    # 舍入噪声带只取 ~16 ULP 的加性容差（cross 为一次乘加、perp_sq 一次
    # 除法）。它远小于三位小数展示粒度对应的 R² 间隙：0.0005 mm 的近相切
    # 间隙对应 R² 量级 ~0.015（R=15 时），绝不会被噪声带抬成相切。
    _ulp = 16.0 * 2.220446049250313e-16
    eps = _ulp * (1.0 + perp_sq + radius * radius)
    if rhs < -eps:
        return None

    # 端点分类严格沿用碰撞规则（hypot，<= 含边界），保证“碰撞集合”与
    # “区间片段集合”一一对应。
    in0 = math.hypot(ox, oy) < radius
    in1 = math.hypot(ox + dx, oy + dy) < radius
    on0 = (not in0) and math.hypot(ox, oy) <= radius
    on1 = (not in1) and math.hypot(ox + dx, oy + dy) <= radius
    hit0, hit1 = in0 or on0, in1 or on1

    # 无限长线落于闭盘内的参数区间 [lo, hi]；o=a-center，垂足 t=-o·d/A。
    tc = -(ox * dx + oy * dy) / A
    if rhs <= eps:
        half = 0.0  # 线与盘（数值）相切：唯一接触点
    else:
        half = math.sqrt(rhs / A)
    lo, hi = tc - half, tc + half

    # 根在端点旁的吸附噪声带（参数域仅 8 ULP；物理长度 ≤ 段长×1.8e-15，
    # 远小于展示粒度）。真实近相切的根离端点为宏观量，不受影响。
    t_eps = 8.0 * _ulp

    if hit0 and hit1:
        return (0.0, 1.0)
    if hit0:
        # 右端在圈外：hi 是离开根。另一根 hi 在 0 旁噪声带内（无理半径
        # 下整段向外的端点相切，根算出 ±1e-17）退化为端点零长点；hi 贴 1
        # 则整段在闭包内。
        if hi <= t_eps:
            return (0.0, 0.0)
        return (0.0, 1.0 if hi >= 1.0 - t_eps else hi)
    if hit1:
        # 左端在圈外：lo 是进入根。lo 贴 0 则整段在闭包内，否则 (lo,1)。
        return (0.0 if lo <= t_eps else lo, 1.0)
    # 两端点都在圈外：仅当弦的内段落在线段内部；弦端在噪声带内贴边时
    # 闭集吸附（端点 hypot 已判外，所以宏观贴边不会发生，这里只兜舍入）。
    if lo > -t_eps and hi < 1.0 + t_eps and hi - lo > t_eps:
        return (max(0.0, lo), min(1.0, hi))
    # 内点相切（lo==hi∈(0,1)）。
    if t_eps < lo < 1.0 - t_eps:
        return (lo, lo)
    return None


def _candidate_pairs(
    nodes: Sequence[Point],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
) -> List[Tuple[int, int]]:
    """空间粗筛：返回包围盒（加数值容差）相交的「线段 × 禁入圈」候选。

    x 方向用扫描线维护活动线段集合，y 方向逐候选比较；粗筛只允许
    “多报”（精确求交再剔除），容差保证相切不会因舍入漏候选。
    复杂度近 O((n + m) log n + 候选数)，避免 n×m 无条件全量求交。
    """
    n = len(nodes) - 1
    m = len(circles)
    if n <= 0 or m <= 0:
        return []

    seg_xlo: List[float] = [0.0] * n
    seg_xhi: List[float] = [0.0] * n
    seg_ylo: List[float] = [0.0] * n
    seg_yhi: List[float] = [0.0] * n
    max_abs = 1.0
    for i in range(n):
        ax, ay = nodes[i]
        bx, by = nodes[i + 1]
        x0, x1 = (ax, bx) if ax <= bx else (bx, ax)
        y0, y1 = (ay, by) if ay <= by else (by, ay)
        seg_xlo[i], seg_xhi[i] = x0, x1
        seg_ylo[i], seg_yhi[i] = y0, y1
        max_abs = max(max_abs, abs(ax), abs(ay), abs(bx), abs(by))

    cir_box: List[Tuple[float, float, float, float]] = []
    for (center, r) in circles:
        cx, cy = center
        R = r + cable_radius
        cir_box.append((cx - R, cx + R, cy - R, cy + R))
        max_abs = max(max_abs, abs(cx) + R, abs(cy) + R, R)
    eps = 1e-10 * max_abs

    # 按左/右端点排序的线段下标（确定性：相等时下标升序）。
    by_xlo = sorted(range(n), key=lambda i: (seg_xlo[i], i))
    by_xhi = sorted(range(n), key=lambda i: (seg_xhi[i], i))
    xlo_sorted = [seg_xlo[i] for i in by_xlo]

    # 禁入圈按左界升序扫描；右界不单调时集合中允许多留线段（只会多报）。
    circle_order = sorted(range(m), key=lambda j: (cir_box[j][0], j))

    pairs: List[Tuple[int, int]] = []
    active: set[int] = set()
    added = 0       # by_xlo 已加入活动集的前缀长度（按历史最大右界推进）
    removed = 0     # by_xhi 已永久移除的前缀长度（左界单调递增，安全）
    for j in circle_order:
        bxlo, bxhi, bylo, byhi = cir_box[j]
        target = bisect_right(xlo_sorted, bxhi + eps)
        while added < target:
            active.add(by_xlo[added])
            added += 1
        while removed < n and seg_xhi[by_xhi[removed]] < bxlo - eps:
            active.discard(by_xhi[removed])
            removed += 1
        for i in active:
            # 活动集可能残留为更早（更宽）圆加入、x 上并不与当前圆相交的
            # 线段；这里显式复核 x 重叠，保证候选数只随真实交错量增长。
            if (
                seg_xlo[i] <= bxhi + eps
                and seg_xhi[i] >= bxlo - eps
                and seg_ylo[i] <= byhi + eps
                and seg_yhi[i] >= bylo - eps
            ):
                pairs.append((i, j))
    return pairs


def _build_intervals(pieces: Sequence[SegmentPiece]) -> List[IntrusionInterval]:
    """按禁入圈分组，跨相邻拐点合并，再按 (起始里程, 禁入圈输入序) 排序。"""
    by_circle: dict[int, List[SegmentPiece]] = {}
    for p in pieces:  # pieces 已按 (线段, 禁入圈) 升序
        by_circle.setdefault(p.circle_index, []).append(p)

    intervals: List[IntrusionInterval] = []
    for circle_index, ps in by_circle.items():
        current: List[SegmentPiece] = []
        for p in ps:
            if current:
                prev = current[-1]
                # 仅当：同一 circle（本组天然满足）、线段下标相邻、
                # 前段以 t1==1 确实到达公共节点、后段以 t0==0 从该节点继续。
                joins = (
                    p.segment_index == prev.segment_index + 1
                    and prev.t1 == 1.0
                    and p.t0 == 0.0
                )
            else:
                joins = False
            if joins:
                current.append(p)
            else:
                if current:
                    intervals.append(_mk_interval(circle_index, current))
                current = [p]
        if current:
            intervals.append(_mk_interval(circle_index, current))

    intervals.sort(key=lambda iv: (iv.start_mileage, iv.circle_index))
    return intervals


def _mk_interval(circle_index: int, ps: Sequence[SegmentPiece]) -> IntrusionInterval:
    first, last = ps[0], ps[-1]
    start = first.start_mileage
    end = last.end_mileage
    return IntrusionInterval(
        circle_index=circle_index,
        entry_segment_index=first.segment_index,
        exit_segment_index=last.segment_index,
        entry_point=first.entry_point,
        exit_point=last.exit_point,
        start_mileage=start,
        end_mileage=end,
        length=end - start,
        pieces=tuple(ps),
    )


def _mask_indices(mask: int) -> Tuple[int, ...]:
    """位掩码中置位的位号（= 禁入圈输入序）升序输出。"""
    out: List[int] = []
    while mask:
        low = mask & -mask
        out.append(low.bit_length() - 1)
        mask ^= low
    return tuple(out)


class _Run:
    """扫描中的一个“活动集合相同且 ≥2”的连续单元格序列（可变内部状态）。"""

    __slots__ = ("mask", "start", "end", "start_incl", "end_incl", "by_segment")

    def __init__(self, mask: int, start: float, start_inclusive: bool) -> None:
        self.mask = mask
        self.start = start
        self.end = start
        self.start_incl = start_inclusive
        self.end_incl = False
        # segment_index -> 覆盖该线段的圈掩码（只增不减，合并自动去重）。
        self.by_segment: Dict[int, int] = {}


def _point_at_mileage(
    mileage: float,
    nodes: Sequence[Point],
    cum: Sequence[float],
    seg_index: int,
) -> Point:
    """精确里程对应的路径坐标（线段 seg_index 内线性插值，末里程夹到末节点）。"""
    a = nodes[seg_index]
    b = nodes[seg_index + 1]
    seg_len = cum[seg_index + 1] - cum[seg_index]
    t = 0.0 if seg_len == 0.0 else (mileage - cum[seg_index]) / seg_len
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def _materialize_run(
    run: _Run,
    nodes: Sequence[Point],
    cum: Sequence[float],
    n: int,
) -> CompoundIntrusionSegment:
    """把扫描完毕的 run 按原线段切成 CompoundPiece 列表并生成复合分段。"""
    is_point = run.start == run.end
    pieces: List[CompoundPiece] = []
    for seg_index in sorted(run.by_segment):
        seg_lo = cum[seg_index]
        seg_hi = cum[seg_index + 1]
        lo = run.start if run.start > seg_lo else seg_lo
        hi = run.end if run.end < seg_hi else seg_hi
        # 零长 run 落在拐点：相邻两线段各自得到同坐标零长片段；
        # 非零 run 在某线段上只可能在端点（拐点）退化为零长。
        if hi < lo:
            continue
        cmask = run.by_segment[seg_index] & run.mask
        if cmask == 0:
            continue
        p0 = _point_at_mileage(lo, nodes, cum, seg_index)
        p1 = p0 if hi == lo else _point_at_mileage(hi, nodes, cum, seg_index)
        pieces.append(
            CompoundPiece(
                segment_index=seg_index,
                circle_indices=_mask_indices(cmask),
                entry_point=p0,
                exit_point=p1,
                start_mileage=lo,
                end_mileage=hi,
                length=hi - lo,
            )
        )

    start_point = pieces[0].entry_point
    if is_point:
        end_point = start_point
    else:
        end_point = pieces[-1].exit_point

    return CompoundIntrusionSegment(
        circle_indices=_mask_indices(run.mask),
        start_mileage=run.start,
        end_mileage=run.end,
        start_point=start_point,
        end_point=end_point,
        start_inclusive=run.start_incl,
        end_inclusive=run.end_incl,
        length=run.end - run.start,
        pieces=tuple(pieces),
    )


def _build_compound_segments(
    pieces: Sequence[SegmentPiece],
    nodes: Sequence[Point],
    cum: Sequence[float],
) -> List[CompoundIntrusionSegment]:
    """复合侵入段扫描。

    输入 P 个精确片段（每个是某圈在里程轴上的闭区间 [s,e]，s==e 为
    零长相切点）。在各圈**舍入前的精确闭区间**端点事件上做一次扫描，
    逐事件坐标区分三种活动集合：

    - ``left``：该里程**左邻域** (x-ε, x) 内成立的圈；
    - ``at``：该里程**点态**成立的圈（含仅相切于 x 的圈）；
    - ``right``：该里程**右邻域** (x, x+ε) 内成立的圈。

    里程轴被切成点态单元格 {x} 与开区间单元格 (x_i, x_{i+1})。
    基数 ≥ 2 且掩码相同的连续单元格合并成最大分段——这样
    A=[0,5]、B=[5,10] 保留里程 5 的双圈零长点；A=[0,10]、B=[2,8]、
    C=[8,12] 在 8 处产生独立三圈点，且不计入左右两段的闭端点。

    关键一维事实：任一开区间单元格必完整落在**同一条原线段**的里程
    范围内（跨拐点处必有片段端点，即事件点），故活动片段按线段聚合
    每单元格只产生 O(1) 个分组；拐点单元格的分组来自该点的
    开始/结束/零长片段。整个扫描不在每个事件重查全部圈：

    时间 O(P log P + R)（每片段仅在自身端点被触碰 O(1) 次），
    空间 O(P + A + R)，A 为峰值活动圈数（活动集合是一个整数位掩码）。
    """
    n = len(nodes) - 1
    if not pieces:
        return []

    # 事件里程可能在 t==1 求值时与节点累计里程相差 1 ULP（大数累加），
    # 先计算节点容差，再把片段端点吸附到容差内的节点累计里程：拐点
    # 两侧的结束/开始片段必须落在同一事件坐标，才能正确合并点态集合；
    # 容差为里程尺度的 1e-10，远小于三位小数展示粒度。
    total_mileage = 0.0
    for p in pieces:
        if p.end_mileage > total_mileage:
            total_mileage = p.end_mileage
    node_eps = 1e-10 * max(1.0, total_mileage)

    def _snap(x: float) -> float:
        j = bisect_right(cum, x) - 1
        for idx in (j, j + 1):
            if 0 <= idx <= n and abs(cum[idx] - x) <= node_eps:
                return cum[idx]
        return x

    # 按事件坐标归类：非零片段的开始/结束、零长相切片段。
    starts_at: Dict[float, List[SegmentPiece]] = {}
    ends_at: Dict[float, List[SegmentPiece]] = {}
    zeros_at: Dict[float, List[SegmentPiece]] = {}
    for p in pieces:
        s_mile = _snap(p.start_mileage)
        if p.end_mileage == p.start_mileage:
            zeros_at.setdefault(s_mile, []).append(p)
        else:
            e_mile = _snap(p.end_mileage)
            starts_at.setdefault(s_mile, []).append(p)
            ends_at.setdefault(e_mile, []).append(p)

    coords = sorted(set(starts_at) | set(ends_at) | set(zeros_at))

    def _nearest_node(x: float) -> Optional[int]:
        """返回吸附后等于 x 的节点下标；x 不是节点事件时返回 None。"""
        j = bisect_right(cum, x) - 1
        for idx in (j, j + 1):
            if 0 <= idx <= n and cum[idx] == x:
                return idx
        return None

    def _bits(plist: Sequence[SegmentPiece]) -> int:
        m = 0
        for p in plist:
            m |= 1 << p.circle_index
        return m

    # 分段在扫描中按“左段 → 点段 → 右段”顺序追加；最终用 Python
    # 稳定排序按 (未舍入起始里程, circle_indices) 排列，同键的点段/
    # 开段自然维持字典序与扫描先后。
    runs: List[CompoundIntrusionSegment] = []
    current: Optional[_Run] = None
    active_mask = 0  # 当前左邻域（开区间）活动圈掩码

    def close_current() -> None:
        nonlocal current
        if current is not None:
            runs.append(_materialize_run(current, nodes, cum, n))
            current = None

    def feed(mask: int, start: float, end: float, start_incl: bool,
             groups: Optional[Dict[int, int]]) -> None:
        """推进单元格状态机；groups 为该单元格按线段聚合的圈掩码增量。"""
        nonlocal current
        if mask.bit_count() >= 2:
            if current is None:
                current = _Run(mask, start, start_incl)
            elif current.mask != mask:
                close_current()
                current = _Run(mask, start, start_incl)
            # 掩码相同则延续：起点/start_inclusive 保持首单元格的值。
            current.end = end
            current.end_incl = start == end  # 末单元格为点态才含右端点
            if groups:
                for seg_idx, bits in groups.items():
                    current.by_segment[seg_idx] = (
                        current.by_segment.get(seg_idx, 0) | (bits & mask)
                    )
        else:
            close_current()

    for k, x in enumerate(coords):
        starters = starts_at.get(x, ())
        enders = ends_at.get(x, ())
        zeros = zeros_at.get(x, ())
        start_bits = _bits(starters)
        end_bits = _bits(enders)
        zero_bits = _bits(zeros)

        # 左邻域 active_mask 已含所有非零结束片段（它们覆盖 (·,x]）；
        # 开始片段与零长片段只在点态加入；右邻域移除结束片段。
        at_mask = active_mask | start_bits | zero_bits
        right_mask = (active_mask & ~end_bits) | start_bits

        # ---- 点态单元格 {x}：按原线段聚合覆盖圈 ----
        if at_mask.bit_count() >= 2:
            groups: Dict[int, int] = {}
            at_node = _nearest_node(x) is not None
            if at_node:
                # 拐点：片段必属于相邻两线段之一（结束/零长在前段，
                # 开始在后段），逐片段归组；不会把前段的圈误算进后段。
                for p in starters:
                    groups[p.segment_index] = groups.get(p.segment_index, 0) | (
                        1 << p.circle_index
                    )
                for p in enders:
                    groups[p.segment_index] = groups.get(p.segment_index, 0) | (
                        1 << p.circle_index
                    )
                for p in zeros:
                    groups[p.segment_index] = groups.get(p.segment_index, 0) | (
                        1 << p.circle_index
                    )
            else:
                # 线段内部：该里程只属于一条原线段，所有点态圈都在其上。
                i0 = bisect_right(cum, x) - 1
                if i0 >= n:
                    i0 = n - 1
                groups[i0] = at_mask
            feed(at_mask, x, x, True, groups)
        else:
            feed(at_mask, x, x, True, None)

        active_mask = right_mask

        # ---- 右邻域开区间单元格 (x, next)：恒定活动集，单一线段 ----
        if k + 1 < len(coords):
            xn = coords[k + 1]
            if right_mask.bit_count() >= 2:
                mid = x + (xn - x) * 0.5
                j0 = bisect_right(cum, mid) - 1
                if j0 >= n:
                    j0 = n - 1
                feed(right_mask, x, xn, False, {j0: right_mask})
            else:
                # 下一单元格前关闭（右邻域不足两圈）；后续由下一事件重新判定。
                close_current()

    close_current()

    # 稳定排序：同 (起始里程, circle_indices) 的分段保持扫描先后。
    runs.sort(key=lambda s: (s.start_mileage, s.circle_indices))
    return runs


def analyze_path_full(
    nodes: Sequence[Point],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
) -> Tuple[List[Collision], List[IntrusionInterval], List[CompoundIntrusionSegment]]:
    """一次计算全部碰撞、连续侵入区间与复合侵入段。

    排序：碰撞按 (线段下标, 禁入圈输入顺序)；侵入区间按
    (起始累计里程, 禁入圈输入顺序)；复合侵入段按
    (未舍入起始里程, circle_indices 字典序)。碰撞集合与区间段片一一
    对应：每个「线段 × 禁入圈」闭交集非空恰好对应一处碰撞。
    """
    n = len(nodes) - 1

    # 累计里程：cum[i] = 线段 i 起点的里程。
    cum = cumulative_mileage(nodes)

    pieces: List[SegmentPiece] = []
    collisions: List[Collision] = []
    for seg_idx, cir_idx in _candidate_pairs(nodes, circles, cable_radius):
        a = nodes[seg_idx]
        b = nodes[seg_idx + 1]
        center, circle_r = circles[cir_idx]
        expanded = circle_r + cable_radius
        tv = segment_disk_interval(a, b, center, expanded)
        if tv is None:
            continue
        t0, t1 = tv
        p0 = (a[0] + t0 * (b[0] - a[0]), a[1] + t0 * (b[1] - a[1]))
        p1 = (a[0] + t1 * (b[0] - a[0]), a[1] + t1 * (b[1] - a[1]))
        seg_len = cum[seg_idx + 1] - cum[seg_idx]
        pieces.append(
            SegmentPiece(
                segment_index=seg_idx,
                circle_index=cir_idx,
                t0=t0,
                t1=t1,
                entry_point=p0,
                exit_point=p1,
                start_mileage=cum[seg_idx] + t0 * seg_len,
                end_mileage=cum[seg_idx] + t1 * seg_len,
            )
        )
        nearest, distance = nearest_point_on_segment(center, a, b)
        collisions.append(
            Collision(
                segment_index=seg_idx,
                circle_index=cir_idx,
                nearest=nearest,
                distance=distance,
                expanded_radius=expanded,
            )
        )

    pieces.sort(key=lambda p: (p.segment_index, p.circle_index))
    collisions.sort(key=lambda c: (c.segment_index, c.circle_index))
    intervals = _build_intervals(pieces)
    # 复合侵入段直接在舍入前的精确片段闭区间上扫描（不经跨拐点合并）。
    compounds = _build_compound_segments(pieces, nodes, cum)
    return collisions, intervals, compounds


def analyze_path(
    nodes: Sequence[Point],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
) -> Tuple[List[Collision], List[IntrusionInterval]]:
    """计算碰撞与连续侵入区间（复合侵入段见 :func:`analyze_path_full`）。

    碰撞按 (线段下标, 禁入圈输入顺序) 升序；侵入区间按
    (起始累计里程, 禁入圈输入顺序) 升序。碰撞集合与区间段片一一对应：
    每个「线段 × 禁入圈」闭交集非空恰好对应一处碰撞。
    """
    collisions, intervals, _ = analyze_path_full(nodes, circles, cable_radius)
    return collisions, intervals


def detect_collisions(
    nodes: Sequence[Point],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
) -> List[Collision]:
    """对所有「线段 × 禁入圈」做检测。

    结果按 (线段下标, 禁入圈输入顺序) 升序返回。
    碰撞判据使用闭集（``<=``），故相切边界稳定地判为碰撞。
    """
    return analyze_path(nodes, circles, cable_radius)[0]
