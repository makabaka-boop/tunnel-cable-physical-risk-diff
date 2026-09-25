"""改线风险对比的物理占用稳定性验收：四类固定几何样例。

施工员先用与原路径完全相同的候选线核对占用风险、再给同一条直线补
中间定位点时，风险对比必须对相同的物理占用给出稳定结论：

1. **原样替换**：候选替换折线与原区间逐点相同——未变的风险持续存在
   （「仍存在」），不得把同一碰撞同时列为「已消除」和「新增」；
2. **共线补点**：只增加共线中间定位点——同一段持续重叠不得被拆成
   多个新增/消除项，数量与位置不随分段方式变化（插入 1 个或 2 个
   定位点结论一致）；
3. **跨替换边界的连续重叠**：侵入区间跨越替换边界而物理重叠未变——
   不得产生虚假的变化提示；
4. **真实绕行**：几何非等价的改线保持结构语义——真正消除/新增的
   部分才计入变化。

每个样例核对：风险身份（禁入圈/线段/判定位置）、连续侵入区间、
消除/新增/仍存在数量及汇总计数，均无虚假增减。
"""

import math

from fastapi.testclient import TestClient

from app.geometry import analyze_path_full
from app.main import app
from app.reroute import build_candidate_nodes, diff_risks

client = TestClient(app)
PATH = "/api/precheck"


def post(body):
    return client.post(PATH, json=body)


def _risks_by_circle(preview):
    return {c["circle_index"]: c for c in preview["circle_risks"]}


def _assert_summary_consistent(preview):
    """汇总计数恒等于按圈明细之和（无虚假增减的基本不变量）。"""
    assert preview["eliminated_count"] == sum(
        len(c["eliminated"]) for c in preview["circle_risks"]
    )
    assert preview["added_count"] == sum(
        len(c["added"]) for c in preview["circle_risks"]
    )
    assert preview["remaining_count"] == sum(
        len(c["remaining"]) for c in preview["circle_risks"]
    )


def _interval_span(route):
    return [(iv["start_mileage"], iv["end_mileage"]) for iv in route["intrusion_intervals"]]


# ---------- 样例一：原样替换（候选线与原路径完全相同）----------


def _identical_body():
    return {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 200, "y": 0}],
        "cable_radius": 5,
        # 扩张半径 10，与原线在 (50,0) 相切（里程 50，零长侵入区间）
        "circles": [{"x": 50, "y": 10, "radius": 5}],
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        },
    }


def test_identical_replacement_reports_no_change():
    data = post(_identical_body()).json()
    # 原线结论：相切碰撞照常存在
    assert data["collision_count"] == 1
    assert data["collisions"][0]["nearest"] == {"x": 50.0, "y": 0.0}

    preview = data["reroute_preview"]
    cand = preview["candidate"]
    # 候选线与原路径完全相同：结论逐项一致
    assert cand["nodes"] == data["nodes"]
    assert cand["collision_count"] == 1
    assert _interval_span(cand) == _interval_span(data) == [(50.0, 50.0)]

    # 物理占用未变：无消除、无新增，唯一风险持续存在
    assert preview["eliminated_count"] == 0
    assert preview["added_count"] == 0
    assert preview["remaining_count"] == 1
    _assert_summary_consistent(preview)

    risks = _risks_by_circle(preview)
    assert risks[0]["eliminated"] == [] and risks[0]["added"] == []
    (rem,) = risks[0]["remaining"]
    assert rem["segment_index"] == 0
    assert rem["nearest"] == {"x": 50.0, "y": 0.0}
    assert rem["distance"] == 10.0
    assert rem["original_mileage"] == 50.0
    assert rem["candidate_mileage"] == 50.0


def test_identical_replacement_geometry_level():
    nodes = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
    circles = [((50.0, 10.0), 10.0)]  # 扩张半径夹具（cable=0）
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1

    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (100.0, 0.0)])
    assert cand == nodes
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)

    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.eliminated == () and s.added == ()
    assert len(s.remaining) == 1
    rem = s.remaining[0]
    assert rem.nearest == (50.0, 0.0)
    assert rem.original_mileage == rem.candidate_mileage == 50.0


