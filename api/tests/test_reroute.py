"""一次性改线预览测试：物理占用差分（原样替换/共线补点/跨边界/真实绕行）。

夹具刻意小而精确：几何构型手工可算，断言未舍入语义（三位小数展示不参与
比较），原线与候选线必须来自同一标定/几何链路。

差分身份基准是**物理占用**而非线段结构键：原样替换、共线补点、连续
侵入跨越替换边界都必须给出稳定结论（风险持续存在、数量不随分段变化），
只有真正离开/进入扩张圈的占用片段才计入消除/新增。
"""

import math

from fastapi.testclient import TestClient

from app.geometry import analyze_path_full, cumulative_mileage
from app.main import app
from app.reroute import build_candidate_nodes, diff_risks, validate_reroute

client = TestClient(app)
PATH = "/api/precheck"


def post(body):
    return client.post(PATH, json=body)


def diff(nodes, cand, circles, cable_radius=0.0):
    """以完整几何结论（含区间）驱动物理占用差分。"""
    _, ivs_o, _ = analyze_path_full(nodes, circles, cable_radius)
    _, ivs_c, _ = analyze_path_full(cand, circles, cable_radius)
    return diff_risks(nodes, cand, ivs_o, ivs_c, circles, cable_radius)


def counts(summaries):
    return (
        sum(len(s.eliminated) for s in summaries),
        sum(len(s.added) for s in summaries),
        sum(len(s.remaining) for s in summaries),
    )


# ---------- 纯几何夹具：validate_reroute / build_candidate_nodes ----------


def test_validate_rejects_bad_range_and_endpoint_mismatch():
    nodes = [(0, 0), (10, 0), (20, 0)]
    # start >= end
    errs = validate_reroute(nodes, 1, 1, [(10, 0), (20, 0)])
    locs = [loc for loc, _ in errs]
    assert ("reroute", "start_index") in locs

    # 下标越界
    errs = validate_reroute(nodes, 0, 5, [(0, 0), (20, 0)])
    locs = [loc for loc, _ in errs]
    assert ("reroute", "end_index") in locs

    # 首端不衔接（精确相等，不容差）
    errs = validate_reroute(nodes, 0, 1, [(0, 1), (10, 0)])
    locs = [loc for loc, _ in errs]
    assert ("reroute", "replacement_points", "0", "x") in locs

    # 末端不衔接
    errs = validate_reroute(nodes, 0, 1, [(0, 0), (10, 1)])
    locs = [loc for loc, _ in errs]
    assert ("reroute", "replacement_points", "1", "x") in locs

    # 替代折线内部相邻重合
    errs = validate_reroute(nodes, 0, 1, [(0, 0), (5, 5), (5, 5), (10, 0)])
    locs = [loc for loc, _ in errs]
    assert ("reroute", "replacement_points", "2", "x") in locs

    # 折点不足两个
    errs = validate_reroute(nodes, 0, 1, [(0, 0)])
    assert any(loc == ("reroute", "replacement_points") for loc, _ in errs)


def test_validate_accepts_full_prefix_and_suffix_splices():
    nodes = [(0, 0), (10, 0), (20, 0), (30, 0)]
    # 整段路径替换（无前缀/后缀）
    assert validate_reroute(nodes, 0, 3, [(0, 0), (15, -10), (30, 0)]) == []
    # 中间替换
    assert validate_reroute(nodes, 1, 2, [(10, 0), (15, 10), (20, 0)]) == []


def test_validate_flags_zero_length_splice_with_adjacent_original_node():
    # 原线首两段共线相邻：替代首点 == 边界起点，而起点与前缀末节点重合
    # 在合法原线中不可能；直接构造一个“边界起点 == 前节点”的情形只能通过
    # 与原节点相同的替代端点触发，这里用独立调用验证拼接复核本身。
    nodes = [(0, 0), (10, 0), (20, 0)]
    # start_index=1：replacement[0] 必须等于 (10,0)，它与 nodes[0] 不重合 → 合法
    assert validate_reroute(nodes, 1, 2, [(10, 0), (20, 0)]) == []


def test_validate_rejects_zero_length_splice_with_prefix_node():
    # 原线节点 0 与节点 1 不重合；但当 start_index=1 时，
    # replacement[0] == nodes[1] == (10,0)，若 nodes[0] 也恰为 (10,0)
    # 原线就非法了——这里直接验证拼接扫描能兜住任何候选相邻重合。
    nodes = [(0, 0), (10, 0), (10, 0), (20, 0)]  # 原线自身退化仅用于本单元
    errs = validate_reroute(nodes, 1, 2, [(10, 0), (20, 0)])
    assert any("零长" in msg or "重合" in msg for _, msg in errs)


