"""连续侵入区间（intrusion_intervals）测试。

覆盖：
- 单段穿越/相切零长点/整段圈内/端点进入；
- 拐点双相切合并、连续跨拐点合并、不该合并的边界；
- 重叠/不同禁入圈独立、自交路径同坐标异里程独立、仅舍入后相等仍独立；
- 按 (起始里程, 禁入圈输入序) 排序；未舍入双精度判定、展示三位小数；
- 覆盖集合与总长度合并前后一致；
- 空间粗筛不漏候选（与朴素全量结果一致）；
- 20000 段 × 2000 个局部稀疏禁入圈的性能（<3s）与确定顺序。
"""

import math
import random
import time

import pytest

from app.geometry import (
    analyze_path,
    detect_collisions,
    segment_disk_interval,
    _candidate_pairs,
)


# ---------- 单段闭集交集 ----------

def test_crossing_interval_coordinates_and_mileage():
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    _, ivs = analyze_path(nodes, [((50.0, 0.0), 10.0)], 5.0)
    assert len(ivs) == 1
    iv = ivs[0]
    assert (iv.circle_index, iv.entry_segment_index, iv.exit_segment_index) == (0, 0, 0)
    assert iv.entry_point == (35.0, 0.0)
    assert iv.exit_point == (65.0, 0.0)
    assert iv.start_mileage == 35.0 and iv.end_mileage == 65.0
    assert iv.length == pytest.approx(30.0)


def test_tangent_is_zero_length_interval():
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    _, ivs = analyze_path(nodes, [((50.0, 15.0), 10.0)], 5.0)
    assert len(ivs) == 1
    iv = ivs[0]
    assert iv.length == 0.0
    assert iv.entry_point == iv.exit_point == (50.0, 0.0)
    assert iv.start_mileage == iv.end_mileage == 50.0
    p = iv.pieces[0]
    assert p.t0 == p.t1


def test_near_tangent_miss_remains_miss():
    # 间隙 0.0005mm：双精度下未命中（展示三位无法分辨也不抬成相切）。
    nodes = [(0.0, 0.0), (1000.0, 0.0)]
    coll, ivs = analyze_path(nodes, [((500.0, 15.0005), 10.0)], 5.0)
    assert coll == [] and ivs == []


def test_entire_segment_inside_is_full_interval():
    nodes = [(0.0, 0.0), (10.0, 10.0)]
    _, ivs = analyze_path(nodes, [((-50.0, -50.0), 100.0)], 5.0)
    assert len(ivs) == 1
    p = ivs[0].pieces[0]
    assert (p.t0, p.t1) == (0.0, 1.0)
    assert ivs[0].entry_point == (0.0, 0.0)
    assert ivs[0].exit_point == (10.0, 10.0)
    assert ivs[0].length == pytest.approx(math.hypot(10.0, 10.0))


def test_interval_starts_at_endpoint_entry():
    # 段右端 (30,0) 在扩张圈（圆 (40,0) R15）内，进入参数 25/30。
    g = segment_disk_interval((0.0, 0.0), (30.0, 0.0), (40.0, 0.0), 15.0)
    assert g == pytest.approx((25.0 / 30.0, 1.0))
    # 端点恰好相切 -> 端点零长点
    assert segment_disk_interval((0.0, 0.0), (30.0, 0.0), (45.0, 0.0), 15.0) == (1.0, 1.0)
    # 再退 1mm -> 未命中
    assert segment_disk_interval((0.0, 0.0), (30.0, 0.0), (46.0, 0.0), 15.0) is None