# ---------- 样例二：共线补点（只增加共线中间定位点）----------


def _collinear_body(replacement):
    return {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        # 扩张半径 30，圆心距线 5：侵入区间 [30-√875, 30+√875]，
        # 定位点 (45,0)/(20,0) 都在扩张圈内（持续重叠被加密分段）
        "circles": [{"x": 30, "y": 5, "radius": 25}],
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": replacement,
        },
    }


def test_collinear_point_insertion_keeps_single_persistent_risk():
    data = post(
        _collinear_body([{"x": 0, "y": 0}, {"x": 45, "y": 0}, {"x": 100, "y": 0}])
    ).json()
    assert data["collision_count"] == 1  # 原线：段0 一处碰撞

    preview = data["reroute_preview"]
    cand = preview["candidate"]
    # 候选线因补点被分成两段，各自报碰撞（候选线自身的逐段结论如实给出），
    # 但物理占用与原线完全相同：侵入区间一致。
    assert cand["collision_count"] == 2
    assert _interval_span(cand) == _interval_span(data)

    # 同一段持续重叠不得被拆成多个新增/消除项：恰好一个「仍存在」
    assert preview["eliminated_count"] == 0
    assert preview["added_count"] == 0
    assert preview["remaining_count"] == 1
    _assert_summary_consistent(preview)

    (rem,) = _risks_by_circle(preview)[0]["remaining"]
    assert rem["segment_index"] == 0
    assert rem["nearest"] == {"x": 30.0, "y": 0.0}  # 最近逼近点不随分段变化
    assert rem["original_mileage"] == 30.0
    assert rem["candidate_mileage"] == 30.0


def test_collinear_insertion_count_is_segmentation_invariant():
    """同一条直线补 1 个或 2 个共线定位点，风险结论必须完全一致。"""
    one = post(
        _collinear_body([{"x": 0, "y": 0}, {"x": 45, "y": 0}, {"x": 100, "y": 0}])
    ).json()["reroute_preview"]
    two = post(
        _collinear_body(
            [
                {"x": 0, "y": 0},
                {"x": 20, "y": 0},
                {"x": 45, "y": 0},
                {"x": 100, "y": 0},
            ]
        )
    ).json()["reroute_preview"]
    # 补 2 点：候选线 3 段都在圈内，逐段碰撞 3 处——但差分结论不变
    assert two["candidate"]["collision_count"] == 3
    for preview in (one, two):
        assert preview["eliminated_count"] == 0
        assert preview["added_count"] == 0
        assert preview["remaining_count"] == 1
        _assert_summary_consistent(preview)
    assert one["circle_risks"][0]["remaining"] == two["circle_risks"][0]["remaining"]
    assert _interval_span(one["candidate"]) == _interval_span(two["candidate"])


