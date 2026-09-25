"""复合侵入段（compound_intrusion_segments）测试。

核心几何由一个**小规模独立预言机**交叉验证：朴素地在每个事件坐标
重扫全部片段，显式区分点态/左邻域/右邻域活动集合，枚举里程轴上的
全部“点单元格 + 开区间单元格”，再聚合成最大分段，与生产扫描器
（O(P log P + R)、事件增量维护）的结果逐字段对比。

另覆盖需求点名的拓扑：
- A=[0,5]、B=[5,10] 保留里程 5 的双圈零长点；
- A=[0,10]、B=[2,8]、C=[8,12] 在 8 处产生独立三圈点，不计入左右
  两段的闭端点（end_inclusive/start_inclusive 为 false）；
- 4.9996/5.0004 同显 5.000 但实际非零长，不并成点；
- 自交路径同坐标异里程不合并；pieces 按原线段切分；
- 排序按未舍入起始里程、circle_indices 字典序；
- 20000 段 × 2000 局部稀疏圈的三秒验收仍成立。
"""

import math
import random
import time

import pytest

from app.geometry import (
    SegmentPiece,
    analyze_path_full,
    _build_compound_segments,
)


# ---------- 合成片段与独立预言机 ----------

# 直线路径：每段恰好 10mm，cum = [0,10,20,...]；坐标 x 即里程。
def synth_nodes(n_segments: int):
    return [(float(i * 10), 0.0) for i in range(n_segments + 1)]