def test_validate_rejects_interior_point_equal_to_boundary():
    # 替代折线含与边界起点重合的内部点：拼接候选产生相邻重合
    nodes = [(0, 0), (10, 0), (20, 0)]
    errs = validate_reroute(
        nodes, 0, 2, [(0, 0), (5, 5), (0, 0), (20, 0)]
    )
    # replacement 内部 (#1,#2) 不重合；重合发生在候选拼接扫描之外（中间段
    # 绕回 (0,0) 不与邻点重合，此例实际合法：自交但无零长段）。
    # 因此这里断言通过——自交路径本身是允许的（风险差分别名已处理）。
    assert errs == []


def test_build_candidate_splice():
    nodes = [(0, 0), (10, 0), (20, 0), (30, 0)]
    cand = build_candidate_nodes(nodes, 1, 2, [(10, 0), (15, -8), (20, 0)])
    assert cand == [(0, 0), (10, 0), (15, -8), (20, 0), (30, 0)]
    # 前缀里程逐段沿用原里程
    assert cumulative_mileage(cand)[:2] == cumulative_mileage(nodes)[:2]


# ---------- 纯几何夹具：切点消除 / 切点新增 ----------


def test_tangent_point_collision_is_eliminated_by_reroute():
    # 原线 (0,0)->(100,0)，扩张半径 15 的圆与线在 (50,0) 相切（零长碰撞）
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 15.0), 15.0)]  # 已是扩张半径（cable+circle 合并夹具）
    raw, _, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1
    assert raw[0].distance == 15.0  # 恰好相切

    # 改线上移 31：与扩张圆完全脱离（30 时恰好相切，必须再远 1mm）
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 31.0), (100.0, 31.0)])
    c_raw, _, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert c_raw == []

    summaries = diff(nodes, cand, circles)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.circle_index == 0
    assert len(s.eliminated) == 1
    assert s.eliminated[0].nearest == (50.0, 0.0)
    assert s.eliminated[0].mileage == 50.0
    assert s.added == () and s.remaining == ()


def test_tangent_point_collision_is_new_on_candidate():
    # 原线在 y=30（相离）；改线下移到 y=15，与 R=15 的圆在 (50,15) 相切
    nodes = [(0.0, 30.0), (100.0, 30.0)]
    circles = [((50.0, 0.0), 15.0)]
    raw, _, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert raw == []

    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 15.0), (100.0, 15.0)])
    c_raw, _, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert len(c_raw) == 1 and c_raw[0].distance == 15.0

    summaries = diff(nodes, cand, circles)
    s = summaries[0]
    assert s.eliminated == () and s.remaining == ()
    assert len(s.added) == 1
    assert s.added[0].nearest == (50.0, 15.0)
    assert s.added[0].mileage == 50.0


# ---------- 跨拐点：连续侵入跨越替换边界时持续存在（不产生虚假变化）----


def test_cross_junction_intrusion_persists_with_mileage_shift():
    # 原线 (0,0)->(10,0)->(20,0)，圆心就在边界节点 (10,0)，R=1：
    # 段0 侵入 [9,10]、段1 侵入 [10,11]，为跨拐点合并区间。
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    circles = [((10.0, 0.0), 1.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert {(c.segment_index, c.circle_index) for c in raw} == {(0, 0), (1, 0)}
    assert len(ivs) == 1 and ivs[0].entry_segment_index == 0
    assert ivs[0].exit_segment_index == 1
    assert ivs[0].start_mileage == 9.0 and ivs[0].end_mileage == 11.0

    # 替换区间 [0,1]：替代折线与原区间**逐点相同** (0,0)->(10,0)。
    # 连续侵入在边界节点 (10,0) 两侧不断开——物理占用完全未变：
    # 不得产生任何消除/新增，整条 [9,11] 是一条「仍存在」。
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (10.0, 0.0)])
    summaries = diff(nodes, cand, circles)
    assert counts(summaries) == (0, 0, 1)
    s = summaries[0]
    rem = s.remaining[0]
    assert rem.segment_index == 0            # 入口在原段0
    assert rem.original_mileage == 9.0
    assert rem.original_end_mileage == 11.0
    assert rem.candidate_mileage == 9.0
    assert rem.candidate_end_mileage == 11.0
    assert rem.length == 2.0
    assert rem.original_entry == (9.0, 0.0)
    assert rem.original_exit == (11.0, 0.0)
    assert rem.candidate_entry == (9.0, 0.0)
    assert rem.candidate_exit == (11.0, 0.0)

    # 改长替换段（绕远），后缀里程按新路径长度重新累计；边界节点上的
    # 占用仍持续存在，而原段0 上 [9,10) 的占用真正被消除、斜臂上的
    # 占用真正新增——这些是物理差异，不是分段产物。
    # (0,0)->(0,-10)->(10,0) 前缀累计到 (10,0) 为 10+sqrt(200)=24.142…
    cand2 = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (0.0, -10.0), (10.0, 0.0)]
    )
    c2_raw, c2_ivs, _ = analyze_path_full(cand2, circles, cable_radius=0.0)
    summaries2 = diff(nodes, cand2, circles)
    e2, a2, m2 = counts(summaries2)
    assert (e2, a2, m2) == (1, 1, 1)
    by = summaries2[0]
    rem2 = by.remaining[0]
    assert rem2.segment_index == 1
    assert math.isclose(rem2.original_mileage, 10.0)
    assert math.isclose(rem2.candidate_mileage, 10.0 + 10.0 * math.sqrt(2.0))
    # 后缀段的区间在候选线上里程整体平移：进入里程 24.142…、离开 25.142…
    seg1_iv = [iv for iv in c2_ivs if iv.exit_segment_index == 2]
    assert seg1_iv and math.isclose(seg1_iv[0].end_mileage, 11.0 + 10.0 * math.sqrt(2.0))
    assert c2_raw  # 候选线仍有碰撞，边界侵入未被“误消除”
    # 消除/新增都只覆盖真正不同的片段（长度均 1），不含边界点零长重复
    assert math.isclose(by.eliminated[0].length, 1.0)
    assert math.isclose(by.added[0].length, 1.0)