def test_collinear_insertion_geometry_level_split_point_inside_circle():
    # 圆心 (30,10)、扩张半径 35：插入点 (60,0) 在圈内（距离 √1000≈31.6）
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((30.0, 10.0), 35.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1 and raw[0].nearest == (30.0, 0.0)

    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (60.0, 0.0), (100.0, 0.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    # 候选两段各自报碰撞（插入点在圈内 → 比原线多一个逐段事件）
    assert len(c_raw) == 2

    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    (s,) = summaries
    assert s.eliminated == () and s.added == ()
    assert len(s.remaining) == 1
    rem = s.remaining[0]
    assert rem.nearest == (30.0, 0.0)
    assert rem.original_mileage == rem.candidate_mileage == 30.0
    # 侵入区间物理覆盖一致（合并后同一区间；端点为不同分段上求得的同一
    # 物理根，未舍入值允许 1 ULP 级差异，三位展示完全相同）
    assert len(c_ivs) == len(ivs) == 1
    assert math.isclose(c_ivs[0].start_mileage, ivs[0].start_mileage)
    assert math.isclose(c_ivs[0].end_mileage, ivs[0].end_mileage)


def test_collinear_insertion_geometry_level_tangent_at_inserted_point():
    # 相切点恰好是插入的定位点 (50,0)：原线 1 个零长事件，候选 2 个
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 15.0), 15.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1

    cand = build_candidate_nodes(nodes, 0, 1, [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert len(c_raw) == 2  # 相邻两段在公共端点各报一次相切

    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    (s,) = summaries
    assert s.eliminated == () and s.added == ()
    assert len(s.remaining) == 1
    rem = s.remaining[0]
    assert rem.nearest == (50.0, 0.0)
    assert rem.original_mileage == rem.candidate_mileage == 50.0


def test_collinear_point_removal_is_also_stable():
    # 原线本身带共线中间点 (60,0)，改线把它删掉：同样是等价替换
    nodes = [(0.0, 0.0), (60.0, 0.0), (100.0, 0.0)]
    circles = [((30.0, 10.0), 35.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 2  # 两段都在圈内

    cand = build_candidate_nodes(nodes, 0, 2, [(0.0, 0.0), (100.0, 0.0)])
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert len(c_raw) == 1

    summaries = diff_risks(nodes, cand, 0, 2, raw, c_raw, ivs, c_ivs)
    (s,) = summaries
    assert s.eliminated == () and s.added == ()
    assert len(s.remaining) == 1
    rem = s.remaining[0]
    assert rem.nearest == (30.0, 0.0)
    assert rem.original_mileage == rem.candidate_mileage == 30.0


def test_equivalent_bent_range_with_multiple_circles():
    # L 形替换区间 [0,2] 的共线加密：三个圈各自的风险全部持续存在
    nodes = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (40.0, 20.0)]
    circles = [((10.0, 0.0), 2.0), ((30.0, 20.0), 2.0), ((20.0, 10.0), 2.0)]
    replacement = [
        (0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (20.0, 20.0),
    ]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    cand = build_candidate_nodes(nodes, 0, 2, replacement)
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)

    summaries = diff_risks(nodes, cand, 0, 2, raw, c_raw, ivs, c_ivs)
    by = {s.circle_index: s for s in summaries}
    assert all(s.eliminated == () and s.added == () for s in summaries)
    assert sum(len(s.remaining) for s in summaries) == 3
    # 圈0（替换段）、圈2（替换段）、圈1（后缀段，等长替换里程不变）
    assert by[0].remaining[0].original_mileage == 10.0
    assert by[2].remaining[0].original_mileage == 30.0
    rem1 = by[1].remaining[0]
    assert rem1.segment_index == 2
    assert rem1.original_mileage == rem1.candidate_mileage == 50.0


# ---------- 样例三：跨替换边界的连续重叠 ----------


def _boundary_span_body(replacement):
    return {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 20, "y": 0}],
        "cable_radius": 1,
        # 扩张半径 5，圆心在边界节点 (10,0)：侵入区间 [5,15] 跨越替换边界
        "circles": [{"x": 10, "y": 0, "radius": 4}],
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": replacement,
        },
    }


def test_boundary_spanning_overlap_identical_replacement():
    data = post(
        _boundary_span_body([{"x": 0, "y": 0}, {"x": 10, "y": 0}])
    ).json()
    assert _interval_span(data) == [(5.0, 15.0)]

    preview = data["reroute_preview"]
    # 物理重叠未变：跨边界的连续侵入不得产生虚假的消除/新增
    assert preview["eliminated_count"] == 0
    assert preview["added_count"] == 0
    assert preview["remaining_count"] == 2  # 替换段部分 + 后缀段部分
    _assert_summary_consistent(preview)
    assert _interval_span(preview["candidate"]) == [(5.0, 15.0)]

    rem = _risks_by_circle(preview)[0]["remaining"]
    assert {p["segment_index"] for p in rem} == {0, 1}
    for p in rem:
        assert p["nearest"] == {"x": 10.0, "y": 0.0}
        assert p["original_mileage"] == 10.0
        assert p["candidate_mileage"] == 10.0


def test_boundary_spanning_overlap_with_collinear_insertion():
    # 边界前再补一个共线点 (6,0)（在扩张圈内）：连续重叠跨三段，结论不变
    data = post(
        _boundary_span_body([{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 10, "y": 0}])
    ).json()
    preview = data["reroute_preview"]
    assert preview["candidate"]["collision_count"] == 3  # 候选三段各自命中
    assert preview["eliminated_count"] == 0
    assert preview["added_count"] == 0
    assert preview["remaining_count"] == 2
    _assert_summary_consistent(preview)
    assert _interval_span(preview["candidate"]) == [(5.0, 15.0)]

    rem = _risks_by_circle(preview)[0]["remaining"]
    assert {p["segment_index"] for p in rem} == {0, 1}
    for p in rem:
        assert p["nearest"] == {"x": 10.0, "y": 0.0}
        assert p["original_mileage"] == 10.0
        assert p["candidate_mileage"] == 10.0


# ---------- 样例四：真实绕行（几何非等价，保持结构对比语义）----------


def _detour_body():
    return {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [
            {"x": 50, "y": 10, "radius": 5},   # 与原线在 (50,0) 相切
            {"x": 60, "y": -24, "radius": 5},  # 原线相离（距离 24 > 10）
        ],
        "reroute": {
            "start_index": 0,
            "end_index": 1,
            "replacement_points": [
                {"x": 0, "y": 0},
                {"x": 50, "y": -30},
                {"x": 100, "y": 0},
            ],
        },
    }


def test_real_detour_keeps_structural_diff_semantics():
    data = post(_detour_body()).json()
    assert data["collision_count"] == 1  # 原线：孔0 相切

    preview = data["reroute_preview"]
    cand = preview["candidate"]
    # 候选线：孔0 已绕开，孔1 被替换段 (50,-30)->(100,0) 穿过（垂足即圆心）
    assert cand["collision_count"] == 1
    assert cand["collisions"][0]["circle_index"] == 1
    assert cand["collisions"][0]["segment_index"] == 1

    # 真实变化各计一次：孔0 消除、孔1 新增、无仍存在
    assert preview["eliminated_count"] == 1
    assert preview["added_count"] == 1
    assert preview["remaining_count"] == 0
    _assert_summary_consistent(preview)

    risks = _risks_by_circle(preview)
    (elim,) = risks[0]["eliminated"]
    assert elim["segment_index"] == 0
    assert elim["nearest"] == {"x": 50.0, "y": 0.0}
    assert elim["mileage"] == 50.0
    assert risks[0]["added"] == [] and risks[0]["remaining"] == []

    (add,) = risks[1]["added"]
    assert add["segment_index"] == 1
    assert add["nearest"] == {"x": 60.0, "y": -24.0}
    assert add["distance"] == 0.0
    # 候选里程：首段 √(50²+30²) 全程 + 次段 0.2 处
    assert math.isclose(
        add["mileage"], math.hypot(50, 30) * 1.2, abs_tol=0.0005
    )
    assert risks[1]["eliminated"] == [] and risks[1]["remaining"] == []


def test_detour_geometry_level_not_confused_with_equivalent():
    # 折返（同一直线上来回）不是共线加密：描画的是另一条曲线，保持结构语义
    nodes = [(0.0, 0.0), (100.0, 0.0)]
    circles = [((50.0, 15.0), 15.0)]
    raw, ivs, _ = analyze_path_full(nodes, circles, cable_radius=0.0)
    assert len(raw) == 1

    cand = build_candidate_nodes(
        nodes, 0, 1, [(0.0, 0.0), (60.0, 0.0), (40.0, 0.0), (100.0, 0.0)]
    )
    c_raw, c_ivs, _ = analyze_path_full(cand, circles, cable_radius=0.0)
    assert c_raw  # 折返路径仍穿过切点邻域
    summaries = diff_risks(nodes, cand, 0, 1, raw, c_raw, ivs, c_ivs)
    (s,) = summaries
    # 非等价 ⇒ 结构语义：原替换段消除、候选替换段新增，不归并为「仍存在」
    assert len(s.eliminated) == 1 and s.eliminated[0].segment_index == 0
    assert len(s.added) >= 1
    assert s.remaining == ()
