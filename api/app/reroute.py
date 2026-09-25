"""一次性改线预览：候选折线构造、端点校验与风险差分（未舍入双精度）。

现场为新增钻孔临时改线时，需要在同一套标定与精确几何规则下同时看到：

- 原线结论（永远不被改线结果覆盖）；
- 候选线结论：替换 ``nodes[start_index .. end_index]`` 这一连续节点区间，
  接入两端的替代折点 ``replacement_points``（首点必须与
  ``nodes[start_index]`` 精确重合、末点与 ``nodes[end_index]`` 精确重合）；
- 按禁入圈归类的「消除 / 新增 / 仍存在」风险摘要。

结构对应（不依赖三位小数展示值，也不做坐标配对）：

- **前缀段**（下标 ``< start_index``）：候选线段下标与原线相同，里程逐段
  相同，碰撞一一对应为「仍存在」；
- **后缀段**（下标 ``>= end_index``）：候选下标 = 原下标 + 段数平移量，
  原线未改动后缀沿用几何，但里程必须按新路径长度重新累计；
  ``remaining`` 事件同时给出两条线各自的未舍入里程，即里程平移量；
- **替换段**：先判定候选替换折线是否与原被替换折线**几何等价**——
  互为共线加密（简化掉共线中间折点后顶点序列完全一致，整数毫米坐标
  精确比较，不容差），再按模式差分。

替换段的两种模式：

- **非等价（真实绕行）**：保持结构语义——原替换段碰撞整体计为「消除」，
  候选替换段碰撞整体计为「新增」；
- **等价（原样替换 / 共线补点）**：物理占用未变，绝不产生虚假的
  消除/新增。两侧碰撞按**连续侵入区间**（同一禁入圈在曲线上的极大
  连通覆盖，是不随分段方式变化的物理占用身份）分组，每圈各自的区间组
  沿里程一一对应；每组归并为一个「仍存在」项，代表点取组内**最近逼近**
  事件（距离最小、里程最小者优先）——共线加密只是把同一物理位置复制
  到更多线段上，最近逼近点不随分段方式变化，数量与位置因此稳定。

自交路径上「同坐标、不同里程」的事件位于不同原线段，天然落入不同的
结构键或不同的侵入区间组，绝不会被误认为同一事件；全部判断使用
:mod:`geometry` 同一批未舍入双精度结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import Collision, IntrusionInterval, cumulative_mileage

Point = Tuple[float, float]


# ---- 输入校验（跨字段；字段级类型/坐标限制由 Pydantic 模型负责）----


def validate_reroute(
    nodes: Sequence[Point],
    start_index: int,
    end_index: int,
    replacement_points: Sequence[Point],
) -> List[Tuple[Tuple[str, ...], str]]:
    """校验改线区间与接入折点；返回 ``(字段定位, 消息)`` 错误列表（空为通过）。

    字段定位形如 ``('reroute', 'start_index')``，由 main 的异常处理器转成
    与现有约定一致的键 ``reroute.start_index``。
    """
    errors: List[Tuple[Tuple[str, ...], str]] = []
    node_count = len(nodes)

    def bad(loc: Tuple[str, ...], msg: str) -> None:
        errors.append((loc, msg))

    if not (0 <= start_index < node_count):
        bad(
            ("reroute", "start_index"),
            f"start_index 必须在 [0, {node_count - 1}] 内",
        )
    if not (0 <= end_index < node_count):
        bad(
            ("reroute", "end_index"),
            f"end_index 必须在 [0, {node_count - 1}] 内",
        )
    if (
        0 <= start_index < node_count
        and 0 <= end_index < node_count
        and start_index >= end_index
    ):
        bad(
            ("reroute", "start_index"),
            "start_index 必须严格小于 end_index（至少替换一条线段）",
        )

    if len(replacement_points) < 2:
        bad(
            ("reroute", "replacement_points"),
            "replacement_points 至少包含 2 个接入折点（区间两端各一个）",
        )
        return errors

    # 替代折线内部不允许相邻重复折点（与主路径同一退化规则）。
    for k in range(len(replacement_points) - 1):
        p = replacement_points[k]
        q = replacement_points[k + 1]
        if p[0] == q[0] and p[1] == q[1]:
            bad(
                ("reroute", "replacement_points", str(k + 1), "x"),
                f"替代折点 #{k} 与 #{k + 1} 完全重合，禁止相邻重复节点",
            )

    # 端点衔接必须精确相等：输入同为整数毫米，这里直接按未舍入值比较，
    # 不引入容差——容差会把肉眼可见的错位端点静默接上。
    if 0 <= start_index < node_count:
        a = nodes[start_index]
        p0 = replacement_points[0]
        if p0[0] != a[0] or p0[1] != a[1]:
            bad(
                ("reroute", "replacement_points", "0", "x"),
                (
                    "首个接入折点必须与被替换区间起点 "
                    f"#{start_index}（{_fmt_pt(a)}）精确重合，"
                    f"当前为（{_fmt_pt(p0)}）"
                ),
            )
    if 0 <= end_index < node_count:
        b = nodes[end_index]
        p_last = replacement_points[-1]
        if p_last[0] != b[0] or p_last[1] != b[1]:
            last_idx = len(replacement_points) - 1
            bad(
                ("reroute", "replacement_points", str(last_idx), "x"),
                (
                    f"末个接入折点必须与被替换区间终点 #{end_index}"
                    f"（{_fmt_pt(b)}）精确重合，当前为（{_fmt_pt(p_last)}）"
                ),
            )

    # 拼接有效性：候选折线 = 前缀 + 替代折点 + 后缀。扫描其每一对相邻节点，
    # 任何重合（边界点与紧邻原节点重合、替代内部重合漏网等）都产生零长段。
    # 自交（非相邻节点同坐标）允许——风险差分按线段结构键区分，不按坐标配对。
    if not errors:
        candidate = (
            list(nodes[:start_index])
            + list(replacement_points)
            + list(nodes[end_index + 1 :])
        )
        for k in range(len(candidate) - 1):
            if candidate[k] == candidate[k + 1]:
                bad(
                    ("reroute", "replacement_points", "0", "x"),
                    "拼接后的候选折线存在相邻重合节点（零长线段）",
                )
                break
    return errors


def _fmt_pt(p: Point) -> str:
    def fmt(v: float) -> str:
        return str(int(v)) if float(v).is_integer() else str(v)

    return f"{fmt(p[0])}, {fmt(p[1])}"


def build_candidate_nodes(
    nodes: Sequence[Point],
    start_index: int,
    end_index: int,
    replacement_points: Sequence[Point],
) -> List[Point]:
    """拼接待检候选折线：原前缀 + 替代折点 + 原后缀。

    调用前应已通过 :func:`validate_reroute`（端点精确重合，故不重复保留
    边界节点）。
    """
    return (
        list(nodes[:start_index])
        + list(replacement_points)
        + list(nodes[end_index + 1 :])
    )


# ---- 风险差分 ----


@dataclass(frozen=True)
class RiskEvent:
    """单条结构键上的碰撞风险（未舍入双精度）。

    ``segment_index`` 为该结论所属路径自身的线段下标；展示时
    原线用 ``original_*``、候选线用 ``candidate_*`` 的里程。
    """

    segment_index: int
    circle_index: int
    nearest: Point
    distance: float
    expanded_radius: float
    mileage: float


@dataclass(frozen=True)
class PersistedRisk:
    """前缀/后缀同一条原线段上仍存在的风险（携带两条线各自的里程）。"""

    segment_index: int
    circle_index: int
    nearest: Point
    distance: float
    expanded_radius: float
    original_mileage: float
    candidate_mileage: float


@dataclass(frozen=True)
class CircleRiskSummary:
    """按禁入圈归类的改线风险变化（未舍入双精度，仅展示时三位小数）。"""

    circle_index: int
    eliminated: Tuple[RiskEvent, ...]
    added: Tuple[RiskEvent, ...]
    remaining: Tuple[PersistedRisk, ...]


def _collision_mileage(c: Collision, nodes: Sequence[Point], cum: Sequence[float]) -> float:
    """判定位置（最近点）在所属线段上的累计里程。

    按线段参数反算而不是按坐标在全路径上匹配：自交路径同坐标异里程不会
    混淆。最近点是端点裁剪/垂足之一，参数由端点与方向向量投影得到。
    """
    a = nodes[c.segment_index]
    b = nodes[c.segment_index + 1]
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    seg_len = cum[c.segment_index + 1] - cum[c.segment_index]
    if seg_len == 0.0:  # 防御：相邻重复节点已在输入校验拒绝
        return cum[c.segment_index]
    t = ((c.nearest[0] - a[0]) * dx + (c.nearest[1] - a[1]) * dy) / (
        dx * dx + dy * dy
    )
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    return cum[c.segment_index] + t * seg_len


def _to_event(
    c: Collision,
    nodes: Sequence[Point],
    cum: Sequence[float],
) -> RiskEvent:
    return RiskEvent(
        segment_index=c.segment_index,
        circle_index=c.circle_index,
        nearest=c.nearest,
        distance=c.distance,
        expanded_radius=c.expanded_radius,
        mileage=_collision_mileage(c, nodes, cum),
    )


# ---- 几何等价判定（共线加密 ⇔ 同一条物理曲线）----


def _simplify_collinear(points: Sequence[Point]) -> List[Point]:
    """删除严格位于相邻两点之间的共线折点（纯加密的中间定位点）。

    坐标为整数毫米，叉积/点积在双精度内精确（坐标量级远小于 2^53），
    不引入容差。折返（点积 ≤ 0）不是加密——它让曲线多次经过同一位置，
    描画的是另一条曲线，必须保留。
    """
    stack: List[Point] = []
    for p in points:
        stack.append(p)
        while len(stack) >= 3:
            a, b, c = stack[-3], stack[-2], stack[-1]
            abx, aby = b[0] - a[0], b[1] - a[1]
            bcx, bcy = c[0] - b[0], c[1] - b[1]
            if abx * bcy != aby * bcx:
                break  # 不共线
            if abx * bcx + aby * bcy <= 0.0:
                break  # 折返或零步：不是纯共线加密
            del stack[-2]
    return stack


def _replacement_is_equivalent(
    original_nodes: Sequence[Point],
    candidate_nodes: Sequence[Point],
    start_index: int,
    end_index: int,
    shift: int,
) -> bool:
    """候选替换折线是否与原被替换折线描画同一条物理曲线。

    互为共线加密（原样替换、只增删共线中间定位点）时等价：简化后的
    顶点序列逐点精确相等。等价 ⇒ 两条折线覆盖的点集与里程参数化一致，
    每个禁入圈在其上的物理占用（闭集）相同。
    """
    original_sub = original_nodes[start_index : end_index + 1]
    candidate_sub = candidate_nodes[start_index : end_index + shift + 1]
    return _simplify_collinear(original_sub) == _simplify_collinear(candidate_sub)


# ---- 等价替换的物理占用差分 ----


def _interval_group_ids(
    intervals: Sequence[IntrusionInterval],
) -> Dict[Tuple[int, int], int]:
    """``(线段下标, 禁入圈)`` → 所属连续侵入区间号。

    碰撞集合与区间片段一一对应（见 :func:`geometry.analyze_path_full`），
    每个碰撞必属于恰好一个区间；区间即“同一禁入圈在曲线上的极大连通
    覆盖”，是不随分段方式变化的物理占用身份。
    """
    group_of: Dict[Tuple[int, int], int] = {}
    for gid, iv in enumerate(intervals):
        for piece in iv.pieces:
            group_of[(piece.segment_index, piece.circle_index)] = gid
    return group_of


def _representative(
    events: Sequence[Collision],
    nodes: Sequence[Point],
    cum: Sequence[float],
) -> Tuple[Collision, float]:
    """组内最近逼近事件及其里程（距离最小；并列取里程最小者）。

    最近逼近点是曲线弧段上的全局最近点：共线加密后，包含它的那条子
    线段仍把它报为最近点，其余子线段只报更远的端点——代表点不随
    分段方式变化。
    """
    best: Optional[Tuple[Tuple[float, float], Collision, float]] = None
    for c in events:
        mileage = _collision_mileage(c, nodes, cum)
        key = (c.distance, mileage)
        if best is None or key < best[0]:
            best = (key, c, mileage)
    assert best is not None  # 组由事件聚成，必然非空
    return best[1], best[2]


def _diff_equivalent_replacement(
    original_replaced: Sequence[Collision],
    candidate_replaced: Sequence[Collision],
    original_intervals: Sequence[IntrusionInterval],
    candidate_intervals: Sequence[IntrusionInterval],
    original_nodes: Sequence[Point],
    candidate_nodes: Sequence[Point],
    cum_o: Sequence[float],
    cum_c: Sequence[float],
) -> Tuple[List[PersistedRisk], List[RiskEvent], List[RiskEvent]]:
    """几何等价的替换区间：按物理占用（连续侵入区间）配对。

    同一物理曲线 + 同一批禁入圈 ⇒ 两侧区间覆盖集合相同：每圈各自的
    区间组按起始里程排序后一一对应，数量与位置不随分段方式变化。
    每组归并为一个「仍存在」项（携带两条线各自的未舍入里程）；
    理论上不会出现的余量按消除/新增如实报告。
    """

    def grouped(
        events: Sequence[Collision],
        intervals: Sequence[IntrusionInterval],
    ) -> Dict[int, List[Tuple[float, int, List[Collision]]]]:
        group_of = _interval_group_ids(intervals)
        acc: Dict[int, List[Collision]] = {}
        for c in events:
            acc.setdefault(group_of[(c.segment_index, c.circle_index)], []).append(c)
        per_circle: Dict[int, List[Tuple[float, int, List[Collision]]]] = {}
        for gid, evs in acc.items():
            per_circle.setdefault(evs[0].circle_index, []).append(
                (intervals[gid].start_mileage, gid, evs)
            )
        for lst in per_circle.values():
            lst.sort(key=lambda item: (item[0], item[1]))
        return per_circle

    o_groups = grouped(original_replaced, original_intervals)
    c_groups = grouped(candidate_replaced, candidate_intervals)

    remaining: List[PersistedRisk] = []
    eliminated: List[RiskEvent] = []
    added: List[RiskEvent] = []
    for circle in sorted(set(o_groups) | set(c_groups)):
        o_lst = o_groups.get(circle, [])
        c_lst = c_groups.get(circle, [])
        for (_, _, o_evs), (_, _, c_evs) in zip(o_lst, c_lst):
            o_rep, o_mileage = _representative(o_evs, original_nodes, cum_o)
            c_rep, c_mileage = _representative(c_evs, candidate_nodes, cum_c)
            remaining.append(
                PersistedRisk(
                    segment_index=o_rep.segment_index,
                    circle_index=circle,
                    nearest=o_rep.nearest,
                    distance=o_rep.distance,
                    expanded_radius=o_rep.expanded_radius,
                    original_mileage=o_mileage,
                    candidate_mileage=c_mileage,
                )
            )
        # 等价曲线上区间结构相同，两侧组数必相等；余量仅作防御性兜底。
        for _, _, evs in o_lst[len(c_lst) :]:
            eliminated.extend(_to_event(c, original_nodes, cum_o) for c in evs)
        for _, _, evs in c_lst[len(o_lst) :]:
            added.extend(_to_event(c, candidate_nodes, cum_c) for c in evs)
    return remaining, eliminated, added


def diff_risks(
    original_nodes: Sequence[Point],
    candidate_nodes: Sequence[Point],
    start_index: int,
    end_index: int,
    original_collisions: Sequence[Collision],
    candidate_collisions: Sequence[Collision],
    original_intervals: Sequence[IntrusionInterval],
    candidate_intervals: Sequence[IntrusionInterval],
) -> List[CircleRiskSummary]:
    """把两套碰撞差分为「消除 / 新增 / 仍存在」。

    前缀/后缀段按结构键 ``(原线段下标, 禁入圈输入序)`` 配对：候选前缀段
    下标不变，后缀段下标平移 ``shift``。替换区间先做**几何等价**判定
    （共线加密 ⇔ 同一物理曲线）：等价时按连续侵入区间分组的物理占用
    配对为「仍存在」，非等价（真实绕行）时保持结构语义——原替换段
    碰撞为「消除」、候选替换段碰撞为「新增」。整个比较只用未舍入
    双精度与结构/区间身份，不读取三位小数展示值，也不按坐标归并事件。
    """
    cum_o = cumulative_mileage(original_nodes)
    cum_c = cumulative_mileage(candidate_nodes)

    # 候选线：替换折点占 (len(replacement)-1) 段，原区间占
    # (end_index-start_index) 段；后缀段下标平移量即二者之差。
    shift = (len(candidate_nodes) - 1) - (len(original_nodes) - 1)
    equivalent = _replacement_is_equivalent(
        original_nodes, candidate_nodes, start_index, end_index, shift
    )

    # 候选碰撞按结构域分流：前缀/后缀进入结构配对，替换段单独处理。
    persisted_pairs: Dict[Tuple[int, int], Collision] = {}
    candidate_replaced: List[Collision] = []
    for c in candidate_collisions:
        j = c.segment_index
        if j < start_index:
            orig_seg = j  # 前缀：下标相同，里程逐段相同
        elif j >= end_index + shift:
            orig_seg = j - shift  # 后缀：下标平移
        else:
            candidate_replaced.append(c)
            continue
        persisted_pairs[(orig_seg, c.circle_index)] = c

    eliminated_events: List[RiskEvent] = []
    remaining: List[PersistedRisk] = []
    original_replaced: List[Collision] = []
    for c in original_collisions:
        i = c.segment_index
        if start_index <= i < end_index:
            original_replaced.append(c)
            continue
        paired = persisted_pairs.pop((i, c.circle_index), None)
        if paired is None:
            # 未改动线段上的风险不可能因改线消失（同圆同段几何未变）；
            # 只有输入圆/标定变化才会发生，此时按“消除 + 新增”如实报告。
            eliminated_events.append(_to_event(c, original_nodes, cum_o))
            continue
        remaining.append(
            PersistedRisk(
                segment_index=i,
                circle_index=c.circle_index,
                nearest=paired.nearest,
                distance=paired.distance,
                expanded_radius=paired.expanded_radius,
                original_mileage=_collision_mileage(c, original_nodes, cum_o),
                candidate_mileage=_collision_mileage(paired, candidate_nodes, cum_c),
            )
        )

    new_events: List[RiskEvent] = []
    if equivalent:
        # 原样替换 / 共线补点：物理占用未变，按连续侵入区间配对为
        # 「仍存在」，不产生虚假的消除/新增。
        rem, elim, add = _diff_equivalent_replacement(
            original_replaced,
            candidate_replaced,
            original_intervals,
            candidate_intervals,
            original_nodes,
            candidate_nodes,
            cum_o,
            cum_c,
        )
        remaining.extend(rem)
        eliminated_events.extend(elim)
        new_events.extend(add)
    else:
        # 真实绕行：替换段整体消除/新增（结构语义）。
        eliminated_events.extend(
            _to_event(c, original_nodes, cum_o) for c in original_replaced
        )
        new_events.extend(
            _to_event(c, candidate_nodes, cum_c) for c in candidate_replaced
        )

    # 前缀/后缀候选段上出现、原线同键没有的碰撞（圆不变时几何相同不会
    # 发生；若发生则按“新增”如实归类），计为新增。
    for c in persisted_pairs.values():
        new_events.append(_to_event(c, candidate_nodes, cum_c))

    by_circle: Dict[int, Dict[str, list]] = {}

    def bucket(circle_idx: int) -> Dict[str, list]:
        return by_circle.setdefault(
            circle_idx, {"eliminated": [], "added": [], "remaining": []}
        )

    for ev in eliminated_events:
        bucket(ev.circle_index)["eliminated"].append(ev)
    for ev in new_events:
        bucket(ev.circle_index)["added"].append(ev)
    for pr in remaining:
        bucket(pr.circle_index)["remaining"].append(pr)

    summaries: List[CircleRiskSummary] = []
    for circle_index in sorted(by_circle):
        b = by_circle[circle_index]
        elim = sorted(b["eliminated"], key=lambda e: (e.segment_index,))
        added = sorted(b["added"], key=lambda e: (e.segment_index,))
        rem = sorted(b["remaining"], key=lambda r: (r.segment_index,))
        if not elim and not added and not rem:
            continue
        summaries.append(
            CircleRiskSummary(
                circle_index=circle_index,
                eliminated=tuple(elim),
                added=tuple(added),
                remaining=tuple(rem),
            )
        )
    return summaries