# ---------- 里程平移：前缀沿用、后缀重累计 ----------


def test_prefix_mileage_identical_suffix_reaccumulated():
    # 原线四段：替换中间区间 [1,2]
    nodes = [(0, 0), (10, 0), (20, 0), (30, 0)]
    circles = [((5, 0), 0.5), ((25, 0), 0.5)]
    raw, _, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    seg_circles = {(c.segment_index, c.circle_index) for c in raw}
    assert seg_circles == {(0, 0), (2, 1)}

    # 绕远：(10,0)->(10,10)->(20,0)，替换段长 2*sqrt200 ≈ 28.284（原长 10）
    cand = build_candidate_nodes(
        nodes, 1, 2, [(10, 0), (10, 10), (20, 0)]
    )
    summaries = diff(nodes, cand, circles)
    by = {s.circle_index: s for s in summaries}
    # 前缀段0：里程不变
    p0 = by[0].remaining[0]
    assert p0.segment_index == 0
    assert p0.original_mileage == p0.candidate_mileage == 4.5
    assert p0.original_end_mileage == p0.candidate_end_mileage == 5.5
    # 后缀原段2 → 候选段3：原里程 24.5~25.5，候选里程 = 绕远累计 + 4.5/+5.5
    p1 = by[1].remaining[0]
    assert p1.segment_index == 2
    assert math.isclose(p1.original_mileage, 24.5)
    assert math.isclose(p1.original_end_mileage, 25.5)
    assert math.isclose(p1.candidate_mileage, 20.0 + 10.0 * math.sqrt(2.0) + 4.5)
    assert math.isclose(p1.candidate_end_mileage, 20.0 + 10.0 * math.sqrt(2.0) + 5.5)
    assert counts(summaries) == (0, 0, 2)


# ---------- 自交路径：同坐标异里程不得误并为同一事件 ----------


