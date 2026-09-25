"""一次性改线预览：候选折线构造、端点校验与**物理占用**风险差分（未舍入双精度）。

现场为新增钻孔临时改线时，需要在同一套标定与精确几何规则下同时看到：

- 原线结论（永远不被改线结果覆盖）；
- 候选线结论：替换 ``nodes[start_index .. end_index]`` 这一连续节点区间，
  接入两端的替代折点 ``replacement_points``（首点必须与
  ``nodes[start_index]`` 精确重合、末点与 ``nodes[end_index]`` 精确重合）；
- 按禁入圈归类的「消除 / 新增 / 仍存在」风险摘要。

风险差分的身份基准是**物理占用**，而不是线段结构键：

- 原样替换（替代折线与原区间逐点重合）不产生任何“消除/新增”，全部风险
  持续存在；
- 共线补点（同一无限长支撑线上插入中间定位点）只改变分段，不改变占用
  点集，同一段持续重叠永远是同一条「仍存在」，数量与区间不随拆分段数
  变化；
- 连续侵入区间跨越替换边界（占用点集在边界节点两侧不断开）时，边界不
  产生虚假的“消除 + 新增”；
- 只有真正离开扩张圈的占用片段计“消除”、真正进入的计“新增”。

实现要点（全部使用 :mod:`geometry` 同一批未舍入双精度片段）：

- 路径节点恒为整数毫米，线段支撑线用整数典范三元组 ``(A, B, C)``
  （``Ax+By+C=0``，gcd 归一、符号归一）精确分组，共线（含反向折叠）
  折线段必然落入同一线组；
- 每条线上把相邻且在公共节点闭接的同方向片段串成“穿越链” visit，
  映射到与走向无关的一维参数 ``u``（物理毫米）；
- 在线组内对合并后的 u 坐标做一次扫描：开区间单元格按活动 visit
  多重集配对（同坐标异里程的自交 visit 互不混淆），点态单元格
  （零长相切/拐点）抽出后按世界点全局聚类、按各路径里程顺序配对；
- 已配对/消除/新增的零长点若只是同身份正长度片段的闭端点则被吸收，
  跨非共线拐点的两臂各自闭合为独立片段——数量与逐段碰撞一一对应且
  对分段方式稳定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .geometry import (
    IntrusionInterval,
    SegmentPiece,
)

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
    # 自交（非相邻节点同坐标）允许——物理差分按点集多重集配对，同坐标
    # 异里程的占用是不同事件，不会被误并。
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


# ---- 风险事件输出模型 ----


@dataclass(frozen=True)
class RiskEvent:
    """单侧（消除/新增）连续物理占用片段（未舍入双精度）。

    身份由「禁入圈 + 世界坐标区间 [entry, exit]」决定，与该片段被拆成
    多少条线段无关。``segment_index`` 为该片段**入口点所属路径自身**的
    线段下标（消除取原线、新增取候选线）；``mileage`` 保留为
    ``start_mileage`` 的同义字段。
    """

    segment_index: int
    circle_index: int
    nearest: Point
    distance: float
    expanded_radius: float
    mileage: float
    entry: Point
    exit: Point
    start_mileage: float
    end_mileage: float
    length: float


@dataclass(frozen=True)
class PersistedRisk:
    """同一段物理占用在原线/候选线上都存在（携带两条线各自的区间里程）。

    ``segment_index`` 取原线入口段；前缀段两里程相等，后缀段里程按新
    路径长度重新累计，差值即里程平移。区间端点同样成对给出，共线补点/
    跨边界合并后身份不随分段变化。
    """

    segment_index: int
    circle_index: int
    nearest: Point
    distance: float
    expanded_radius: float
    original_mileage: float
    candidate_mileage: float
    original_entry: Point
    original_exit: Point
    candidate_entry: Point
    candidate_exit: Point
    original_end_mileage: float
    candidate_end_mileage: float
    length: float


@dataclass(frozen=True)
class CircleRiskSummary:
    """按禁入圈归类的改线风险变化（未舍入双精度，仅展示时三位小数）。"""

    circle_index: int
    eliminated: Tuple[RiskEvent, ...]
    added: Tuple[RiskEvent, ...]
    remaining: Tuple[PersistedRisk, ...]


# ---- 物理占用差分 ----


@dataclass(frozen=False)
class _Line:
    """一条典范支撑线：整数三元组与单位法向/切向（未舍入）。"""

    key: Tuple[int, int, int]
    h: float          # hypot(A, B)（= 段方向长度/g）
    e: Point          # 单位切向（典范走向，与折线遍历方向无关）
    n: Point          # 单位法向 (A/h, B/h)
    offset: float     # n·p = -C/h

    def u(self, p: Point) -> float:
        return p[0] * self.e[0] + p[1] * self.e[1]

    def point_at(self, u: float) -> Point:
        # p = u·e + offset·n
        return (
            u * self.e[0] + self.offset * self.n[0],
            u * self.e[1] + self.offset * self.n[1],
        )


def _line_for_segment(a: Point, b: Point) -> _Line:
    """整数毫米端点线段的典范支撑线。

    ``Ax+By+C=0`` 中取 A=dy/g、B=-dx/g（g=gcd(|dx|,|dy|)），符号归一使
    (A,B) 首个非零分量为正；于是反向遍历的共线段得到**同一条**线与同一
    个 u 轴方向。路径节点恒为整数（标定只变换圆心），三元组是精确整数。
    """
    dx_f, dy_f = b[0] - a[0], b[1] - a[1]
    dx, dy = int(dx_f), int(dy_f)
    g = math.gcd(abs(dx), abs(dy))
    A, B = dy // g, -dx // g
    C = -(A * int(a[0]) + B * int(a[1]))
    if A < 0 or (A == 0 and B < 0):
        A, B, C = -A, -B, -C
    h = math.hypot(A, B)
    e = (-B / h, A / h)
    n = (A / h, B / h)
    return _Line(key=(A, B, C), h=h, e=e, n=n, offset=-C / h)


@dataclass(frozen=False)
class _Visit:
    """同一条支撑线上、同走向、在拐点闭接的连续占用片段链。

    这是物理占用在一条直线上的一次“遍历”：插入多少共线定位点都只会把
    片段接进同一条 visit，故差分对分段方式不敏感。
    """

    vid: int
    side: int                 # 0=原线，1=候选线
    circle_index: int         # 该链所属禁入圈（不同圈的片段绝不链接）
    line: _Line
    sign: int                 # 折线遍历方向相对典范切向 e：+1/-1
    pieces: List[SegmentPiece] = field(default_factory=list)
    pu0: List[float] = field(default_factory=list)  # 各片段入口 u
    pu1: List[float] = field(default_factory=list)  # 各片段出口 u
    ulo: float = 0.0
    uhi: float = 0.0

    @property
    def m_lo(self) -> float:
        return self.pieces[0].start_mileage

    @property
    def m_hi(self) -> float:
        return self.pieces[-1].end_mileage

    def _piece_at(self, u: float, tol: float) -> int:
        """覆盖 u 的片段下标；恰在内部节点时取遍历方向上的**入口段**。

        共享节点同时是上一段出口与下一段入口；持续占用片段的身份按
        遍历入口侧归属（与区间的 entry_segment 一致），故这里优先选
        以该点为遍历入口（``t0==0``，即 ``pu0``）的片段。
        """
        for k in range(len(self.pieces)):
            if abs(self.pu0[k] - u) <= tol:
                return k  # 该点是此片段沿遍历方向的入口
        for k, p in enumerate(self.pieces):
            lo = min(self.pu0[k], self.pu1[k])
            hi = max(self.pu0[k], self.pu1[k])
            if lo - tol <= u <= hi + tol:
                return k
        return 0

    def mileage_at(self, u: float, tol: float) -> float:
        """u 处的累计里程；参数沿该 visit 的**遍历方向**求值（sign 可负）。"""
        k = self._piece_at(u, tol)
        p = self.pieces[k]
        u0, u1 = self.pu0[k], self.pu1[k]
        if abs(u1 - u0) <= tol:
            return p.start_mileage  # 零长片段两端里程相等
        t = (u - u0) / (u1 - u0)
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        return p.start_mileage + t * (p.end_mileage - p.start_mileage)

    def segment_at(self, u: float, tol: float) -> int:
        return self.pieces[self._piece_at(u, tol)].segment_index

    def world_at(self, u: float, tol: float) -> Point:
        k = self._piece_at(u, tol)
        p = self.pieces[k]
        u0, u1 = self.pu0[k], self.pu1[k]
        if abs(u1 - u0) <= tol:
            return p.entry_point
        t = (u - u0) / (u1 - u0)
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        return (
            p.entry_point[0] + t * (p.exit_point[0] - p.entry_point[0]),
            p.entry_point[1] + t * (p.exit_point[1] - p.entry_point[1]),
        )


def _build_visits(
    side: int,
    nodes: Sequence[Point],
    intervals: Sequence[IntrusionInterval],
    lines: Dict[Tuple[int, int, int], _Line],
    vid_start: int,
) -> List[_Visit]:
    """把一条路径的全部占用片段（跨拐点区间已含全部 pieces）串成 visit。

    相邻片段（线段下标连续、前段 t1==1、后段 t0==0）在同一支撑线且
    遍历方向一致时接续；非共线拐点断开（两臂各自成 visit，闭端点在
    点态配对中相遇）；共线反向（折叠）也断开——同坐标异里程是两次
    独立占用。
    """
    pieces: List[SegmentPiece] = []
    for iv in intervals:
        pieces.extend(iv.pieces)
    pieces.sort(key=lambda p: (p.segment_index, p.circle_index))

    visits: List[_Visit] = []
    vid = vid_start
    cur: Optional[_Visit] = None

    def seg_dir(i: int) -> Point:
        return (
            nodes[i + 1][0] - nodes[i][0],
            nodes[i + 1][1] - nodes[i][1],
        )

    for p in pieces:
        a = nodes[p.segment_index]
        b = nodes[p.segment_index + 1]
        line = _line_for_segment(a, b)
        lines.setdefault(line.key, line)
        line = lines[line.key]
        dx, dy = seg_dir(p.segment_index)
        sign = 1 if dx * line.e[0] + dy * line.e[1] > 0.0 else -1
        u0 = line.u(p.entry_point)
        u1 = line.u(p.exit_point)

        joins = False
        if (
            cur is not None
            and cur.circle_index == p.circle_index
            and cur.line.key == line.key
            and cur.sign == sign
        ):
            prev = cur.pieces[-1]
            joins = (
                p.segment_index == prev.segment_index + 1
                and prev.t1 == 1.0
                and p.t0 == 0.0
            )
        if not joins:
            cur = _Visit(
                vid=vid, side=side, circle_index=p.circle_index,
                line=line, sign=sign,
            )
            vid += 1
            visits.append(cur)
        cur.pieces.append(p)
        cur.pu0.append(u0)
        cur.pu1.append(u1)

    for v in visits:
        los = [min(a, b) for a, b in zip(v.pu0, v.pu1)]
        his = [max(a, b) for a, b in zip(v.pu0, v.pu1)]
        v.ulo = min(los)
        v.uhi = max(his)
    return visits


@dataclass(frozen=True)
class _Frag:
    """扫描产出的一个待输出连续片段（正长度或零长点）。"""

    kind: int                 # 0=remaining, 1=eliminated, 2=added
    line_key: Tuple[int, int, int]
    u0: float
    u1: float
    old: Optional[_Visit]
    new: Optional[_Visit]


def _cluster_values(values: Sequence[float], tol: float) -> List[float]:
    """把一维坐标按容差聚类，每簇取最小值（确定性；容差远小于展示粒度）。"""
    if not values:
        return []
    sv = sorted(values)
    clusters: List[float] = [sv[0]]
    for x in sv[1:]:
        if x - clusters[-1] <= tol:
            continue
        clusters.append(x)
    return clusters


def _nearest_on_fragment(
    line: _Line, center: Point, u0: float, u1: float
) -> Tuple[Point, float]:
    """圆心到片段（线上 [u0,u1]）的最近点与距离：投影后夹到片段内。"""
    uc = (center[0] - line.offset * line.n[0]) * line.e[0] + (
        center[1] - line.offset * line.n[1]
    ) * line.e[1]
    if uc < u0:
        uc = u0
    elif uc > u1:
        uc = u1
    q = line.point_at(uc)
    return q, math.hypot(center[0] - q[0], center[1] - q[1])


def _visit_ends_at_path_node(
    v: _Visit, u: float, nodes: Sequence[Point], tol: float
) -> bool:
    """visit 在 u 处的世界点是否就是路径节点（拐点），且 visit 覆盖到它。

    用于判断跨支撑线零长配对是否为“折臂在拐点连续穿越”：该点必须是
    该侧折线的真实节点（某段起点/终点），而不是线段内部的偶然交点。
    """
    wp = v.world_at(u, tol)
    for k, p in enumerate(v.pieces):
        lo = min(v.pu0[k], v.pu1[k])
        hi = max(v.pu0[k], v.pu1[k])
        if not (lo - tol <= u <= hi + tol):
            continue
        for node in (nodes[p.segment_index], nodes[p.segment_index + 1]):
            if abs(node[0] - wp[0]) <= tol and abs(node[1] - wp[1]) <= tol:
                return True
    return False


def diff_risks(
    original_nodes: Sequence[Point],
    candidate_nodes: Sequence[Point],
    original_intervals: Sequence[IntrusionInterval],
    candidate_intervals: Sequence[IntrusionInterval],
    circles: Sequence[Tuple[Point, float]],
    cable_radius: float,
) -> List[CircleRiskSummary]:
    """按**物理占用点集**把两套侵入区间差分为「消除 / 新增 / 仍存在」。

    比较身份是“禁入圈 × 世界坐标上的连续占用片段”，不读取线段下标或
    三位小数展示值：原样替换、共线补点、区间跨越替换边界都不产生虚假
    变化；自交路径同坐标异里程的占用以 visit 多重集保留为独立事件。
    """
    lines: Dict[Tuple[int, int, int], _Line] = {}
    old_visits = _build_visits(0, original_nodes, original_intervals, lines, 0)
    new_visits = _build_visits(1, candidate_nodes, candidate_intervals, lines, 1 << 30)

    circle_ids = sorted(
        {p.circle_index for v in old_visits + new_visits for p in v.pieces}
    )

    # 统一容差：里程/坐标尺度的 1e-10，远小于三位小数展示粒度（0.5mm），
    # 只吸收同一二次方程在共线拆分段上独立求根的 ULP 级差异。
    scale = 1.0
    for v in old_visits + new_visits:
        scale = max(scale, abs(v.ulo), abs(v.uhi), v.m_hi)
    for (cx, cy), r in circles:
        scale = max(scale, abs(cx) + r + cable_radius, abs(cy) + r + cable_radius)
    tol = 1e-10 * scale

    summaries: List[CircleRiskSummary] = []

    for cid in circle_ids:
        center, circle_r = circles[cid]
        expanded = circle_r + cable_radius

        ov = [v for v in old_visits if v.circle_index == cid]
        nv = [v for v in new_visits if v.circle_index == cid]

        positive: List[_Frag] = []
        point_occs: List[Tuple[Point, int, _Visit, float]] = []

        for key, line in lines.items():
            olds = [v for v in ov if v.line.key == key]
            news = [v for v in nv if v.line.key == key]
            if not olds and not news:
                continue
            coords = _cluster_values(
                [x for v in olds + news for x in (v.ulo, v.uhi)], tol
            )

            def active(vs: List[_Visit], a: float, b: float) -> List[_Visit]:
                return [v for v in vs if v.ulo <= a + tol and v.uhi >= b - tol]

            def emit(kind: int, a: float, b: float,
                     o: Optional[_Visit], c: Optional[_Visit]) -> None:
                if positive and positive[-1].kind == kind:
                    last = positive[-1]
                    same_pair = last.old is o and last.new is c and last.line_key == key
                    if same_pair and abs(last.u1 - a) <= tol and b > a:
                        positive[-1] = _Frag(kind, key, last.u0, b, o, c)
                        return
                positive.append(_Frag(kind, key, a, b, o, c))

            # ---- 开区间单元格：活动 visit 多重集做最小权配对 ----
            # 代价按“沿各路径遍历方向的里程远近”：折叠路径上同一支撑线
            # 可能有两个候选 visit（去程/回程），只有里程位置对齐的那个
            # 是同一物理占用；排名贪心会把回程错配过去。配对后剩余按
            # 消除/新增如实归类。
            for k in range(len(coords) - 1):
                a, b = coords[k], coords[k + 1]
                if b - a <= tol:
                    continue
                ao = sorted(active(olds, a, b), key=lambda v: (v.m_lo, v.vid))
                an = sorted(active(news, a, b), key=lambda v: (v.m_lo, v.vid))
                cand_pairs: List[Tuple[float, int, int]] = []
                for io, vo in enumerate(ao):
                    mo = vo.mileage_at(max(a, vo.ulo), tol)
                    for iq, vn in enumerate(an):
                        mn = vn.mileage_at(max(a, vn.ulo), tol)
                        cand_pairs.append((abs(mo - mn), io, iq))
                cand_pairs.sort()
                uo_used: set = set()
                un_used: set = set()
                matched: List[Tuple[int, int]] = []
                for _, io, iq in cand_pairs:
                    if io in uo_used or iq in un_used:
                        continue
                    uo_used.add(io)
                    un_used.add(iq)
                    matched.append((io, iq))
                for io, iq in matched:
                    emit(0, a, b, ao[io], an[iq])
                for i, v in enumerate(ao):
                    if i not in uo_used:
                        emit(1, a, b, v, None)
                for i, v in enumerate(an):
                    if i not in un_used:
                        emit(2, a, b, None, v)

            # ---- 点态单元格：登记覆盖该世界点的全部 visit（含内部覆盖）----
            # 之后在全局聚类里把“相邻段闭接于该点”的 visit 合并为一次
            # 路径经过：共线补点/跨边界替换时该点是同一次经过，不产生
            # 虚假的新增/消除；自交路径在该点有两次不相邻的经过，仍保留
            # 为两个独立事件。
            for x in coords:
                for v in olds:
                    if v.ulo - tol <= x <= v.uhi + tol:
                        point_occs.append((v.world_at(min(max(x, v.ulo), v.uhi), tol), 0, v, x))
                for v in news:
                    if v.ulo - tol <= x <= v.uhi + tol:
                        point_occs.append((v.world_at(min(max(x, v.ulo), v.uhi), tol), 1, v, x))

        # ---- 零长点全局处理（可跨非共线拐点）----
        # 同一 visit 在同一世界点至多登记一次（visit 端点恰为另一线组
        # 坐标时会在不同线组循环里重复入列；内部覆盖也与端点去重）。
        dedup: set = set()
        uniq_occs: List[Tuple[Point, int, _Visit, float]] = []
        for oc in point_occs:
            key = (oc[1], oc[2].vid)
            if key in dedup:
                continue
            dedup.add(key)
            uniq_occs.append(oc)
        point_occs = uniq_occs

        point_occs.sort(key=lambda oc: (oc[0][0], oc[0][1]))
        clusters: List[List[Tuple[Point, int, _Visit, float]]] = []
        for oc in point_occs:
            if clusters:
                ref = clusters[-1][0][0]
                if math.hypot(oc[0][0] - ref[0], oc[0][1] - ref[1]) <= tol:
                    clusters[-1].append(oc)
                    continue
            clusters.append([oc])

        def visit_segments_at(v: _Visit, wp: Point) -> List[int]:
            """visit 在世界点 wp 处覆盖到的线段下标（端点拐点可能有两条）。"""
            out: List[int] = []
            for k, p in enumerate(v.pieces):
                w0 = v.line.u(p.entry_point)
                w1 = v.line.u(p.exit_point)
                uu = v.line.u(wp)
                if min(w0, w1) - tol <= uu <= max(w0, w1) + tol:
                    out.append(p.segment_index)
            return out

        def merge_passes(
            wp: Point,
            occs_side: List[Tuple[Point, int, _Visit, float]],
            path_nodes: Sequence[Point],
        ) -> List[Tuple[_Visit, float]]:
            """把该侧覆盖世界点 ``wp`` 的 visit 按“路径经过”分组（并查集）。

            两个 visit 属于同一次经过，当且仅当它们在该点覆盖到两条
            相邻线段 i、i+1 且共享节点 ``nodes[i+1]`` 恰为该点（连续
            穿越）；不相邻（自交，或折回的两条非相邻段）保持为独立经过。
            纯内部覆盖（visit 只落在一条线段内部，该点是 visit 内部点）
            自成一个经过。
            """
            n = len(path_nodes) - 1
            k_total = len(occs_side)
            parent = list(range(k_total))

            def find(z: int) -> int:
                while parent[z] != z:
                    parent[z] = parent[parent[z]]
                    z = parent[z]
                return z

            # 该点覆盖到的每条线段 -> 一个覆盖它的 occ（同一 visit 已在
            # 入列前去重；不同 visit 共占同一线段只能发生在共线折叠，
            # 那种情形下列表里也只有一个 visit 覆盖——visit 按折线片段
            # 串成，同一段的同一圈片段唯一）。
            seg_to_occ: Dict[int, int] = {}
            for idx, (_, _, v, _) in enumerate(occs_side):
                for seg in visit_segments_at(v, wp):
                    seg_to_occ.setdefault(seg, idx)

            def unite(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

            # 相邻线段在该点闭接（共享节点恰为 wp）→ 同一次经过。
            for seg, idx in seg_to_occ.items():
                nxt = seg + 1
                if nxt <= n and nxt in seg_to_occ:
                    joint = path_nodes[nxt]
                    if abs(joint[0] - wp[0]) <= tol and abs(joint[1] - wp[1]) <= tol:
                        unite(idx, seg_to_occ[nxt])

            groups: Dict[int, List[Tuple[Point, int, _Visit, float]]] = {}
            for idx, oc in enumerate(occs_side):
                groups.setdefault(find(idx), []).append(oc)
            reps: List[Tuple[_Visit, float]] = []
            for members in groups.values():
                members.sort(
                    key=lambda o: (
                        o[2].mileage_at(o[3], tol),
                        o[2].pieces[0].segment_index,
                        o[2].vid,
                    )
                )
                rep_visit = members[0][2]
                reps.append((rep_visit, rep_visit.line.u(wp)))
            reps.sort(key=lambda rv: (rv[0].mileage_at(rv[1], tol), rv[0].vid))
            return reps

        zero_frags: List[_Frag] = []
        for occs in clusters:
            wp = occs[0][0]
            olds_p = merge_passes(
                wp, [o for o in occs if o[1] == 0], original_nodes
            )
            news_p = merge_passes(
                wp, [o for o in occs if o[1] == 1], candidate_nodes
            )

            # 同世界点上两侧“路径经过”的配对（最小权贪心）：
            #
            # - 同支撑线且 u 一致：同一条直线上的同一点，身份最强
            #   （代价按里程接近度）。自交路径同坐标异里程由此按里程
            #   就近配对（0↔0、400↔480），不会错配；
            # - 跨支撑线：仅在两侧都是路径拐点闭接时才配对（折臂连续
            #   穿越），代价高于一切同线配对，故某 pass 还能在本线配对
            #   时绝不被别的折臂抢走（折叠去/回程的拐点不会顶掉同线占用）；
            # - 同线 u 不一致（不同平行线的偶然点）、或非拐点的跨线
            #   孤立相切：代价高到不配对，如实成为消除/新增。
            pairings: List[Tuple[float, int, int]] = []
            for io, (vo, uo) in enumerate(olds_p):
                mo = vo.mileage_at(uo, tol)
                for iq, (vn, un) in enumerate(news_p):
                    mn = vn.mileage_at(un, tol)
                    if vo.line.key == vn.line.key:
                        cost = (
                            1.0e100 if abs(uo - un) > tol else abs(mo - mn)
                        )
                    else:
                        o_at_node = _visit_ends_at_path_node(
                            vo, uo, original_nodes, tol
                        )
                        n_at_node = _visit_ends_at_path_node(
                            vn, un, candidate_nodes, tol
                        )
                        cost = 1.0e6 + abs(mo - mn) if (o_at_node and n_at_node) else 1.0e100
                    pairings.append((cost, io, iq))
            pairings.sort()
            used_o: set = set()
            used_n: set = set()
            pairs: List[Tuple[Tuple[_Visit, float], Tuple[_Visit, float]]] = []
            for cost, io, iq in pairings:
                if cost >= 1.0e99:
                    break
                if io in used_o or iq in used_n:
                    continue
                used_o.add(io)
                used_n.add(iq)
                pairs.append((olds_p[io], news_p[iq]))
            for (vo, uo), (vn, un) in pairs:
                zero_frags.append(_Frag(0, vo.line.key, uo, uo, vo, vn))
            for io, (vo, uo) in enumerate(olds_p):
                if io not in used_o:
                    zero_frags.append(_Frag(1, vo.line.key, uo, uo, vo, None))
            for iq, (vn, un) in enumerate(news_p):
                if iq not in used_n:
                    zero_frags.append(_Frag(2, vn.line.key, un, un, None, vn))

        # ---- 吸收：零长点在两种情况下不独立成项 ----
        # (1) 它是同身份正长度片段在同一世界点的闭端点（跨支撑线也吸收：
        #     拐点竖臂正片段从 x 臂闭包端点出发）；
        # (2) 该点被同侧任一正长度片段**内部覆盖**——该点的占用已由连续
        #     区间表达（如旧线 x 轴内部覆盖到拐点，候选竖臂从该点新增：
        #     拐点本身随持续区间闭合，不再是另一处新增）。
        def absorbed(z: _Frag) -> bool:
            z_line = lines[z.line_key]
            zw = z_line.point_at(z.u0)

            def side_covered(which: str) -> bool:
                """该点是否被该侧某正长度片段闭包覆盖（内部或端点）。"""
                for f in positive:
                    f_line = lines[f.line_key]
                    vv = f.old if which == "old" else f.new
                    if vv is None:
                        continue
                    # 世界点是否落在该片段闭包内：用其支撑线 u 投影夹定。
                    ua, ub = f.u0, f.u1
                    proj = (zw[0] - f_line.offset * f_line.n[0]) * f_line.e[0] + (
                        zw[1] - f_line.offset * f_line.n[1]
                    ) * f_line.e[1]
                    if not (min(ua, ub) - tol <= proj <= max(ua, ub) + tol):
                        continue
                    fw = f_line.point_at(min(max(proj, min(ua, ub)), max(ua, ub)))
                    if math.hypot(fw[0] - zw[0], fw[1] - zw[1]) <= tol:
                        return True
                return False

            for f in positive:
                if f.kind != z.kind:
                    # 条件 (2)：同侧正长度覆盖（身份可以不同，看的是点
                    # 已被连续区间表达，而非该零长配对的 visit）。
                    if (
                        (z.kind == 1 and side_covered("old"))
                        or (z.kind == 2 and side_covered("new"))
                    ):
                        return True
                    continue
                f_line = lines[f.line_key]
                for end_u in (f.u0, f.u1):
                    fw = f_line.point_at(end_u)
                    if math.hypot(fw[0] - zw[0], fw[1] - zw[1]) > tol:
                        continue
                    if z.kind == 0:
                        if f.old is z.old and f.new is z.new:
                            return True
                    elif z.kind == 1 and f.old is z.old:
                        return True
                    elif z.kind == 2 and f.new is z.new:
                        return True
            # 消除/新增的零长点即使没有“同 kind”正片段，只要该侧在点上
            # 有任何正长度覆盖，也应吸收（条件 2 兜底）。
            if z.kind == 1 and side_covered("old"):
                return True
            if z.kind == 2 and side_covered("new"):
                return True
            return False

        zero_frags = [z for z in zero_frags if not absorbed(z)]
        frags = positive + zero_frags

        eliminated: List[RiskEvent] = []
        added: List[RiskEvent] = []
        remaining: List[PersistedRisk] = []

        for f in frags:
            line = lines[f.line_key]
            if f.kind == 0:
                ov_, nv_ = f.old, f.new
                assert ov_ is not None and nv_ is not None
                ua, ub = f.u0, f.u1
                q, dist = _nearest_on_fragment(line, center, min(ua, ub), max(ua, ub))
                # 里程必须沿各路径自身的遍历方向：入口 = 里程小端。
                oma, omb = ov_.mileage_at(ua, tol), ov_.mileage_at(ub, tol)
                cma, cmb = nv_.mileage_at(ua, tol), nv_.mileage_at(ub, tol)
                om0, om1 = min(oma, omb), max(oma, omb)
                cm0, cm1 = min(cma, cmb), max(cma, cmb)
                u_entry_o = ua if oma <= omb else ub
                u_entry_n = ua if cma <= cmb else ub
                u_exit_o = ub if u_entry_o == ua else ua
                u_exit_n = ub if u_entry_n == ua else ua
                remaining.append(
                    PersistedRisk(
                        segment_index=ov_.segment_at(u_entry_o, tol),
                        circle_index=cid,
                        nearest=q,
                        distance=dist,
                        expanded_radius=expanded,
                        original_mileage=om0,
                        candidate_mileage=cm0,
                        original_entry=ov_.world_at(u_entry_o, tol),
                        original_exit=ov_.world_at(u_exit_o, tol),
                        candidate_entry=nv_.world_at(u_entry_n, tol),
                        candidate_exit=nv_.world_at(u_exit_n, tol),
                        original_end_mileage=om1,
                        candidate_end_mileage=cm1,
                        length=om1 - om0,
                    )
                )
            elif f.kind == 1:
                v = f.old
                assert v is not None
                ua, ub = f.u0, f.u1
                q, dist = _nearest_on_fragment(line, center, min(ua, ub), max(ua, ub))
                ma, mb = v.mileage_at(ua, tol), v.mileage_at(ub, tol)
                m0, m1 = min(ma, mb), max(ma, mb)
                u_entry = ua if ma <= mb else ub
                u_exit = ub if u_entry == ua else ua
                eliminated.append(
                    RiskEvent(
                        segment_index=v.segment_at(u_entry, tol),
                        circle_index=cid,
                        nearest=q,
                        distance=dist,
                        expanded_radius=expanded,
                        mileage=m0,
                        entry=v.world_at(u_entry, tol),
                        exit=v.world_at(u_exit, tol),
                        start_mileage=m0,
                        end_mileage=m1,
                        length=m1 - m0,
                    )
                )
            else:
                v = f.new
                assert v is not None
                ua, ub = f.u0, f.u1
                q, dist = _nearest_on_fragment(line, center, min(ua, ub), max(ua, ub))
                ma, mb = v.mileage_at(ua, tol), v.mileage_at(ub, tol)
                m0, m1 = min(ma, mb), max(ma, mb)
                u_entry = ua if ma <= mb else ub
                u_exit = ub if u_entry == ua else ua
                added.append(
                    RiskEvent(
                        segment_index=v.segment_at(u_entry, tol),
                        circle_index=cid,
                        nearest=q,
                        distance=dist,
                        expanded_radius=expanded,
                        mileage=m0,
                        entry=v.world_at(u_entry, tol),
                        exit=v.world_at(u_exit, tol),
                        start_mileage=m0,
                        end_mileage=m1,
                        length=m1 - m0,
                    )
                )

        if not eliminated and not added and not remaining:
            continue

        eliminated.sort(key=lambda e: (e.start_mileage, e.segment_index))
        added.sort(key=lambda e: (e.start_mileage, e.segment_index))
        remaining.sort(key=lambda r: (r.original_mileage, r.segment_index))
        summaries.append(
            CircleRiskSummary(
                circle_index=cid,
                eliminated=tuple(eliminated),
                added=tuple(added),
                remaining=tuple(remaining),
            )
        )

    return summaries