def test_collision_set_and_piece_set_always_match():
    random.seed(123)
    for _ in range(400):
        nn = random.randint(2, 8)
        nodes = [(float(random.randint(-40, 40)), float(random.randint(-40, 40)))]
        while len(nodes) < nn:
            p = (float(random.randint(-40, 40)), float(random.randint(-40, 40)))
            if p != nodes[-1]:
                nodes.append(p)
        cable = random.choice([0.0, 0.5, 1.0, 2.5, 5.0, 5.1978])
        circles = [
            (
                (float(random.randint(-45, 45)), float(random.randint(-45, 45))),
                random.choice([0.1, 1.0, 2.7, 5.0, math.sqrt(2.0)]),
            )
            for _ in range(random.randint(0, 5))
        ]
        coll, ivs = analyze_path(nodes, circles, cable)
        hits_coll = {(c.segment_index, c.circle_index) for c in coll}
        hits_pieces = {
            (p.segment_index, p.circle_index) for iv in ivs for p in iv.pieces
        }
        # 朴素最近点判定作为参照
        expect = set()
        for i in range(len(nodes) - 1):
            ax, ay = nodes[i]
            bx, by = nodes[i + 1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            for j, (c, r) in enumerate(circles):
                t = max(0.0, min(1.0, ((c[0] - ax) * dx + (c[1] - ay) * dy) / L2))
                if math.hypot(c[0] - (ax + t * dx), c[1] - (ay + t * dy)) <= r + cable:
                    expect.add((i, j))
        assert hits_coll == hits_pieces == expect


# ---------- 跨拐点合并 ----------

def test_corner_double_tangent_merges_into_zero_interval():
    # 折角节点 (10,0)；圆心 (11,-1) 在“外角”方向，扩张半径 sqrt(2)
    # 恰好与两段都只在该节点相切（两个零长点）-> 合并为一个零长区间。
    R = math.sqrt(2.0)
    nodes = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    coll, ivs = analyze_path(nodes, [((11.0, -1.0), R - 1.0)], 1.0)
    assert len(coll) == 2
    assert len(ivs) == 1
    iv = ivs[0]
    assert iv.length == 0.0
    assert iv.entry_segment_index == 0 and iv.exit_segment_index == 1
    assert iv.entry_point == iv.exit_point == (10.0, 0.0)
    assert [(p.segment_index, p.t0, p.t1) for p in iv.pieces] == [
        (0, 1.0, 1.0),
        (1, 0.0, 0.0),
    ]


def test_continuous_crossing_merges_across_node():
    # 直线拆成三段，圆连续覆盖中间拐点：合并成一个区间（3 个片段）。
    nodes = [(-20.0, 0.0), (0.0, 0.0), (20.0, 0.0), (40.0, 0.0)]
    _, ivs = analyze_path(nodes, [((10.0, 0.0), 12.0)], 0.0)
    assert len(ivs) == 1
    iv = ivs[0]
    assert iv.entry_segment_index == 0 and iv.exit_segment_index == 2
    assert len(iv.pieces) == 3
    assert [(p.segment_index, p.t0, p.t1) for p in iv.pieces] == [
        (0, pytest.approx(0.9), 1.0),
        (1, 0.0, 1.0),
        (2, 0.0, pytest.approx(0.1)),
    ]
    assert iv.start_mileage == pytest.approx(18.0)
    assert iv.end_mileage == pytest.approx(42.0)
    assert iv.length == pytest.approx(24.0)


def test_no_merge_when_previous_interval_does_not_reach_node():
    # 折返路径：段 0 侵入 [3,7] 不抵节点 (10,0)；段 1 折回同样侵入——
    # 前段没有到达公共节点，必须保持两个独立区间。
    nodes = [(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)]
    _, ivs = analyze_path(nodes, [((5.0, 0.0), 2.0)], 0.0)
    assert len(ivs) == 2
    assert ivs[0].exit_segment_index == 0
    assert ivs[1].entry_segment_index == 1
    # 同坐标异里程：进入/离开坐标重合，里程不同
    assert ivs[0].entry_point == ivs[1].exit_point == (3.0, 0.0)
    assert ivs[0].start_mileage == pytest.approx(3.0)
    assert ivs[1].end_mileage == pytest.approx(17.0)


def test_different_circles_at_same_node_stay_independent():
    R = math.sqrt(2.0)
    nodes = [(-10.0, 0.0), (0.0, 0.0), (0.0, 10.0)]
    _, ivs = analyze_path(
        nodes,
        [((1.0, -1.0), R - 0.5), ((1.0, -1.0), R - 0.5)],
        0.5,
    )
    assert len(ivs) == 2
    assert {iv.circle_index for iv in ivs} == {0, 1}


def test_rounding_only_equal_boundaries_stay_independent():
    # 两个区间在三位小数下显示值可能贴近，但双精度上由几何间隙隔开：
    # 折返路径，扩张圈在每段各侵入一次，绝不跨节点合并。
    nodes = [(0.0, 0.0), (1000.0, 0.0), (0.0, 0.0)]
    _, ivs = analyze_path(nodes, [((500.0, 0.0005), 10.0)], 5.0)
    assert len(ivs) == 2
    assert [iv.entry_segment_index for iv in ivs] == [0, 1]
    # 两者坐标在未舍入双精度下仅差一次反向求值的舍入（显示三位同为
    # 485.000），但里程不同、区间独立——正是“仅舍入后相等也不合并”。
    assert ivs[0].entry_point[0] == pytest.approx(ivs[1].exit_point[0], abs=1e-12)
    assert ivs[0].entry_point[1] == ivs[1].exit_point[1] == 0.0


def test_merge_preserves_coverage_and_total_length():
    random.seed(99)
    for _ in range(200):
        nn = random.randint(2, 12)
        nodes = [(float(random.randint(-30, 30)), float(random.randint(-30, 30)))]
        while len(nodes) < nn:
            p = (float(random.randint(-30, 30)), float(random.randint(-30, 30)))
            if p != nodes[-1]:
                nodes.append(p)
        circles = [
            (
                (float(random.randint(-35, 35)), float(random.randint(-35, 35))),
                random.choice([0.5, 1.0, 2.0, 3.0, 4.0]),
            )
            for _ in range(random.randint(0, 5))
        ]
        cable = random.choice([0.0, 0.5, 1.0, 2.0])
        _, ivs = analyze_path(nodes, circles, cable)
        for iv in ivs:
            segs = [p.segment_index for p in iv.pieces]
            # 片段覆盖连续线段
            assert segs == list(range(segs[0], segs[-1] + 1))
            # 跨拐点处严格相接（前 t1==1 且后 t0==0）
            for prev, nxt in zip(iv.pieces, iv.pieces[1:]):
                assert prev.t1 == 1.0 and nxt.t0 == 0.0
                assert prev.exit_point == nxt.entry_point
            # 区间长度 = 片段长度之和（合并前后总长度一致）
            assert iv.length == pytest.approx(
                sum(p.length for p in iv.pieces), rel=1e-12, abs=1e-12
            )
            assert iv.start_mileage <= iv.end_mileage


# ---------- 排序 ----------

def test_intervals_sorted_by_start_mileage_then_circle_index():
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    # 圈 0 在右侧、圈 1 在左侧；同起点时再按 circle_index。
    circles = [
        ((80.0, 0.0), 5.0),
        ((20.0, 0.0), 5.0),
        ((20.0, 2.0), 5.0),  # 与圈 1 近似同起点，circle_index 决定先后
    ]
    _, ivs = analyze_path(nodes, circles, 5.0)
    starts = [iv.start_mileage for iv in ivs]
    keys = [(iv.start_mileage, iv.circle_index) for iv in ivs]
    assert keys == sorted(keys)
    assert starts[0] <= starts[1] <= starts[2]
    assert [iv.circle_index for iv in ivs[:2]] == [1, 2]


# ---------- 粗筛不漏候选 ----------

def test_coarse_filter_is_superset_of_bbox_intersection():
    random.seed(7)
    for _ in range(500):
        nn = random.randint(2, 9)
        nodes = [(random.uniform(-60, 60), random.uniform(-60, 60))]
        while len(nodes) < nn:
            p = (random.uniform(-60, 60), random.uniform(-60, 60))
            if math.dist(p, nodes[-1]) > 1e-6:
                nodes.append(p)
        cable = random.uniform(0.1, 4.0)
        circles = [
            (
                (random.uniform(-70, 70), random.uniform(-70, 70)),
                random.uniform(0.1, 6.0),
            )
            for _ in range(random.randint(0, 7))
        ]
        fast = set(_candidate_pairs(nodes, circles, cable))
        brute = set()
        for i in range(len(nodes) - 1):
            ax, ay = nodes[i]
            bx, by = nodes[i + 1]
            x0, x1 = sorted((ax, bx))
            y0, y1 = sorted((ay, by))
            for j, (c, r) in enumerate(circles):
                R = r + cable
                if x0 <= c[0] + R and x1 >= c[0] - R and y0 <= c[1] + R and y1 >= c[1] - R:
                    brute.add((i, j))
        assert brute <= fast


# ---------- 性能 ----------

def _build_sparse_scenario(seed: int = 42):
    """20000 段近水平长路径 + 2000 个与路径包围盒交错但局部稀疏的禁入圈。"""
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


def test_sparse_long_path_performance_under_three_seconds():
    nodes, circles = _build_sparse_scenario()
    t0 = time.perf_counter()
    coll, ivs = analyze_path(nodes, circles, 2.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"耗时 {elapsed:.3f}s 超过 3s"
    assert coll == [] and ivs == []  # 局部稀疏：无真实碰撞


def test_sparse_scenario_deterministic_order():
    nodes, circles = _build_sparse_scenario()
    # 路径中段放一个真实侵入，验证输出顺序确定
    circles[123] = ((5_000_000.0, 0.0), 8.0)
    _, ivs_a = analyze_path(nodes, circles, 2.0)
    _, ivs_b = analyze_path(nodes, circles, 2.0)
    keys = lambda ivs: [(iv.start_mileage, iv.circle_index) for iv in ivs]
    assert keys(ivs_a) == keys(ivs_b)
    assert keys(ivs_a) == sorted(keys(ivs_a))


def test_detect_collisions_still_matches_analyze():
    nodes = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    circles = [((5.0, 0.0), 1.0), ((10.0, 5.0), 1.0)]
    only = detect_collisions(nodes, circles, 2.0)
    both, _ = analyze_path(nodes, circles, 2.0)
    assert [(c.segment_index, c.circle_index) for c in only] == [
        (c.segment_index, c.circle_index) for c in both
    ]