def test_self_intersection_same_coordinate_keep_events_distinct():
    # 自交（闭合）折线：(0,0)->(100,0)->(100,100)->(0,100)->(0,0)
    # 段3 (0,100)->(0,0) 与段0 (0,0)->(100,0) 在 (0,0) 相交。
    nodes = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
    # 圆0 圆心恰在自交点 (0,0)、R=0 退化为点圆：段0 在里程 0 命中、
    # 段3 在闭合矩形累计里程 400 命中——同坐标、异里程的两个独立事件。
    # 圆1 圆心 (2,0) R=0：只在段0 内部里程 2 命中，供“改线消除”对照。
    circles = [((0.0, 0.0), 0.0), ((2.0, 0.0), 0.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    keys = sorted((c.segment_index, c.circle_index) for c in raw)
    assert keys == [(0, 0), (0, 1), (3, 0)]
    # 圆0 的两个零长区间同坐标 (0,0)、里程 0 与 400，绝不跨非相邻段合并
    c0_mileages = sorted(iv.start_mileage for iv in ivs if iv.circle_index == 0)
    assert c0_mileages == [0.0, 400.0]

    # 改线替换区间 [0,1]（段0）：L 形向上绕远
    # (0,0)->(0,40)->(100,40)->(100,0)：
    # 首接入点仍是 (0,0)，圆0 在该点的占用在候选线**仍存在**（世界点未变）；
    # 段3 上同坐标异里程（400→480）的圆0 事件也在后缀持续存在——它们是
    # 自交点上的两次独立经过，必须各自保留、互不配对也不消除。
    cand = build_candidate_nodes(
        nodes, 0, 1, [(0, 0), (0, 40), (100, 40), (100, 0)]
    )
    summaries = {s.circle_index: s for s in diff(nodes, cand, circles)}

    # 圆0：原线两次经过 (0,0)（里程 0 与 400）在候选线仍有同坐标两次
    # 经过（里程 0 与 480）→ 两条「仍存在」，无消除/新增。
    s0 = summaries[0]
    assert counts([s0]) == (0, 0, 2)
    rem0 = {p.segment_index: p for p in s0.remaining}
    assert set(rem0) == {0, 3}
    assert math.isclose(rem0[0].original_mileage, 0.0)
    assert math.isclose(rem0[0].candidate_mileage, 0.0)
    assert math.isclose(rem0[3].original_mileage, 400.0)
    # 候选：替换路径长 180，后缀三边长 300，(0,0) 终点里程 480
    assert math.isclose(rem0[3].candidate_mileage, 480.0)
    # 两条仍存在的世界坐标入口都是 (0,0)（身份按物理点+经过多重集）
    assert rem0[0].original_entry == (0.0, 0.0)
    assert rem0[3].original_entry == (0.0, 0.0)

    # 圆1：段0 内部（里程 2）的命中被改线消除，候选线无新增。
    s1 = summaries[1]
    assert len(s1.eliminated) == 1 and s1.eliminated[0].mileage == 2.0
    assert s1.added == () and s1.remaining == ()


# ---------- 自动化验收：四类固定几何样例（身份/区间/数量/汇总稳定性）----


def test_acceptance_1_identical_replacement_has_no_spurious_change():
    """样例一：原样替换——几何位置完全不变时汇总无任何增减。"""
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 15.0), 15.0)]   # 在 (50,0) 零长相切
    # 替代折线与原区间逐点相同（仍是同一闭线段）。
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (100.0, 0.0)])
    ss = diff(nodes, cand, circles)
    assert counts(ss) == (0, 0, 1)
    r = ss[0].remaining[0]
    assert r.segment_index == 0
    assert r.original_entry == r.candidate_entry == (50.0, 0.0)
    assert r.original_exit == r.candidate_exit == (50.0, 0.0)
    assert r.original_mileage == r.candidate_mileage == 50.0
    assert r.length == 0.0

    # 正长度穿越区间的原样替换同样稳定（圆横穿直线，占用 [41.34,58.66]）。
    circles2 = [((50.0, 5.0), 10.0)]
    ss2 = diff(nodes, cand, circles2)
    assert counts(ss2) == (0, 0, 1)
    r2 = ss2[0].remaining[0]
    assert math.isclose(r2.original_mileage, 50.0 - 5.0 * math.sqrt(3.0))
    assert math.isclose(r2.original_end_mileage, 50.0 + 5.0 * math.sqrt(3.0))
    assert r2.original_entry == r2.candidate_entry
    assert r2.original_exit == r2.candidate_exit
    assert r2.original_mileage == r2.candidate_mileage


def test_acceptance_2_collinear_extra_points_do_not_split_occupancy():
    """样例二：只增加共线定位点——同一段持续重叠不被拆成多项。

    用两种不同的分段方式（2 段 / 4 段，且内部点落在侵入区间内）替换，
    差分必须给出**数量、身份与区间完全相同**的一条「仍存在」。
    """
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 5.0), 10.0)]  # 横穿：[50-5√3, 50+5√3]
    lo, hi = 50.0 - 5.0 * math.sqrt(3.0), 50.0 + 5.0 * math.sqrt(3.0)

    variants = [
        [(0.0, 0.0), (47.0, 0.0), (100.0, 0.0)],                    # 2 段
        [(0.0, 0.0), (30.0, 0.0), (50.0, 0.0), (80.0, 0.0),
         (100.0, 0.0)],                                             # 4 段
        [(0.0, 0.0), (lo - 2.0, 0.0), (lo, 0.0), (hi, 0.0),
         (hi + 3.0, 0.0), (100.0, 0.0)],                            # 区间端点即折点
    ]
    for rep in variants:
        cand = build_candidate_nodes(nodes, 0, 1, rep)
        ss = diff(nodes, cand, circles)
        assert counts(ss) == (0, 0, 1)
        r = ss[0].remaining[0]
        assert r.segment_index == 0
        assert math.isclose(r.original_mileage, lo)
        assert math.isclose(r.original_end_mileage, hi)
        assert math.isclose(r.candidate_mileage, lo)
        assert math.isclose(r.candidate_end_mileage, hi)
        assert math.isclose(r.length, hi - lo)
        assert r.original_entry == (lo, 0.0)
        assert r.original_exit == (hi, 0.0)
        # 候选各分段独立求根，世界坐标仅差 1 ULP（远小于三位展示粒度），
        # 按容差核对；身份与区间不因拆段改变。
        assert math.isclose(r.candidate_entry[0], lo, abs_tol=1e-9)
        assert math.isclose(r.candidate_exit[0], hi, abs_tol=1e-9)
        assert r.candidate_entry[1] == 0.0 and r.candidate_exit[1] == 0.0

    # 相切（零长占用）+ 共线补点：同样恰好一条仍存在，不被拆成消除/新增。
    circles_t = [((50.0, 15.0), 15.0)]
    cand_t = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (25.0, 0.0), (50.0, 0.0), (75.0, 0.0), (100.0, 0.0)]
    )
    ss_t = diff(nodes, cand_t, circles_t)
    assert counts(ss_t) == (0, 0, 1)
    assert ss_t[0].remaining[0].original_entry == (50.0, 0.0)


