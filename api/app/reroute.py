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
- **替换段**：原线区间内的整段（含其在公共端点上的碰撞）被消除；
  候选线替换折点产生的整段碰撞为「新增」；
- **后缀段**（下标 ``>= end_index``）：候选下标 = 原下标 + 段数平移量，
  原线未改动后缀沿用几何，但里程必须按新路径长度重新累计；
  ``remaining`` 事件同时给出两条线各自的未舍入里程，即里程平移量。

自交路径上「同坐标、不同里程」的事件位于不同原线段，天然落入不同的
结构键 ``(segment_index, circle_index)``，因此绝不会被误认为同一事件；
全部判断使用 :mod:`geometry` 同一批未舍入双精度结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .geometry import Collision, cumulative_mileage

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


def diff_risks(
    original_nodes: Sequence[Point],
    candidate_nodes: Sequence[Point],
    start_index: int,
    end_index: int,
    original_collisions: Sequence[Collision],
    candidate_collisions: Sequence[Collision],
) -> List[CircleRiskSummary]:
    """按结构对应把两套碰撞差分为「消除 / 新增 / 仍存在」。

    结构键为 ``(原线段下标, 禁入圈输入序)``：候选前缀段下标不变，后缀段
    下标平移 ``shift``，替换段不与任何原段配对。整个比较只用未舍入里程与
    下标，不读取三位小数展示值，也不按坐标归并事件。
    """
    cum_o = cumulative_mileage(original_nodes)
    cum_c = cumulative_mileage(candidate_nodes)

    # 候选线：替换折点占 (len(replacement)-1) 段，原区间占
    # (end_index-start_index) 段；后缀段下标平移量即二者之差。
    old_span = end_index - start_index
    candidate_seg_count = len(candidate_nodes) - 1
    original_seg_count = len(original_nodes) - 1
    shift = candidate_seg_count - original_seg_count

    # (orig_seg_index, circle) -> 候选碰撞（仅前缀/后缀，即“仍存在”域）
    persisted_pairs: Dict[Tuple[int, int], Collision] = {}
    new_events: List[RiskEvent] = []
    for c in candidate_collisions:
        j = c.segment_index
        if j < start_index:
            orig_seg = j  # 前缀：下标相同，里程逐段相同
        elif j >= end_index + shift:
            orig_seg = j - shift  # 后缀：下标平移
        else:
            new_events.append(_to_event(c, candidate_nodes, cum_c))
            continue
        persisted_pairs[(orig_seg, c.circle_index)] = c

    eliminated_events: List[RiskEvent] = []
    remaining: List[PersistedRisk] = []
    for c in original_collisions:
        i = c.segment_index
        unchanged = i < start_index or i >= end_index
        if not unchanged:
            eliminated_events.append(_to_event(c, original_nodes, cum_o))
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