def synth_pieces_for(circle: int, start_mileage: float, end_mileage: float):
    """把一个里程区间展开为落在各原线段上的 SegmentPiece 列表。

    跨越 10mm 拐点时按原线段切开（与真实几何同构）；s==e 为零长片段。
    """
    if start_mileage == end_mileage:
        # 零长点：拐点处归到前一线段（真实几何同时还会有后段零长片段，
        # 那由调用方另行构造；抽象预言机里单一片段即可贡献活动圈位）。
        seg = int(start_mileage // 10)
        if start_mileage > 0 and start_mileage % 10 == 0:
            seg -= 1
        base = seg * 10
        return [
            SegmentPiece(
                segment_index=seg,
                circle_index=circle,
                t0=(start_mileage - base) / 10.0,
                t1=(start_mileage - base) / 10.0,
                entry_point=(float(start_mileage), 0.0),
                exit_point=(float(start_mileage), 0.0),
                start_mileage=float(start_mileage),
                end_mileage=float(start_mileage),
            )
        ]
    out = []
    x = start_mileage
    while x < end_mileage:
        seg = int(x // 10)
        nxt = min(float((seg + 1) * 10), end_mileage)
        base = seg * 10
        out.append(
            SegmentPiece(
                segment_index=seg,
                circle_index=circle,
                t0=(x - base) / 10.0,
                t1=(nxt - base) / 10.0,
                entry_point=(float(x), 0.0),
                exit_point=(float(nxt), 0.0),
                start_mileage=float(x),
                end_mileage=float(nxt),
            )
        )
        x = nxt
    return out


def spec_pieces(specs):
    out = []
    for (c, s, e) in specs:
        out.extend(synth_pieces_for(c, s, e))
    return out


def oracle_segments(specs, n_segments):
    """朴素预言机：枚举所有点/开区间单元格，每格重扫全部片段。

    specs: [(circle, s, e), ...]（s==e 零长）。
    返回 [(mask, start, end, start_inclusive, end_inclusive), ...]。
    """
    pieces = spec_pieces(specs)
    coords = sorted({p.start_mileage for p in pieces} | {p.end_mileage for p in pieces})

    def point_mask(x):
        m = 0
        for p in pieces:
            if p.start_mileage <= x <= p.end_mileage:
                m |= 1 << p.circle_index
        return m

    def open_mask(lo, hi):
        mid = (lo + hi) / 2.0
        m = 0
        for p in pieces:
            if p.start_mileage < mid < p.end_mileage:
                m |= 1 << p.circle_index
        return m

    # 单元格序列：(mask, cell_start, cell_end, is_point)
    cells = []
    for i, x in enumerate(coords):
        cells.append((point_mask(x), x, x, True))
        if i + 1 < len(coords):
            xn = coords[i + 1]
            cells.append((open_mask(x, xn), x, xn, False))

    runs = []
    cur = None
    for mask, cs, ce, is_point in cells:
        if mask.bit_count() >= 2:
            if cur is None or cur[0] != mask:
                if cur is not None:
                    runs.append(cur)
                cur = [mask, cs, ce, is_point, is_point]
            else:
                cur[2] = ce
                cur[4] = is_point  # 末单元格是否含右端点
        else:
            if cur is not None:
                runs.append(cur)
                cur = None
    if cur is not None:
        runs.append(cur)
    return [(m, s, e, si, ei) for (m, s, e, si, ei) in runs]


def run_tuples(segs):
    return [
        (
            s.circle_indices,
            s.start_mileage,
            s.end_mileage,
            s.start_inclusive,
            s.end_inclusive,
        )
        for s in segs
    ]


def build_from_specs(specs, n_segments=6):
    nodes = synth_nodes(n_segments)
    cum = [float(i * 10) for i in range(n_segments + 1)]
    pieces = spec_pieces(specs)
    return _build_compound_segments(pieces, nodes, cum)


# ---------- 需求点名拓扑（精确抽象区间） ----------

def test_ab_meeting_at_five_keeps_double_circle_zero_point():
    # A=[0,5] 在前段结束、B=[5,10] 在后段开始：里程 5 必须是独立
    # 双圈零长点（两个开邻域都只有单圈）。
    specs = [(0, 0.0, 5.0), (1, 5.0, 10.0)]
    got = run_tuples(build_from_specs(specs))
    assert got == [((0, 1), 5.0, 5.0, True, True)]


def test_ab_c_triple_point_is_independent_from_side_runs():
    # A=[0,10]、B=[2,8]、C=[8,12]：[2,8] 双圈段不含右端点 8，
    # 8 是独立三圈零长点，[8,10] 双圈段不含左端点 8。同起点 8 时
    # (0,1,2) 含 (0,2) 为前缀 → (0,1,2) 字典序在前。
    specs = [(0, 0.0, 10.0), (1, 2.0, 8.0), (2, 8.0, 12.0)]
    got = run_tuples(build_from_specs(specs))
    assert got == [
        ((0, 1), 2.0, 8.0, True, False),
        ((0, 1, 2), 8.0, 8.0, True, True),
        ((0, 2), 8.0, 10.0, False, True),
    ]
    # 三圈点分段的几何内容
    segs = build_from_specs(specs)
    triple = segs[1]
    assert triple.length == 0.0
    assert triple.start_point == triple.end_point == (8.0, 0.0)
    (tp,) = triple.pieces
    assert tp.segment_index == 0 and tp.circle_indices == (0, 1, 2)
    assert tp.length == 0.0
    # 左右两圈段边界片段不得把里程 8 算进自身
    left_piece = segs[0].pieces[0]
    right_piece = segs[2].pieces[0]
    assert left_piece.end_mileage == 8.0 and segs[0].end_inclusive is False
    assert right_piece.start_mileage == 8.0 and segs[2].start_inclusive is False


def test_same_active_set_merges_across_open_and_point_cells():
    # A、B 都覆盖 [2,8]，里程 5 还有 C 的零长相切：C 只在 5 处制造
    # 独立三圈点；两侧 {A,B} 开段活动集相同、不含端点 5。契约排序下
    # 左开段起点 2 最先；同起点 5 时 (0,1) 是 (0,1,2) 的前缀，
    # 字典序在前，故右开段先于三圈点。
    specs = [(0, 2.0, 8.0), (1, 2.0, 8.0), (2, 5.0, 5.0)]
    got = run_tuples(build_from_specs(specs))
    assert got == [
        ((0, 1), 2.0, 5.0, True, False),
        ((0, 1), 5.0, 8.0, False, True),
        ((0, 1, 2), 5.0, 5.0, True, True),
    ]


def test_same_active_set_open_cell_continuity_is_one_segment():
    # A、B 全程覆盖 [2,8]，事件之间活动集恒为 {A,B} → 输出一个
    # 含双端点的整段（start/end_inclusive 均 true）。
    specs = [(0, 2.0, 8.0), (1, 2.0, 8.0)]
    got = run_tuples(build_from_specs(specs))
    assert got == [((0, 1), 2.0, 8.0, True, True)]


def test_positive_run_crossing_node_splits_pieces_by_original_segment():
    # 两个圈各自跨拐点（10mm 处）连续覆盖：一个分段、pieces 跨两线段，
    # 拐点处两段的片段都精确到 (10,0)，与 SVG 逐段一致。
    specs = [
        (0, 5.0, 10.0), (0, 10.0, 15.0),
        (1, 5.0, 10.0), (1, 10.0, 15.0),
    ]
    segs = build_from_specs(specs)
    assert len(segs) == 1
    s = segs[0]
    assert (s.circle_indices, s.start_mileage, s.end_mileage) == ((0, 1), 5.0, 15.0)
    assert s.start_inclusive and s.end_inclusive
    assert [p.segment_index for p in s.pieces] == [0, 1]
    assert [(p.start_mileage, p.end_mileage) for p in s.pieces] == [
        (5.0, 10.0),
        (10.0, 15.0),
    ]
    assert s.pieces[0].exit_point == s.pieces[1].entry_point == (10.0, 0.0)
    assert s.length == pytest.approx(10.0)
    assert s.length == pytest.approx(sum(p.length for p in s.pieces))


def test_corner_double_tangent_zero_point_has_piece_on_both_segments():
    # 两圈在拐点 (10,0) 双相切：每圈在前后段各一个零长片段；
    # 复合分段为零长点，但 pieces 覆盖相邻两条原线段。
    nodes = synth_nodes(3)
    cum = [0.0, 10.0, 20.0, 30.0]
    pieces = [
        SegmentPiece(0, 0, 1.0, 1.0, (10.0, 0.0), (10.0, 0.0), 10.0, 10.0),
        SegmentPiece(1, 0, 0.0, 0.0, (10.0, 0.0), (10.0, 0.0), 10.0, 10.0),
        SegmentPiece(0, 1, 1.0, 1.0, (10.0, 0.0), (10.0, 0.0), 10.0, 10.0),
        SegmentPiece(1, 1, 0.0, 0.0, (10.0, 0.0), (10.0, 0.0), 10.0, 10.0),
    ]
    segs = _build_compound_segments(pieces, nodes, cum)
    assert len(segs) == 1
    s = segs[0]
    assert s.circle_indices == (0, 1)
    assert s.start_mileage == s.end_mileage == 10.0
    assert s.length == 0.0 and s.start_inclusive and s.end_inclusive
    assert [p.segment_index for p in s.pieces] == [0, 1]
    assert all(p.length == 0.0 and p.circle_indices == (0, 1) for p in s.pieces)


# ---------- 仅显示值相等 / 同坐标异里程 / 排序 ----------

def test_4_9996_5_0004_display_same_but_keep_nonzero_segment():
    # 两个半径 0.0004、圆心 (5,0) 的扩张圈：精确重叠 [4.9996, 5.0004]，
    # 三位展示起止同为 5.000，但它是非零分段，绝不能并成零长点。
    nodes = [(0.0, 0.0), (20.0, 0.0)]
    circles = [((5.0, 0.0), 0.0004), ((5.0, 0.0), 0.0004)]
    _, _, segs = analyze_path_full(nodes, circles, 0.0)
    assert len(segs) == 1
    s = segs[0]
    assert s.start_mileage == pytest.approx(4.9996)
    assert s.end_mileage == pytest.approx(5.0004)
    assert s.length > 0.0
    assert round(s.start_mileage, 3) == round(s.end_mileage, 3) == 5.0
    assert s.start_inclusive and s.end_inclusive


def test_self_intersecting_same_coordinate_different_mileage_stays_split():
    # 0->10->0 折返：坐标 5 出现在里程 5 与 15；两次重叠必须独立。
    nodes = [(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
    circles = [((5.0, 0.0), 1.0), ((5.0, 0.0), 1.0)]
    _, _, segs = analyze_path_full(nodes, circles, 0.0)
    assert len(segs) == 2
    assert [s.start_mileage for s in segs] == [4.0, 14.0]
    assert segs[0].pieces[0].segment_index == 0
    assert segs[1].pieces[0].segment_index == 1


def test_sorted_by_unrounded_start_then_circle_indices_lexicographic():
    # 三个分段起点不同：按未舍入起始里程升序，再按 circle_indices。
    specs = [
        (0, 1.0, 2.0), (1, 1.0, 2.0),       # (0,1) 起点最早
        (0, 3.0, 4.0), (2, 3.0, 4.0),       # (0,2)，线段 0
        (3, 23.0, 24.0), (4, 23.0, 24.0),   # (3,4)，里程更晚
    ]
    segs = build_from_specs(specs)
    keys = [(s.start_mileage, s.circle_indices) for s in segs]
    assert keys == sorted(keys)
    assert keys == [
        (1.0, (0, 1)),
        (3.0, (0, 2)),
        (23.0, (3, 4)),
    ]


def test_same_start_orders_by_circle_indices_lexicographic():
    # 三圈点处：右开段 {0,2} 与点段 {0,1,2} 同一起点 8；元组前缀
    # 规则下 (0,1,2) < (0,2)，点段排在前。
    specs = [(0, 0.0, 10.0), (1, 2.0, 8.0), (2, 8.0, 12.0)]
    segs = build_from_specs(specs)
    keys = [(s.start_mileage, s.circle_indices) for s in segs]
    assert keys == [
        (2.0, (0, 1)),
        (8.0, (0, 1, 2)),
        (8.0, (0, 2)),
    ]


# ---------- 片段不变量：详情边界与逐段切分 ----------

def test_piece_invariants():
    random.seed(2024)
    nodes = synth_nodes(5)
    cum = [float(i * 10) for i in range(6)]
    for _ in range(300):
        specs = []
        # 每 (圈, 线段) 至多一个区间，端点在拐点时自然跨段相接。
        for c in range(4):
            for seg in range(5):
                if random.random() < 0.55:
                    a = float(random.randint(seg * 10, (seg + 1) * 10))
                    b = float(random.randint(int(a), (seg + 1) * 10))
                    specs.append((c, a, b))
        segs = _build_compound_segments(spec_pieces(specs), nodes, cum)
        for s in segs:
            assert len(s.circle_indices) >= 2
            assert s.circle_indices == tuple(sorted(s.circle_indices))
            seg_ids = [p.segment_index for p in s.pieces]
            assert seg_ids == sorted(seg_ids)
            assert seg_ids == list(range(seg_ids[0], seg_ids[-1] + 1))
            covered = 0.0
            for p in s.pieces:
                assert cum[p.segment_index] <= p.start_mileage <= p.end_mileage <= cum[p.segment_index + 1] + 1e-9
                assert p.circle_indices == tuple(sorted(p.circle_indices))
                assert set(p.circle_indices) <= set(s.circle_indices)
                assert p.length == pytest.approx(p.end_mileage - p.start_mileage)
                covered += p.length
            if s.start_mileage < s.end_mileage:
                assert covered == pytest.approx(s.length)
            # 起止坐标与首末片段一致
            assert s.start_point == s.pieces[0].entry_point
            assert s.end_point == s.pieces[-1].exit_point


# ---------- 独立预言机随机交叉验证 ----------

def test_matches_naive_oracle_on_random_small_inputs():
    random.seed(404)
    for trial in range(500):
        n_segments = random.randint(1, 4)
        n_circles = random.randint(2, 5)
        specs = []
        # 真实几何的片段是逐线段的：每 (圈, 线段) 独立给一个闭区间
        # （端点吸附到 0/1，即里程拐点）；跨拐点连续由两侧片段共同表达，
        # 只在单点相切则只在一侧出现该点。
        for c in range(n_circles):
            for seg in range(n_segments):
                r = random.random()
                if r < 0.45:
                    continue
                lo = seg * 10
                hi = (seg + 1) * 10
                a = float(random.randint(lo, hi))
                if r < 0.58:
                    specs.append((c, a, a))  # 零长相切（含拐点）
                else:
                    b = float(random.randint(int(a), hi))
                    specs.append((c, a, b))
        expected = oracle_segments(specs, n_segments)
        got = run_tuples(build_from_specs(specs, n_segments=n_segments))
        # 预言机输出 tuple 化后比较（mask 转升序 circle_indices）
        def norm(rows):
            return [
                (
                    tuple(
                        i for i in range(n_circles) if (m >> i) & 1
                    )
                    if isinstance(m, int)
                    else m,
                    s,
                    e,
                    si,
                    ei,
                )
                for (m, s, e, si, ei) in rows
            ]
        # 契约顺序：(未舍入起始里程, circle_indices 字典序)。
        contract = lambda rows: sorted(rows, key=lambda r: (r[1], r[0]))
        assert contract(norm(got)) == contract(norm(expected)), (
            trial, specs, got, expected
        )
        # 生产输出必须已按契约排序。
        segs = build_from_specs(specs, n_segments=n_segments)
        order_keys = [(s_.start_mileage, s_.circle_indices) for s_ in segs]
        assert order_keys == sorted(order_keys)


# ---------- 几何级真实圆盘拓扑 ----------

def test_real_disks_ab_meeting_zero_point():
    # 两圈在路径点 10 处“恰好相接”：圈 A 覆盖 [0,10]、圈 B 覆盖 [10,20]
    # （均含端点），里程 10 为双圈零长点，两侧开段各只单圈。
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    # A: 圆心 (0,0) r=10；B: 圆心 (20,0) r=10，cable=0
    coll, ivs, segs = analyze_path_full(
        nodes, [((0.0, 0.0), 10.0), ((20.0, 0.0), 10.0)], 0.0
    )
    assert len(segs) == 1
    s = segs[0]
    assert s.circle_indices == (0, 1)
    assert s.start_mileage == s.end_mileage == 10.0
    assert s.length == 0.0 and s.start_inclusive and s.end_inclusive
    # 零长点在拐点：pieces 覆盖前后两条原线段
    assert [p.segment_index for p in s.pieces] == [0, 1]
    assert all(p.length == 0.0 for p in s.pieces)
    # 普通区间仍各自独立（旧语义不变）
    assert [(iv.circle_index, iv.end_mileage - iv.start_mileage > 0) for iv in ivs] == [
        (0, True),
        (1, True),
    ]


def test_real_disks_triple_point_topology():
    nodes = [(0.0, 0.0), (20.0, 0.0)]
    circles = [
        ((0.0, 0.0), 10.0),    # A [0,10]
        ((5.0, 0.0), 3.0),     # B [2,8]
        ((10.0, 0.0), 2.0),    # C [8,12]
    ]
    _, _, segs = analyze_path_full(nodes, circles, 0.0)
    assert run_tuples(segs) == [
        ((0, 1), 2.0, 8.0, True, False),
        ((0, 1, 2), 8.0, 8.0, True, True),
        ((0, 2), 8.0, 10.0, False, True),
    ]

def test_real_corner_double_tangent_distinct_circles():
    # 折角 (10,0)，两个不同圆心都在外角方向、扩张圈同时相切于该拐点：
    # 复合分段是该点零长点，pieces 跨两条线段。
    R = math.sqrt(2.0)
    nodes = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    circles = [((11.0, -1.0), R), ((10.0 + math.sqrt(1.75), -0.5), R)]
    coll, _, segs = analyze_path_full(nodes, circles, 0.0)
    assert len(coll) == 4  # 两圈 × 两段都相切
    assert len(segs) == 1
    s = segs[0]
    assert s.circle_indices == (0, 1)
    assert s.start_mileage == s.end_mileage == 10.0 and s.length == 0.0
    assert [p.segment_index for p in s.pieces] == [0, 1]


def test_node_endpoint_ulp_mismatch_still_grouped_as_corner():
    # 非整数段长（斜边）下，t==1 求出的结束里程可能与节点累计里程
    # 相差 1 ULP：扫描仍必须把“前段结束 / 后段开始”归到同一拐点事件，
    # 得到双圈零长点，且 pieces 分属两条原线段。
    a = (1.0e8, 0.0)
    b = (1.0e8 + 3.0, 4.0)
    c = (1.0e8 + 6.0, 8.0)
    nodes = [a, b, c]
    seg_len = math.hypot(3.0, 4.0)  # 恰好 5.0
    cum = [0.0, seg_len, 2.0 * seg_len]
    # 人为构造结束里程与节点累计值不等（+1 ULP）的前段片段，
    # 与恰好从节点开始的后段片段配对。
    end_ulp = math.nextafter(cum[1], math.inf)
    pieces = [
        # 圈0 只在段 0 的终点（节点）相切；结束里程人为 +1 ULP。
        SegmentPiece(0, 0, 1.0, 1.0, b, b, cum[1], end_ulp),
        # 圈1 只在段 1 的起点（同一节点）相切。
        SegmentPiece(1, 1, 0.0, 0.0, b, b, cum[1], cum[1]),
    ]
    segs = _build_compound_segments(pieces, nodes, cum)
    assert len(segs) == 1
    one = segs[0]
    assert one.circle_indices == (0, 1)
    assert one.start_mileage == one.end_mileage == cum[1]
    assert one.length == 0.0
    assert [p.segment_index for p in one.pieces] == [0, 1]
    assert all(p.length == 0.0 for p in one.pieces)


# ---------- 性能：20000 段 × 2000 局部稀疏圈 ----------

def _build_sparse_scenario(seed=42):
    random.seed(seed)
    nodes = []
    x = y = 0.0
    for _ in range(20001):
        nodes.append((x, y))
        x += 1000.0
        y += random.uniform(-0.5, 0.5)
    circles = []
    for _ in range(2000):
        circles.append(
            (
                (
                    random.uniform(0, 20_000_000),
                    random.choice([-1.0, 1.0]) * random.uniform(2000.0, 50000.0),
                ),
                3.0,
            )
        )
    return nodes, circles


def test_sparse_scenario_compound_stage_under_three_seconds():
    nodes, circles = _build_sparse_scenario()
    t0 = time.perf_counter()
    coll, ivs, segs = analyze_path_full(nodes, circles, 2.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"耗时 {elapsed:.3f}s 超过 3s"
    assert coll == [] and ivs == [] and segs == []


def test_sparse_scenario_with_overlaps_stays_fast_and_ordered():
    nodes, circles = _build_sparse_scenario()
    # 路径中部放两个相互重叠的真实侵入圈，产生复合分段
    circles[123] = ((5_000_000.0, 0.0), 8.0)
    circles[456] = ((5_000_005.0, 0.0), 8.0)
    t0 = time.perf_counter()
    _, _, segs = analyze_path_full(nodes, circles, 2.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"耗时 {elapsed:.3f}s 超过 3s"
    assert segs, "重叠圈应产生复合分段"
    keys = [(s.start_mileage, s.circle_indices) for s in segs]
    assert keys == sorted(keys)
    # 确定性：重复运行结果一致
    _, _, segs2 = analyze_path_full(nodes, circles, 2.0)
    assert run_tuples(segs) == run_tuples(segs2)