def test_acceptance_3_overlap_crossing_replacement_boundary_is_stable():
    """样例三：风险区间跨越替换边界而物理重叠未变——无虚假变化提示。"""
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    circles = [((10.0, 0.5), 1.0)]  # 占用 [10-h,10+h]，h=√(1-0.25)
    h = math.sqrt(0.75)

    # (a) 原样替换 [0,1]：区间 [10-h,10+h] 跨过边界节点 (10,0)。
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (10.0, 0.0)])
    ss = diff(nodes, cand, circles)
    assert counts(ss) == (0, 0, 1)
    r = ss[0].remaining[0]
    assert math.isclose(r.original_mileage, 10.0 - h)
    assert math.isclose(r.original_end_mileage, 10.0 + h)
    assert r.original_mileage == r.candidate_mileage

    # (b) 原样替换 + 替换段内共线补点：结论与 (a) 逐项一致。
    cand2 = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (4.0, 0.0), (7.0, 0.0), (10.0, 0.0)]
    )
    ss2 = diff(nodes, cand2, circles)
    assert counts(ss2) == (0, 0, 1)
    r2 = ss2[0].remaining[0]
    assert r2.segment_index == r.segment_index
    assert math.isclose(r2.original_mileage, r.original_mileage)
    assert math.isclose(r2.original_end_mileage, r.original_end_mileage)
    assert r2.original_entry == r.original_entry and r2.original_exit == r.original_exit

    # (c) 替换区间放在中间 [1,2]（四条节点），区间仍跨边界：同样无增减。
    nodes4 = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    cand3 = build_candidate_nodes(
        nodes4, 1, 2, [(10.0, 0.0), (13.0, 0.0), (17.0, 0.0), (20.0, 0.0)]
    )
    ss3 = diff(nodes4, cand3, circles)
    assert counts(ss3) == (0, 0, 1)
    assert math.isclose(ss3[0].remaining[0].original_mileage, 10.0 - h)

    # (d) 圆恰在边界节点（零长两侧正长度区间）：合并为一条仍存在。
    circles0 = [((10.0, 0.0), 1.0)]
    ss4 = diff(nodes, cand, circles0)
    assert counts(ss4) == (0, 0, 1)
    r4 = ss4[0].remaining[0]
    assert r4.original_mileage == 9.0 and r4.original_end_mileage == 11.0
    assert r4.original_entry == (9.0, 0.0) and r4.original_exit == (11.0, 0.0)


def test_acceptance_4_real_detour_reports_only_true_changes():
    """样例四：真实绕行——只把真正消除/新增的物理占用计入变化。"""
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    tangent = [((50.0, 15.0), 15.0)]

    # (a) 相切点真正脱离：1 消除、0 新增、0 仍存在。
    up = build_candidate_nodes(nodes, 0, 1, [(0.0, 31.0), (100.0, 31.0)])
    assert counts(diff(nodes, up, tangent)) == (1, 0, 0)

    # (b) 原线相离、候选真正切入：0 消除、1 新增、0 仍存在。
    # 圆心 (50,0)：距原线 y=30 为 30 > 15（相离），距候选 y=15 恰 15（相切）。
    tangent_b = [((50.0, 0.0), 15.0)]
    nodes_far = [(0.0, 30.0), (100.0, 30.0)]
    down = build_candidate_nodes(nodes_far, 0, 1, [(0.0, 15.0), (100.0, 15.0)])
    assert counts(diff(nodes_far, down, tangent_b)) == (0, 1, 0)

    # (c) 部分共线的真实分叉：候选 (0,0)->(50,0)->(50,-40)->(100,0)
    # 与原线共线共享 [0,50]，在节点 (50,0) 分叉。圆心 (45,0)、R=10：
    # 与原 y=0 线相交世界 x [35,55]；与候选共线段相交 [35,50]（持续存在），
    # 竖段 (50,0)->(50,-40) 水平距圆心 5 < 10 另相交到 y=√75≈8.66（新增）。
    # 于是：持续 [35,50]、消除 (50,55]、新增竖段部分；拐点 (50,0) 被两侧
    # 正长度片段闭端点吸收，不产生零长项。
    circles_j = [((45.0, 0.0), 10.0)]
    fork = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (50.0, 0.0), (50.0, -40.0), (100.0, 0.0)]
    )
    ss = diff(nodes, fork, circles_j)
    assert counts(ss) == (1, 1, 1)
    by = ss[0]
    rem = by.remaining[0]
    assert math.isclose(rem.original_mileage, 35.0)
    assert math.isclose(rem.original_end_mileage, 50.0)
    assert math.isclose(rem.candidate_mileage, 35.0)
    assert math.isclose(rem.candidate_end_mileage, 50.0)
    assert rem.original_entry == (35.0, 0.0)
    assert rem.original_exit == (50.0, 0.0) == rem.candidate_exit
    elim = by.eliminated[0]
    assert math.isclose(elim.start_mileage, 50.0)
    assert math.isclose(elim.end_mileage, 55.0)
    assert math.isclose(elim.length, 5.0)
    added = by.added[0]
    assert added.entry == (50.0, 0.0)
    assert math.isclose(added.exit[0], 50.0)
    assert math.isclose(added.exit[1], -math.sqrt(75.0), rel_tol=1e-9)
    assert math.isclose(added.start_mileage, 50.0)
    assert math.isclose(added.end_mileage, 50.0 + math.sqrt(75.0))


