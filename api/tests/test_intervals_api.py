"""/api/precheck 的 intrusion_intervals 接口契约测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
PATH = "/api/precheck"


def post(body):
    return client.post(PATH, json=body)


def test_feasible_response_has_empty_intervals():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 50, "y": 30, "radius": 10}],
    }
    data = post(body).json()
    assert data["feasible"] is True
    assert data["intrusion_intervals"] == []


def test_interval_fields_and_three_decimal_display():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 50, "y": 0, "radius": 10}],
    }
    iv = post(body).json()["intrusion_intervals"]
    assert len(iv) == 1
    one = iv[0]
    assert one["circle_index"] == 0
    assert one["entry_segment_index"] == 0
    assert one["exit_segment_index"] == 0
    assert one["entry"] == {"x": 35.0, "y": 0.0}
    assert one["exit"] == {"x": 65.0, "y": 0.0}
    assert one["start_mileage"] == 35.0
    assert one["end_mileage"] == 65.0
    assert one["length"] == 30.0
    (piece,) = one["pieces"]
    assert piece["segment_index"] == 0 and piece["circle_index"] == 0
    assert piece["entry"] == {"x": 35.0, "y": 0.0}
    assert piece["exit"] == {"x": 65.0, "y": 0.0}
    assert piece["start_mileage"] == 35.0 and piece["end_mileage"] == 65.0
    assert piece["length"] == 30.0


def test_tangent_interval_is_zero_length():
    body = {
        "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 0, "y": 15, "radius": 10}],
    }
    data = post(body).json()
    iv = data["intrusion_intervals"]
    assert len(iv) == 1
    assert iv[0]["length"] == 0.0
    assert iv[0]["start_mileage"] == iv[0]["end_mileage"] == 100.0
    assert iv[0]["entry"] == iv[0]["exit"] == {"x": 0.0, "y": 0.0}


def test_merged_corner_interval_spans_two_segments():
    import math

    R = math.sqrt(2.0) - 1.0  # cable=1 -> 扩张 sqrt2
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        "cable_radius": 1,
        "circles": [{"x": 11, "y": -1, "radius": R}],
    }
    data = post(body).json()
    iv = data["intrusion_intervals"]
    assert len(iv) == 1
    assert iv[0]["entry_segment_index"] == 0
    assert iv[0]["exit_segment_index"] == 1
    assert iv[0]["length"] == 0.0
    assert iv[0]["entry"] == iv[0]["exit"] == {"x": 10.0, "y": 0.0}
    assert [p["segment_index"] for p in iv[0]["pieces"]] == [0, 1]
    # 碰撞明细仍保留两处（语义与排序不变）
    assert [(c["segment_index"], c["circle_index"]) for c in data["collisions"]] == [
        (0, 0),
        (1, 0),
    ]
    assert data["collision_count"] == 2


def test_intervals_ordered_by_start_mileage_then_circle():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [
            {"x": 80, "y": 0, "radius": 5},
            {"x": 20, "y": 0, "radius": 5},
        ],
    }
    data = post(body).json()
    keys = [(iv["start_mileage"], iv["circle_index"]) for iv in data["intrusion_intervals"]]
    assert keys == sorted(keys)
    assert [iv["circle_index"] for iv in data["intrusion_intervals"]] == [1, 0]


def test_collision_fields_unchanged_when_intervals_present():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        "cable_radius": 2,
        "circles": [
            {"x": 10, "y": 15, "radius": 3},
            {"x": 5, "y": 0, "radius": 1},
            {"x": 10, "y": 5, "radius": 1},
        ],
    }
    data = post(body).json()
    assert [(c["segment_index"], c["circle_index"]) for c in data["collisions"]] == [
        (0, 1),
        (1, 0),
        (1, 2),
    ]
    first = data["first_collision"]
    assert (first["segment_index"], first["circle_index"]) == (0, 1)
    assert data["collision_count"] == 3
    # 区间也按起始里程排序，且片段集合与碰撞集合一一对应
    piece_keys = sorted(
        (p["segment_index"], p["circle_index"])
        for iv in data["intrusion_intervals"]
        for p in iv["pieces"]
    )
    assert piece_keys == [(0, 1), (1, 0), (1, 2)]


def test_validation_error_response_has_no_interval_fields():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}],
        "cable_radius": 0,
        "circles": [],
    }
    r = post(body)
    assert r.status_code == 422
    data = r.json()
    assert "intrusion_intervals" not in data
    assert "compound_intrusion_segments" not in data
    assert "feasible" not in data


# ---------- 复合侵入段 compound_intrusion_segments ----------

def test_feasible_response_has_empty_compound_segments():
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 50, "y": 30, "radius": 10}],
    }
    assert post(body).json()["compound_intrusion_segments"] == []


def test_compound_segment_fields_and_three_decimal_display():
    # 直线上两个相互重叠的扩张圈：圈0 圆心 (20,0) r10 -> [10,30]，
    # 圈1 圆心 (30,0) r10 -> [20,40]；复合段 [20,30]，双圈。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 1,
        "circles": [
            {"x": 20, "y": 0, "radius": 9},
            {"x": 30, "y": 0, "radius": 9},
        ],
    }
    segs = post(body).json()["compound_intrusion_segments"]
    assert len(segs) == 1
    one = segs[0]
    assert one["circle_indices"] == [0, 1]
    assert one["start_mileage"] == 20.0 and one["end_mileage"] == 30.0
    assert one["length"] == 10.0
    assert one["start"] == {"x": 20.0, "y": 0.0}
    assert one["end"] == {"x": 30.0, "y": 0.0}
    assert one["start_inclusive"] is True and one["end_inclusive"] is True
    (piece,) = one["pieces"]
    assert piece["segment_index"] == 0
    assert piece["circle_indices"] == [0, 1]
    assert piece["entry"] == {"x": 20.0, "y": 0.0}
    assert piece["exit"] == {"x": 30.0, "y": 0.0}
    assert piece["start_mileage"] == 20.0 and piece["end_mileage"] == 30.0
    assert piece["length"] == 10.0


def test_compound_triple_point_is_independent_zero_segment():
    # A=[0,10]、B=[2,8]、C=[8,12]：三圈点 8 独立，左右两圈段为开端点。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
        "cable_radius": 1,
        "circles": [
            {"x": 0, "y": 0, "radius": 9},
            {"x": 5, "y": 0, "radius": 2},
            {"x": 10, "y": 0, "radius": 1},
        ],
    }
    segs = post(body).json()["compound_intrusion_segments"]
    assert [(s["circle_indices"], s["start_mileage"], s["end_mileage"],
             s["start_inclusive"], s["end_inclusive"]) for s in segs] == [
        ([0, 1], 2.0, 8.0, True, False),
        ([0, 1, 2], 8.0, 8.0, True, True),
        ([0, 2], 8.0, 10.0, False, True),
    ]
    triple = segs[1]
    assert triple["length"] == 0.0
    assert triple["start"] == triple["end"] == {"x": 8.0, "y": 0.0}
    (tp,) = triple["pieces"]
    assert tp["circle_indices"] == [0, 1, 2] and tp["length"] == 0.0


def test_compound_meeting_circles_zero_point_at_node():
    # 圈0 覆盖 [0,10]、圈1 覆盖 [10,20]，恰在拐点里程 10 双圈零长点；
    # 零长点 pieces 覆盖相邻两条原线段。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 20, "y": 0}],
        "cable_radius": 1,
        "circles": [
            {"x": 0, "y": 0, "radius": 9},
            {"x": 20, "y": 0, "radius": 9},
        ],
    }
    segs = post(body).json()["compound_intrusion_segments"]
    assert len(segs) == 1
    one = segs[0]
    assert one["circle_indices"] == [0, 1]
    assert one["start_mileage"] == one["end_mileage"] == 10.0
    assert one["length"] == 0.0
    assert one["start_inclusive"] and one["end_inclusive"]
    assert [p["segment_index"] for p in one["pieces"]] == [0, 1]
    assert all(p["length"] == 0.0 for p in one["pieces"])


def test_compound_49996_50004_display_5000_but_nonzero():
    # 两个 r=0.0004、圆心 (5,0) 的圈：重叠 [4.9996,5.0004]，
    # 展示起止同为 5.000，但不是零长分段。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 20, "y": 0}],
        "cable_radius": 0.0002,
        "circles": [
            {"x": 5, "y": 0, "radius": 0.0002},
            {"x": 5, "y": 0, "radius": 0.0002},
        ],
    }
    segs = post(body).json()["compound_intrusion_segments"]
    assert len(segs) == 1
    one = segs[0]
    assert one["start_mileage"] == 5.0 and one["end_mileage"] == 5.0
    assert one["length"] == 0.001 and one["length"] > 0.0
    assert one["start_inclusive"] and one["end_inclusive"]


def test_old_fields_unchanged_alongside_compound_segments():
    # 旧契约字段逐项保持：feasible/collisions/first_collision/
    # intrusion_intervals 的值与顺序均不因新增字段改变。
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        "cable_radius": 2,
        "circles": [
            {"x": 10, "y": 15, "radius": 3},
            {"x": 5, "y": 0, "radius": 1},
            {"x": 10, "y": 5, "radius": 1},
        ],
    }
    data = post(body).json()
    assert set(data) >= {
        "feasible", "collision_count", "first_collision", "collisions",
        "intrusion_intervals", "compound_intrusion_segments",
    }
    assert [(c["segment_index"], c["circle_index"]) for c in data["collisions"]] == [
        (0, 1),
        (1, 0),
        (1, 2),
    ]
    assert data["collision_count"] == 3
    piece_keys = sorted(
        (p["segment_index"], p["circle_index"])
        for iv in data["intrusion_intervals"]
        for p in iv["pieces"]
    )
    assert piece_keys == [(0, 1), (1, 0), (1, 2)]
    # 单圈区间彼此不同（或不重叠），复合段为空也不影响旧字段
    assert isinstance(data["compound_intrusion_segments"], list)
