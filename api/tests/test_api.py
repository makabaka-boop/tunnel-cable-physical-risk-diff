"""/api/precheck 接口测试：穿越/相切/圈内段、排序、三位小数展示与全部字段错误。"""

import math

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PATH = "/api/precheck"


def post(body):
    return client.post(PATH, json=body)


def base_body(**over):
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [],
    }
    body.update(over)
    return body


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_feasible_route():
    r = post(base_body(circles=[{"x": 50, "y": 30, "radius": 10}]))
    assert r.status_code == 200
    data = r.json()
    assert data["feasible"] is True
    assert data["collision_count"] == 0
    assert data["first_collision"] is None
    assert data["collisions"] == []


def test_crossing_returns_nearest_and_expanded():
    r = post(base_body(circles=[{"x": 50, "y": 0, "radius": 10}]))
    data = r.json()
    assert r.status_code == 200
    assert data["feasible"] is False
    assert data["collision_count"] == 1
    first = data["first_collision"]
    assert first["segment_index"] == 0
    assert first["circle_index"] == 0
    assert first["nearest"] == {"x": 50.0, "y": 0.0}
    assert first["distance"] == 0.0
    assert first["expanded_radius"] == 15.0
    # 圆与扩张圈都在响应中，供前端绘制
    assert data["circles"][0]["expanded_radius"] == 15.0


def test_tangent_is_collision():
    """相切（距离恰等于扩张半径）必须判碰撞。"""
    r = post(base_body(circles=[{"x": 50, "y": 15, "radius": 10}]))
    data = r.json()
    assert data["feasible"] is False
    assert data["collisions"][0]["distance"] == 15.0
    assert data["collisions"][0]["nearest"] == {"x": 50.0, "y": 0.0}


def test_response_sorted_by_segment_then_circle():
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}],
        cable_radius=2,
        circles=[
            {"x": 10, "y": 15, "radius": 3},  # seg1/c0
            {"x": 5, "y": 0, "radius": 1},    # seg0/c1
            {"x": 10, "y": 5, "radius": 1},   # seg1/c2
        ],
    )
    data = post(body).json()
    order = [(c["segment_index"], c["circle_index"]) for c in data["collisions"]]
    assert order == [(0, 1), (1, 0), (1, 2)]
    # first_collision 与升序列表首项一致
    first = data["first_collision"]
    assert (first["segment_index"], first["circle_index"]) == (0, 1)


def test_endpoint_clamped_nearest():
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 30, "y": 0}],
        circles=[{"x": 40, "y": 0, "radius": 10}],
        cable_radius=5,
    )
    c = post(body).json()["collisions"][0]
    assert c["nearest"] == {"x": 30.0, "y": 0.0}
    assert c["distance"] == 10.0


def test_segment_entirely_inside_circle():
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 10, "y": 10}],
        circles=[{"x": -50, "y": -50, "radius": 100}],
        cable_radius=5,
    )
    data = post(body).json()
    assert data["collision_count"] == 1
    qx, qy = data["collisions"][0]["nearest"].values()
    assert 0.0 <= qx <= 10.0 and math.isclose(qx, qy)


def test_display_coordinates_rounded_to_three_decimals():
    # 斜线段垂足产生多位小数，输出只展示三位，内部仍双精度判定。
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 3, "y": 1}],
        circles=[{"x": 1, "y": 1, "radius": 1}],
        cable_radius=1,
    )
    c = post(body).json()["collisions"][0]
    # 垂足 (1.2, 0.4)；距 hypot(0.2,0.6)=0.632455... <= 2
    assert c["nearest"]["x"] == 1.2
    assert c["nearest"]["y"] == 0.4
    assert c["distance"] == 0.632
    assert len(str(c["distance"]).split(".")[1]) <= 3


def test_near_miss_not_changed_by_three_decimal_display():
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 1, "y": 0}],
        cable_radius=5,
        circles=[{"x": 3, "y": 10, "radius": 5.1978}],
    )
    data = post(body).json()
    assert data["feasible"] is True
    assert data["collision_count"] == 0
    assert data["first_collision"] is None
    assert data["collisions"] == []