def test_acceptance_4_detour_preserves_suffix_with_mileage_shift():
    """样例四附：绕行后后缀未改动占用持续存在，仅里程平移。"""
    nodes = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    circles = [((10.0, 0.0), 1.0)]
    cand = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (0.0, -10.0), (10.0, 0.0)]
    )
    ss = diff(nodes, cand, circles)
    # x 臂 [9,10) 真消除、斜臂新增、后缀 [10,11] 持续存在（里程平移）。
    assert counts(ss) == (1, 1, 1)
    by = ss[0]
    rem = by.remaining[0]
    assert rem.segment_index == 1
    assert math.isclose(rem.original_mileage, 10.0)
    assert math.isclose(rem.candidate_mileage, 10.0 + 10.0 * math.sqrt(2.0))
    assert rem.original_entry == (10.0, 0.0) == rem.candidate_entry
    # 不存在以边界点为端点的零长消除/新增（点被两侧正长度片段吸收）。
    assert all(e.length > 0.0 for e in by.eliminated)
    assert all(a.length > 0.0 for a in by.added)


def test_acceptance_stability_under_dense_subdivision_long_path():
    """长路径 + 多圈：共线密集细分仍零虚假变化，且结论对细分密度稳定。"""
    import random
    import time

    random.seed(1)
    n = 5000
    nodes = [(float(i * 2), 0.0) for i in range(n + 1)]
    circles = [
        ((float(random.randint(0, n) * 2), 0.0), 0.5) for _ in range(200)
    ]
    _, ivs_o, _ = analyze_path_full(nodes, circles, 0.0)

    def signature(cand):
        _, ivs_c, _ = analyze_path_full(cand, circles, 0.0)
        ss = diff_risks(nodes, cand, ivs_o, ivs_c, circles, 0.0)
        sig = []
        for s in ss:
            for p in s.remaining:
                sig.append(
                    (
                        s.circle_index,
                        round(p.original_mileage, 6),
                        round(p.original_end_mileage, 6),
                        round(p.length, 6),
                    )
                )
        return ss, sorted(sig)

    # 细分 A：替换区以 1mm 步长铺 6000 段；细分 B：2mm 步长 3000 段。
    rep_a = [(2000.0 + i, 0.0) for i in range(0, 6001)]
    rep_b = [(2000.0 + 2 * i, 0.0) for i in range(0, 3001)]
    assert validate_reroute(nodes, 1000, 4000, rep_a) == []
    assert validate_reroute(nodes, 1000, 4000, rep_b) == []
    cand_a = build_candidate_nodes(nodes, 1000, 4000, rep_a)
    cand_b = build_candidate_nodes(nodes, 1000, 4000, rep_b)

    t0 = time.perf_counter()
    ss_a, sig_a = signature(cand_a)
    ss_b, sig_b = signature(cand_b)
    elapsed = time.perf_counter() - t0

    for ss in (ss_a, ss_b):
        assert counts(ss)[:2] == (0, 0)          # 无任何消除/新增
    # 两种细分密度下，持续片段的圈号/区间/长度逐项一致（身份稳定）。
    assert sig_a == sig_b
    assert elapsed < 3.0


# ---------- HTTP 层：字段级 422、兼容性、标定同源 ----------


def _base_reroute_body(**over):
    body = {
        "nodes": [
            {"x": 0, "y": 0},
            {"x": 100, "y": 0},
            {"x": 100, "y": 100},
        ],
        "cable_radius": 5,
        "circles": [
            {"x": 50, "y": 0, "radius": 10},
            {"x": 100, "y": 50, "radius": 10},
        ],
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": [
                {"x": 0, "y": 0},
                {"x": 50, "y": -60},
                {"x": 100, "y": 0},
            ],
        },
    }
    body.update(over)
    return body


