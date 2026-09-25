"""纯二维几何计算测试。

覆盖：穿越孔位、端点碰撞、相切边界（<= 判碰撞）、整段位于圈内、
最近点（内部垂足 / 端点裁剪）、多处碰撞按 (线段下标, 禁入圈顺序) 排序。
"""

import math

import pytest

from app.geometry import detect_collisions, nearest_point_on_segment


def test_crossing_through_borehole():
    """折线直接穿过孔位：最近点为圆心垂足，距离 0。"""
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 0.0), 10.0)]
    hits = detect_collisions(nodes, circles, cable_radius=5.0)
    assert len(hits) == 1
    h = hits[0]
    assert h.nearest == (50.0, 0.0)
    assert h.distance == pytest.approx(0.0)
    assert h.expanded_radius == 15.0
    assert (h.segment_index, h.circle_index) == (0, 0)


def test_miss_hole_but_cable_swatch_invades():
    """题意核心：折线不穿孔位，但电缆半径侵入扩张安全圈。"""
    nodes = [(-100.0, 0.0), (100.0, 0.0)]
    # 圆心距折线 16mm，扩张半径 15mm：本体安全但电缆侵入 -> 碰撞
    hits = detect_collisions(nodes, [((0.0, 16.0), 10.0)], cable_radius=5.0)
    assert hits == []
    # 距折线 15mm：恰好相切 -> 依 <= 判碰撞
    hits_tangent = detect_collisions(nodes, [((0.0, 15.0), 10.0)], cable_radius=5.0)
    assert len(hits_tangent) == 1
    assert hits_tangent[0].nearest == (0.0, 0.0)
    assert hits_tangent[0].distance == pytest.approx(15.0)


def test_endpoint_collision_uses_endpoint_as_nearest():
    """碰撞发生在端点：最近点被裁剪为线段端点。"""
    nodes = [(0.0, 0.0), (30.0, 0.0)]
    # 圆心 (40,0)、孔半径 10、电缆 5 => 扩张 15；
    # 垂足 x=40 超出线段右端，最近点为端点 (30,0)，距离 10。
    hits = detect_collisions(nodes, [((40.0, 0.0), 10.0)], cable_radius=5.0)
    assert len(hits) == 1
    assert hits[0].nearest == (30.0, 0.0)
    assert hits[0].distance == pytest.approx(10.0)
    assert hits[0].distance <= hits[0].expanded_radius == 15.0

    # 端点恰好相切（距离 == 扩张半径）同样判碰撞
    tangent = detect_collisions(nodes, [((45.0, 0.0), 10.0)], cable_radius=5.0)
    assert len(tangent) == 1
    assert tangent[0].nearest == (30.0, 0.0)
    assert tangent[0].distance == pytest.approx(15.0)

    # 再退 1mm 即安全
    clear = detect_collisions(nodes, [((46.0, 0.0), 10.0)], cable_radius=5.0)
    assert clear == []


def test_entire_segment_inside_circle():
    """整段（含两端点）都位于圈内：规则不变，仍以唯一最近点为判定位置。"""
    nodes = [(0.0, 0.0), (10.0, 10.0)]
    center = (-50.0, -50.0)
    # 两端点到圆心均小于孔半径 100
    assert math.dist((0.0, 0.0), center) < 100.0
    assert math.dist((10.0, 10.0), center) < 100.0
    hits = detect_collisions(nodes, [(center, 100.0)], cable_radius=5.0)
    assert len(hits) == 1
    qx, qy = hits[0].nearest
    # 最近点必须在线段闭包上：q = t*(10,10), t∈[0,1]
    assert 0.0 <= qx <= 10.0 and qx == pytest.approx(qy)
    assert hits[0].distance == pytest.approx(math.dist(center, (qx, qy)))


def test_nearest_point_projection_and_clamping():
    a = (0.0, 0.0)
    b = (10.0, 0.0)
    # 内部垂足
    (q, d) = nearest_point_on_segment((4.0, 3.0), a, b)
    assert q == (4.0, 0.0) and d == pytest.approx(3.0)
    # 投影在起点之前 -> 夹到起点
    (q, d) = nearest_point_on_segment((-5.0, -12.0), a, b)
    assert q == (0.0, 0.0) and d == pytest.approx(13.0)
    # 投影在终点之后 -> 夹到终点
    (q, d) = nearest_point_on_segment((15.0, 8.0), a, b)
    assert q == (10.0, 0.0) and d == pytest.approx(math.hypot(5.0, 8.0))
    # 斜线段上的非整数垂足
    (q, d) = nearest_point_on_segment((1.0, 1.0), (0.0, 0.0), (3.0, 1.0))
    assert q == pytest.approx((1.2, 0.4))
    assert d == pytest.approx(math.hypot(0.2, 0.6))


def test_collisions_sorted_by_segment_then_circle():
    """多处碰撞严格按 (线段下标, 禁入圈输入顺序) 升序。"""
    nodes = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]  # 线段 0 与 1
    circles = [
        ((10.0, 15.0), 3.0),  # c0：仅线段1 端点相切（dist5 = 3+2）
        ((5.0, 0.0), 1.0),    # c1：线段0 穿过（dist0）；线段1 不碰
        ((10.0, 5.0), 1.0),   # c2：仅线段1 穿过
    ]
    hits = detect_collisions(nodes, circles, cable_radius=2.0)
    order = [(h.segment_index, h.circle_index) for h in hits]
    assert order == [(0, 1), (1, 0), (1, 2)]


def test_display_rounding_does_not_create_collision():
    """距离与扩张半径展示三位后相同，但双精度原值未越界时仍为可敷设。"""
    nodes = [(0.0, 0.0), (1.0, 0.0)]
    # sqrt(104) ≈ 10.198039，扩张半径 10.1978；二者展示三位均约为 10.198。
    hits = detect_collisions(nodes, [((3.0, 10.0), 5.1978)], cable_radius=5.0)
    assert hits == []


def test_shared_endpoint_collision_counted_once_per_segment():
    """同一公共端点对两条相邻线段分别构成碰撞时，两个组合都必须保留。"""
    nodes = [(-10.0, 0.0), (0.0, 0.0), (0.0, 10.0)]
    hits = detect_collisions(nodes, [((1.0, -1.0), 0.5)], cable_radius=1.0)
    assert len(hits) == 2
    assert [(h.segment_index, h.circle_index) for h in hits] == [(0, 0), (1, 0)]
    assert all(h.nearest == (0.0, 0.0) for h in hits)
    assert all(h.distance == pytest.approx(math.sqrt(2.0)) for h in hits)
    assert all(h.expanded_radius == 1.5 for h in hits)


def test_no_circle_means_feasible():
    hits = detect_collisions([(0.0, 0.0), (1.0, 1.0)], [], cable_radius=1.0)
    assert hits == []
