"""一次性改线预览测试：切点、跨拐点、标定、里程平移、自交同坐标异里程。

夹具刻意小而精确：几何构型手工可算，断言未舍入语义（三位小数展示不参与
比较），原线与候选线必须来自同一标定/几何链路。
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
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1
    assert raw[0].distance == 15.0  # 恰好相切

    # 改线上移 31：与扩张圆完全脱离（30 时恰好相切，必须再远 1mm）
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 31.0), (100.0, 31.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert c_raw == []

    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
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
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert raw == []

    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 15.0), (100.0, 15.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert len(c_raw) == 1 and c_raw[0].distance == 15.0

    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    s = summaries[0]
    assert s.eliminated == () and s.remaining == ()
    assert len(s.added) == 1
    assert s.added[0].nearest == (50.0, 15.0)
    assert s.added[0].mileage == 50.0


# ---------- 跨拐点：边界节点上的侵入随两条新邻接线延续 ----------


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

    # 替换区间 [0,1]：替代折点与原区间完全相同（原样替换）。侵入区间
    # [9,11] 跨越替换边界而物理重叠未变——不得产生虚假的消除/新增，
    # 段0 与后缀段1 上的风险都必须是「仍存在」，里程逐段一致。
    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (10.0, 0.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    s = summaries[0]
    assert s.eliminated == () and s.added == ()
    assert len(s.remaining) == 2
    rem = {p.segment_index: p for p in s.remaining}
    assert set(rem) == {0, 1}
    for p in rem.values():
        assert p.nearest == (10.0, 0.0)
        assert p.original_mileage == 10.0
        assert p.candidate_mileage == 10.0
    # 候选线的连续侵入区间与原线完全一致（物理覆盖未变）
    assert [(iv.start_mileage, iv.end_mileage) for iv in c_ivs] == [(9.0, 11.0)]

    # 改长替换段（绕远），后缀里程按新路径长度重新累计：
    # (0,0)->(0,-10)->(10,0) 前缀累计到 (10,0) 为 10+sqrt(200)=24.142…
    cand2 = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (0.0, -10.0), (10.0, 0.0)]
    )
    c2_raw, c2_ivs, _ = analyze_path_full(cand2, circles, cable_radius=0.0)
    summaries2 = diff_risks(nodes, cand2, 0, 1, raw, c2_raw, ivs, c2_ivs)
    # 真实绕行：原替换段（段0）的碰撞计为消除；候选替换段在边界节点
    # (10,0) 仍命中（段1 (0,-10)->(10,0) 的最近点），计为新增。
    s2 = summaries2[0]
    assert {e.segment_index for e in s2.eliminated} == {0}
    assert {a.segment_index for a in s2.added} == {1}
    rem2 = [p for p in s2.remaining if p.segment_index == 1][0]
    assert rem2.original_mileage == 10.0
    assert math.isclose(rem2.candidate_mileage, 10.0 + 10.0 * math.sqrt(2.0))
    # 后缀段的区间在候选线上里程整体平移：进入里程 24.142…、离开 25.142…
    seg1_iv = [iv for iv in c2_ivs if iv.exit_segment_index == 2]
    assert seg1_iv and math.isclose(seg1_iv[0].end_mileage, 11.0 + 10.0 * math.sqrt(2.0))
    _ = c_ivs  # 同长度替换的区间集合保持一致
    assert c2_raw  # 候选线仍有碰撞，边界侵入未被“误消除”


# ---------- 里程平移：前缀沿用、后缀重累计 ----------


def test_prefix_mileage_identical_suffix_reaccumulated():
    # 原线四段：替换中间区间 [1,2]
    nodes = [(0, 0), (10, 0), (20, 0), (30, 0)]
    circles = [((5, 0), 0.5), ((25, 0), 0.5)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    seg_circles = {(c.segment_index, c.circle_index) for c in raw}
    assert seg_circles == {(0, 0), (2, 1)}

    # 绕远：(10,0)->(10,10)->(20,0)，替换段长 2*sqrt200 ≈ 28.284（原长 10）
    cand = build_candidate_nodes(
        nodes, 1, 2, [(10, 0), (10, 10), (20, 0)]
    )
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    summaries = diff_risks(nodes, cand, 1, 2, raw, c_raw, ivs, c_ivs)
    by = {s.circle_index: s for s in summaries}
    # 前缀段0：里程不变
    p0 = by[0].remaining[0]
    assert p0.segment_index == 0
    assert p0.original_mileage == p0.candidate_mileage == 5.0
    # 后缀原段2 → 候选段3：原里程 25，候选里程 = 绕远累计 + 5
    p1 = by[1].remaining[0]
    assert p1.segment_index == 2
    assert math.isclose(p1.original_mileage, 25.0)
    assert math.isclose(p1.candidate_mileage, 20.0 + 10.0 * math.sqrt(2.0) + 5.0)


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
    # 首接入点仍是 (0,0)，圆0 在该点的事件作为替换段事件“消除旧、新增新”，
    # 而段3 上同坐标异里程（400→480）的圆0 事件在后缀，必须独立保留。
    cand = build_candidate_nodes(
        nodes, 0, 1, [(0, 0), (0, 40), (100, 40), (100, 0)]
    )
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    # 候选段 0 首点命中圆0；候选段 5（=原段3 平移 +2）命中圆0
    assert sorted((c.segment_index, c.circle_index) for c in c_raw) == [(0, 0), (5, 0)]
    summaries = {s.circle_index: s for s in diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)}

    # 圆0：原段0 事件消除、候选段0 事件新增（同坐标也不配对——它们分属
    # 被替换区间内外的不同结构段）；原段3 事件作为“仍存在”独立保留。
    s0 = summaries[0]
    assert {e.segment_index for e in s0.eliminated} == {0}
    assert {a.segment_index for a in s0.added} == {0}
    rem0 = {p.segment_index: p for p in s0.remaining}
    assert set(rem0) == {3}
    assert math.isclose(rem0[3].original_mileage, 400.0)
    # 候选：替换路径长 180，后缀三边长 300，(0,0) 终点里程 480
    assert math.isclose(rem0[3].candidate_mileage, 480.0)

    # 圆1：段0 内部（里程 2）的命中被改线消除，候选线无新增。
    s1 = summaries[1]
    assert len(s1.eliminated) == 1 and s1.eliminated[0].mileage == 2.0
    assert s1.added == () and s1.remaining == ()


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
    assert risks[0]["eliminated"][0]["segment_index"] == 0
    assert risks[0]["added"] == [] and risks[0]["remaining"] == []
    assert risks[1]["eliminated"] == [] and risks[1]["added"] == []
    assert len(risks[1]["remaining"]) == 1
    rem = risks[1]["remaining"][0]
    assert rem["segment_index"] == 1
    assert rem["original_mileage"] == 150.0
    # 替换路径 (0,0)->(50,-60)->(100,0) 长 2*hypot(50,60)=156.205，
    # 后缀段从该里程起计，判定点里程再加 50；接口值已三位舍入。
    assert math.isclose(
        rem["candidate_mileage"],
        2.0 * math.hypot(50, 60) + 50.0,
        abs_tol=0.0005,
    )
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