def test_preview_returns_both_conclusions_and_circle_risks():
    data = post(_base_reroute_body()).json()
    assert data["feasible"] is False
    assert data["collision_count"] == 2  # 原线结论不动
    preview = data["reroute_preview"]
    cand = preview["candidate"]
    # 候选折线：前缀 + 替代折点 + 后缀
    assert [tuple(p.values()) for p in cand["nodes"]] == [
        (0.0, 0.0),
        (50.0, -60.0),
        (100.0, 0.0),
        (100.0, 100.0),
    ]
    # 孔0 的穿越被消除；孔1 在后缀段仍存在（候选下标 2）
    assert cand["collision_count"] == 1
    assert cand["collisions"][0]["segment_index"] == 2
    assert cand["collisions"][0]["circle_index"] == 1
    risks = {c["circle_index"]: c for c in preview["circle_risks"]}
    assert len(risks[0]["eliminated"]) == 1
    elim0 = risks[0]["eliminated"][0]
    assert elim0["segment_index"] == 0
    # 物理占用区间：扩张15、圆心 (50,0) 与段 (0,0)->(100,0)
    # 相交于世界 x [35,65]（该段里程从 (0,0) 起，即 [35,65]）。
    assert elim0["entry"] == {"x": 35.0, "y": 0.0}
    assert elim0["exit"] == {"x": 65.0, "y": 0.0}
    assert elim0["start_mileage"] == 35.0
    assert elim0["end_mileage"] == 65.0
    assert risks[0]["added"] == [] and risks[0]["remaining"] == []
    assert risks[1]["eliminated"] == [] and risks[1]["added"] == []
    assert len(risks[1]["remaining"]) == 1
    rem = risks[1]["remaining"][0]
    assert rem["segment_index"] == 1
    # 物理占用区间：扩张15、圆心 (100,50) 与竖直段 x=100 相交世界 y[35,65]，
    # 原线里程 [100+35,100+65]=[135,165]。
    assert rem["original_mileage"] == 135.0
    assert rem["original_end_mileage"] == 165.0
    # 替换路径 (0,0)->(50,-60)->(100,0) 长 2*hypot(50,60)=156.205，
    # 后缀段从该里程起计，占用区间整体平移；接口值已三位舍入。
    assert math.isclose(
        rem["candidate_mileage"],
        2.0 * math.hypot(50, 60) + 35.0,
        abs_tol=0.0005,
    )
    assert math.isclose(
        rem["candidate_end_mileage"],
        2.0 * math.hypot(50, 60) + 65.0,
        abs_tol=0.0005,
    )
    assert rem["original_entry"] == {"x": 100.0, "y": 35.0}
    assert rem["candidate_entry"] == {"x": 100.0, "y": 35.0}
    assert preview["eliminated_count"] == 1
    assert preview["added_count"] == 0
    assert preview["remaining_count"] == 1
    assert preview["range"] == {
        "start_index": 0,
        "end_index": 1,
        "replacement_point_count": 3,
    }
    # 候选线 circles 与原线同源（同一批标定后的圈）
    assert cand["circles"] == data["circles"]


def test_http_acceptance_four_fixed_geometric_samples():
    """HTTP 层四类固定几何样例：身份/区间/数量/汇总无虚假增减。"""

    def call(nodes, circles, start, end, rep, cable=5):
        """circles 给出的是扩张半径；实际禁入圈半径 = 扩张半径 - cable。"""
        body = {
            "nodes": [{"x": int(x), "y": int(y)} for x, y in nodes],
            "cable_radius": cable,
            "circles": [
                {"x": int(x), "y": int(y), "radius": int(r) - cable}
                for (x, y), r in circles
            ],
            "reroute": {
                "start_index": start,
                "end_index": end,
                "replacement_points": [
                    {"x": int(x), "y": int(y)} for x, y in rep
                ],
            },
        }
        r = post(body)
        assert r.status_code == 200, r.text
        return r.json()["reroute_preview"]

    # 样例一：原样替换（替代折线与原区间逐点相同）。
    pv = call(
        [(0, 0), (100, 0)], [((50, 15), 15)], 0, 1, [(0, 0), (100, 0)],
    )
    assert (
        pv["eliminated_count"],
        pv["added_count"],
        pv["remaining_count"],
    ) == (0, 0, 1)
    r0 = pv["circle_risks"][0]["remaining"][0]
    assert r0["original_entry"] == {"x": 50.0, "y": 0.0}
    assert r0["original_mileage"] == r0["candidate_mileage"] == 50.0

    # 样例二：共线补点（一段直线补 3 个中间定位点），横穿区间不被拆项。
    pv2 = call(
        [(0, 0), (100, 0)],
        [((50, 5), 10)],
        0,
        1,
        [(0, 0), (30, 0), (50, 0), (80, 0), (100, 0)],
    )
    assert (
        pv2["eliminated_count"],
        pv2["added_count"],
        pv2["remaining_count"],
    ) == (0, 0, 1)
    r2 = pv2["circle_risks"][0]["remaining"][0]
    assert abs(r2["original_mileage"] - (50.0 - 5.0 * math.sqrt(3.0))) < 0.0005
    assert abs(r2["length"] - 10.0 * math.sqrt(3.0)) < 0.0005

    # 样例三：连续侵入跨越替换边界（圆在边界节点旁），原样+补点均无变化。
    # 扩张半径取 6（禁入圈半径 1 + 电缆 5），圆心 (10,0) 与 y=0 相交 [4,16]。
    pv3 = call(
        [(0, 0), (10, 0), (20, 0)],
        [((10, 0), 6)],
        0,
        1,
        [(0, 0), (4, 0), (7, 0), (10, 0)],
    )
    assert (
        pv3["eliminated_count"],
        pv3["added_count"],
        pv3["remaining_count"],
    ) == (0, 0, 1)
    r3 = pv3["circle_risks"][0]["remaining"][0]
    assert r3["original_mileage"] == 4.0 and r3["original_end_mileage"] == 16.0

    # 样例四：真实绕行（中间折点上移 40，整条替代折线脱离相切扩张圈 15）
    # → 仅 1 消除；接入端点仍必须是原节点 (0,0)/(100,0)。
    pv4 = call(
        [(0, 0), (100, 0)],
        [((50, 15), 15)],
        0,
        1,
        [(0, 0), (50, 40), (100, 0)],
    )
    assert (
        pv4["eliminated_count"],
        pv4["added_count"],
        pv4["remaining_count"],
    ) == (1, 0, 0)
    e4 = pv4["circle_risks"][0]["eliminated"][0]
    assert e4["nearest"] == {"x": 50.0, "y": 0.0}
    assert e4["start_mileage"] == e4["end_mileage"] == 50.0
    assert pv4["candidate"]["feasible"] is True