def test_shared_endpoint_returns_collision_for_each_segment():
    body = base_body(
        nodes=[{"x": -10, "y": 0}, {"x": 0, "y": 0}, {"x": 0, "y": 10}],
        cable_radius=1,
        circles=[{"x": 1, "y": -1, "radius": 0.5}],
    )
    data = post(body).json()
    assert data["collision_count"] == 2
    order = [
        (c["segment_index"], c["circle_index"])
        for c in data["collisions"]
    ]
    assert order == [(0, 0), (1, 0)]
    assert (
        data["first_collision"]["segment_index"],
        data["first_collision"]["circle_index"],
    ) == (0, 0)
    assert data["first_collision"]["nearest"] == {"x": 0.0, "y": 0.0}


def test_first_collision_follows_order_not_intrusion_depth():
    body = base_body(
        nodes=[{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 20, "y": 0}],
        cable_radius=1,
        circles=[
            {"x": 5, "y": 2, "radius": 1.1},
            {"x": 15, "y": 0, "radius": 1},
        ],
    )
    data = post(body).json()
    assert data["collision_count"] == 2
    assert [
        (c["segment_index"], c["circle_index"])
        for c in data["collisions"]
    ] == [(0, 0), (1, 1)]
    first = data["first_collision"]
    assert (first["segment_index"], first["circle_index"]) == (0, 0)
    assert first["distance"] == 2.0
    assert first["expanded_radius"] == 2.1


# ---------- 字段级错误：整次预检失败且不产生结论 ----------

def assert_field_error(body, field_fragment):
    r = post(body)
    assert r.status_code == 422, r.text
    data = r.json()
    assert data["ok"] is False
    assert "errors" in data and data["errors"]
    assert any(field_fragment in k for k in data["errors"]), data["errors"]
    # 错误响应不含任何旧结论字段
    assert "feasible" not in data and "collisions" not in data


def test_error_non_finite_coordinate_nan_and_infinity():
    body = base_body(nodes=[{"x": 0, "y": 0}, {"x": "NaN", "y": 0}])
    assert_field_error(body, "nodes[1].x")
    body = base_body(nodes=[{"x": 0, "y": 0}, {"x": "Infinity", "y": 0}])
    assert_field_error(body, "nodes[1].x")
    body = base_body(nodes=[{"x": 0, "y": 0}, {"x": "-Infinity", "y": 0}])
    assert_field_error(body, "nodes[1].x")


def test_error_non_finite_radii():
    body = base_body(circles=[{"x": 0, "y": 0, "radius": "NaN"}])
    assert_field_error(body, "circles[0].radius")
    body = base_body(cable_radius="Infinity")
    assert_field_error(body, "cable_radius")


def test_error_too_few_nodes():
    body = base_body(nodes=[{"x": 0, "y": 0}])
    assert_field_error(body, "nodes")


def test_error_non_integer_millimeter_coordinate():
    body = base_body(nodes=[{"x": 0, "y": 0}, {"x": 12.5, "y": 0}])
    assert_field_error(body, "nodes[1].x")


def test_error_non_positive_radii():
    body = base_body(cable_radius=0)
    assert_field_error(body, "cable_radius")
    body = base_body(cable_radius=-3)
    assert_field_error(body, "cable_radius")
    body = base_body(circles=[{"x": 1, "y": 1, "radius": 0}])
    assert_field_error(body, "circles[0].radius")
    body = base_body(circles=[{"x": 1, "y": 1, "radius": -2}])
    assert_field_error(body, "circles[0].radius")


def test_error_adjacent_duplicate_nodes_is_field_level():
    body = base_body(nodes=[{"x": 5, "y": 5}, {"x": 5, "y": 5}])
    assert_field_error(body, "nodes")


def test_error_boolean_rejected():
    body = base_body(cable_radius=True)
    assert_field_error(body, "cable_radius")


def test_error_wrong_type_string():
    body = base_body(nodes=[{"x": "a", "y": 0}, {"x": 1, "y": 0}])
    assert_field_error(body, "nodes[0].x")


def test_error_unknown_field_rejected():
    body = base_body()
    body["nope"] = 1
    assert_field_error(body, "nope")