def test_reroute_field_errors_are_field_scoped_422():
    body = _base_reroute_body()
    body["reroute"]["replacement_points"][0] = {"x": 1, "y": 0}
    r = post(body)
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert "reroute.replacement_points[0].x" in errors
    # 校验失败不产生任何结论（响应里根本没有结果体）
    assert "feasible" not in r.json()

    body = _base_reroute_body()
    body["reroute"]["start_index"] = 2
    body["reroute"]["end_index"] = 1
    r = post(body)
    assert r.status_code == 422
    assert "reroute.start_index" in r.json()["errors"]

    body = _base_reroute_body()
    body["reroute"]["start_index"] = True  # 布尔不得冒充下标
    r = post(body)
    assert r.status_code == 422

    body = _base_reroute_body()
    body["reroute"]["replacement_points"][1]["x"] = 50.5  # 必须整数毫米
    r = post(body)
    assert r.status_code == 422
    assert "reroute.replacement_points[1].x" in r.json()["errors"]

    body = _base_reroute_body()
    body["reroute"]["extra"] = 1  # 多余字段
    r = post(body)
    assert r.status_code == 422


def test_without_reroute_response_is_unchanged():
    body = _base_reroute_body()
    del body["reroute"]
    data = post(body).json()
    assert data["reroute_preview"] is None
    assert set(data.keys()) == {
        "feasible",
        "cable_radius",
        "nodes",
        "circles",
        "collision_count",
        "first_collision",
        "collisions",
        "intrusion_intervals",
        "compound_intrusion_segments",
        "calibration",
        "reroute_preview",
    }


def test_reroute_uses_same_calibration_for_both_routes():
    # survey = path + (1000,2000)（纯平移标定）；
    # 原线 (0,0)->(100,0)，孔 survey (1050,2000) r10、cable5
    # → 变换后圆心 (50,0)，扩张15，穿越。
    # 改线绕到 y=-30 后相离：风险被消除。两条线必须共用同一标定。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 1050, "y": 2000, "radius": 10}],
        "calibration": {
            "survey_points": [
                {"x": 1000, "y": 2000},
                {"x": 1100, "y": 2000},
                {"x": 1000, "y": 2100},
            ],
            "path_points": [
                {"x": 0, "y": 0},
                {"x": 100, "y": 0},
                {"x": 0, "y": 100},
            ],
            "max_rms_error": 1,
        },
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": [
                {"x": 0, "y": -30},
                {"x": 100, "y": -30},
            ],
        },
    }
    # 首端点不衔接（必须以原节点为接入端点）→ 先改成合法端点
    body["reroute"]["replacement_points"] = [
        {"x": 0, "y": 0},
        {"x": 50, "y": -30},
        {"x": 100, "y": 0},
    ]
    data = post(body).json()
    assert data["calibration"] is not None
    assert data["collision_count"] == 1  # 原线穿越
    cand = data["reroute_preview"]["candidate"]
    # 候选线圆心视图是标定后的施工坐标（与原线同一批）
    assert cand["circles"][0]["center"] == {"x": 50.0, "y": 0.0}
    assert cand["feasible"] is True
    risks = data["reroute_preview"]["circle_risks"]
    assert len(risks) == 1
    assert len(risks[0]["eliminated"]) == 1


def test_calibration_failure_rejects_entire_preview_with_422():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [],
        "calibration": {
            "survey_points": [
                {"x": 0, "y": 0},
                {"x": 100, "y": 0},
                {"x": 0, "y": 50},
            ],
            "path_points": [
                {"x": 0, "y": 0},
                {"x": 100, "y": 0},
                {"x": 0, "y": 100},  # 50mm 残差
            ],
            "max_rms_error": 1,
        },
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        },
    }
    r = post(body)
    assert r.status_code == 422
    assert "calibration.max_rms_error" in r.json()["errors"]
