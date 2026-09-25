"""现场标定（calibration）测试：最小二乘刚体变换与 /api/precheck 集成。

拟合结果由一个**独立矩阵计算**交叉核对：显式构造协方差矩阵
H = Σ s'·p'ᵀ，对其做 2x2 SVD（经 HᵀH / H·Hᵀ 的对称特征分解），再按
Kabsch 公式 R = V·diag(1, det(V·Uᵀ))·Uᵀ 组装——与生产实现的
atan2 闭式解走完全不同的计算路径。

覆盖验收点名的全部情形：纯平移、九十度旋转、轻微噪声、超阈值 422、
退化点集 422、非共线镜像数据（不得拟合出镜像）、大坐标稳定性，
以及标定后的相切 / 复合侵入与直接请求同源（三位展示逐项一致）。
"""

import math
import random

import pytest
from fastapi.testclient import TestClient

from app.calibration import fit_rigid_transform
from app.geometry import analyze_path_full
from app.main import app

client = TestClient(app)

PATH = "/api/precheck"


# ---------- 独立矩阵计算预言机：2x2 SVD 的 Kabsch ----------


def _symmetric_eig_2x2(a, b, c):
    """对称矩阵 [[a, b], [b, c]] 的特征对，按特征值降序返回 [(λ, (x, y)), ...]。"""
    tr = (a + c) / 2.0
    disc = math.hypot((a - c) / 2.0, b)
    lam1, lam2 = tr + disc, tr - disc

    def eigvec(lam):
        # (M - λI)v = 0 的两行各给一个候选，取范数较大者避免退化。
        v1 = (b, lam - a)
        v2 = (lam - c, b)
        vx, vy = v1 if (v1[0] ** 2 + v1[1] ** 2) >= (v2[0] ** 2 + v2[1] ** 2) else v2
        nrm = math.hypot(vx, vy)
        if nrm == 0.0:  # 矩阵本就是 λI：任取标准基
            return (1.0, 0.0)
        return (vx / nrm, vy / nrm)

    v1 = eigvec(lam1)
    # 第二特征向量取与第一正交的单位向量（对称矩阵特征向量正交）。
    v2 = (-v1[1], v1[0])
    return [(lam1, v1), (lam2, v2)]


def _mat_vec(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1], m[1][0] * v[0] + m[1][1] * v[1])


def _mat_mul(m1, m2):
    return tuple(
        tuple(m1[i][0] * m2[0][j] + m1[i][1] * m2[1][j] for j in range(2))
        for i in range(2)
    )


def _det(m):
    return m[0][0] * m[1][1] - m[0][1] * m[1][0]


def oracle_fit(survey, path):
    """独立矩阵计算：显式协方差矩阵 + 2x2 SVD 的 Kabsch 刚体拟合。

    返回 (R, t, rms)；仅用于测试核对，与生产闭式解实现路径无关。
    """
    n = len(survey)
    sx = sum(p[0] for p in survey) / n
    sy = sum(p[1] for p in survey) / n
    px = sum(p[0] for p in path) / n
    py = sum(p[1] for p in path) / n

    # H = Σ s'·p'ᵀ（行主序 2x2）
    h = [[0.0, 0.0], [0.0, 0.0]]
    for (ax, ay), (bx, by) in zip(survey, path):
        ux, uy = ax - sx, ay - sy
        vx, vy = bx - px, by - py
        h[0][0] += ux * vx
        h[0][1] += ux * vy
        h[1][0] += uy * vx
        h[1][1] += uy * vy

    # V：HᵀH 的特征向量（降序）；U 的列直接由 H·v₁ 归一化给出（避免
    # σ 除法把秩 1 情形的舍入残影放大成伪向量），第二列取 +90° 正交补——
    # 二维中非退化 H 的最优真旋转由主奇异对 v1→u1 唯一确定。
    ht_h = _mat_mul([[h[0][0], h[1][0]], [h[0][1], h[1][1]]], h)
    (lam1, v1), (lam2, v2) = _symmetric_eig_2x2(ht_h[0][0], ht_h[0][1], ht_h[1][1])
    hv1 = _mat_vec(h, v1)
    nrm1 = math.hypot(hv1[0], hv1[1])
    assert nrm1 > 0.0, "测试数据不应使 H 退化"
    u1 = (hv1[0] / nrm1, hv1[1] / nrm1)
    u2 = (-u1[1], u1[0])
    v2 = (-v1[1], v1[0])

    # R = V·diag(1, det(V·Uᵀ))·Uᵀ（列主序组矩阵再转置相乘）
    V = ((v1[0], v2[0]), (v1[1], v2[1]))
    U = ((u1[0], u2[0]), (u1[1], u2[1]))
    Ut = ((U[0][0], U[1][0]), (U[0][1], U[1][1]))
    d = _det(_mat_mul(V, Ut))
    DUt = ((Ut[0][0], Ut[0][1]), (Ut[1][0] * (1.0 if d >= 0 else -1.0),
                                   Ut[1][1] * (1.0 if d >= 0 else -1.0)))
    R = _mat_mul(V, DUt)

    t = (px - (R[0][0] * sx + R[0][1] * sy), py - (R[1][0] * sx + R[1][1] * sy))
    # 残差在去质心坐标上计算（R·s + t − p = R·s' − p'），避免大坐标抵消。
    ss = 0.0
    for (ax, ay), (bx, by) in zip(survey, path):
        ux, uy = ax - sx, ay - sy
        vx, vy = bx - px, by - py
        rx = R[0][0] * ux + R[0][1] * uy - vx
        ry = R[1][0] * ux + R[1][1] * uy - vy
        ss += rx * rx + ry * ry
    return R, t, math.sqrt(ss / n)


def rot_matrix(theta):
    c, s = math.cos(theta), math.sin(theta)
    return ((c, -s), (s, c))


def apply_rt(R, t, p):
    return (R[0][0] * p[0] + R[0][1] * p[1] + t[0],
            R[1][0] * p[0] + R[1][1] * p[1] + t[1])


def assert_same_transform(actual, survey, path, tol=1e-9, rms_tol=1e-12):
    """生产结果与独立矩阵预言机逐项一致（旋转/平移/残差）。"""
    R_exp, t_exp, rms_exp = oracle_fit(survey, path)
    scale = max(1.0, abs(t_exp[0]), abs(t_exp[1]))
    for i in range(2):
        for j in range(2):
            assert actual.rotation[i][j] == pytest.approx(R_exp[i][j], abs=tol)
    assert actual.translation[0] == pytest.approx(t_exp[0], abs=tol * scale)
    assert actual.translation[1] == pytest.approx(t_exp[1], abs=tol * scale)
    assert actual.rms_error == pytest.approx(rms_exp, rel=1e-6, abs=rms_tol)
    assert actual.point_count == len(survey)


def assert_proper_rotation(R, tol=1e-12):
    """真旋转：正交归一且 det = +1（不缩放、不镜像）。"""
    assert R[0][0] ** 2 + R[1][0] ** 2 == pytest.approx(1.0, abs=tol)
    assert R[0][1] ** 2 + R[1][1] ** 2 == pytest.approx(1.0, abs=tol)
    assert R[0][0] * R[0][1] + R[1][0] * R[1][1] == pytest.approx(0.0, abs=tol)
    assert _det(R) == pytest.approx(1.0, abs=tol)


# ---------- 拟合单元测试：独立矩阵计算核对 ----------


def test_pure_translation_exact():
    survey = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
    t = (-1000.0, -2000.0)
    path = [(x + t[0], y + t[1]) for (x, y) in survey]
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path)
    assert_proper_rotation(tr.rotation)
    assert tr.rotation[0][0] == pytest.approx(1.0, abs=1e-15)
    assert tr.rotation[0][1] == pytest.approx(0.0, abs=1e-15)
    assert tr.translation[0] == pytest.approx(-1000.0, abs=1e-9)
    assert tr.translation[1] == pytest.approx(-2000.0, abs=1e-9)
    assert tr.rms_error < 1e-9


def test_ninety_degree_rotation_exact():
    survey = [(0.0, 0.0), (10.0, 0.0), (0.0, 20.0), (10.0, 20.0), (5.0, 7.0)]
    R90 = rot_matrix(math.pi / 2)
    t = (300.0, -50.0)
    path = [apply_rt(R90, t, p) for p in survey]
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path)
    assert_proper_rotation(tr.rotation)
    # 九十度旋转矩阵 [[0, -1], [1, 0]]
    assert tr.rotation[0][0] == pytest.approx(0.0, abs=1e-12)
    assert tr.rotation[0][1] == pytest.approx(-1.0, abs=1e-12)
    assert tr.rotation[1][0] == pytest.approx(1.0, abs=1e-12)
    assert tr.rotation[1][1] == pytest.approx(0.0, abs=1e-12)
    assert tr.translation[0] == pytest.approx(300.0, abs=1e-9)
    assert tr.translation[1] == pytest.approx(-50.0, abs=1e-9)
    assert tr.rms_error < 1e-9


def test_slight_noise_recovers_transform_and_small_rms():
    rng = random.Random(20260923)
    theta = math.radians(37.0)
    R = rot_matrix(theta)
    t = (1234.5, -678.25)
    survey = [(rng.uniform(-500, 500), rng.uniform(-500, 500)) for _ in range(12)]
    # 轻微噪声（±0.2 mm）：rms 应与小噪声同量级，变换接近真值
    path = []
    for p in survey:
        q = apply_rt(R, t, p)
        path.append((q[0] + rng.uniform(-0.2, 0.2), q[1] + rng.uniform(-0.2, 0.2)))
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path, tol=1e-6)
    assert_proper_rotation(tr.rotation)
    assert 0.0 < tr.rms_error < 0.5
    assert tr.rotation[0][0] == pytest.approx(R[0][0], abs=1e-3)
    assert tr.rotation[1][0] == pytest.approx(R[1][0], abs=1e-3)
    assert tr.translation[0] == pytest.approx(t[0], abs=0.5)
    assert tr.translation[1] == pytest.approx(t[1], abs=0.5)


def test_mirror_data_never_fits_reflection():
    """非共线镜像数据：最佳拟合仍是 det=+1 的真旋转，残差与独立矩阵一致。"""
    survey = [(0.0, 0.0), (10.0, 0.0), (0.0, 5.0), (10.0, 5.0), (3.0, 4.0)]
    # 沿 y 轴镜像（非共线）：任何真旋转都无法精确拟合
    path = [(-x, y) for (x, y) in survey]
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path)
    assert_proper_rotation(tr.rotation)
    # 镜像不可达：残差显著为正，且等于独立矩阵计算的最优真旋转残差
    assert tr.rms_error > 1.0
    # 扰动检验：拟合角是最小二乘最优（两侧扰动代价不减）
    theta = math.atan2(tr.rotation[1][0], tr.rotation[0][0])

    def cost(th):
        R = rot_matrix(th)
        tt = (0.0, 0.0)  # 平移由质心闭合；这里直接整体重算
        sp = [apply_rt(R, tt, p) for p in survey]
        cx = sum(p[0] for p in sp) / len(sp) - sum(p[0] for p in path) / len(path)
        cy = sum(p[1] for p in sp) / len(sp) - sum(p[1] for p in path) / len(path)
        return sum(
            (q[0] - cx - p[0]) ** 2 + (q[1] - cy - p[1]) ** 2
            for q, p in zip(sp, path)
        )

    assert cost(theta - 1e-6) >= cost(theta) - 1e-9
    assert cost(theta + 1e-6) >= cost(theta) - 1e-9


def test_large_coordinates_stability():
    """全站仪大坐标（1e6 mm 量级）：拟合精度不随坐标原点漂移。"""
    base = (2_513_000.0, 583_000.0)
    survey = [
        (base[0] + dx, base[1] + dy)
        for (dx, dy) in [(0, 0), (1200, 300), (400, 1500), (900, 800), (2000, 100)]
    ]
    theta = math.radians(153.0)
    R = rot_matrix(theta)
    t = (-2_000_000.0, -1_000_000.0)  # 映射到施工局部小坐标
    path = [apply_rt(R, t, p) for p in survey]
    tr = fit_rigid_transform(survey, path)
    # 大坐标构造本身带 ~5e-10 mm 舍入底噪，rms 对比按该量级放宽
    assert_same_transform(tr, survey, path, tol=1e-6, rms_tol=1e-9)
    assert_proper_rotation(tr.rotation, tol=1e-9)
    assert tr.rms_error < 1e-6
    # 应用到大坐标钻孔中心：误差仍远小于三位展示粒度
    hole = (base[0] + 640.0, base[1] + 480.0)
    got = tr.apply(hole)
    want = apply_rt(R, t, hole)
    assert got[0] == pytest.approx(want[0], abs=1e-6)
    assert got[1] == pytest.approx(want[1], abs=1e-6)


def test_two_pairs_minimal_and_collinear_supported():
    # 最少 2 对即唯一确定二维刚体变换
    survey = [(0.0, 0.0), (50.0, 0.0)]
    R = rot_matrix(math.radians(-30.0))
    t = (10.0, 20.0)
    path = [apply_rt(R, t, p) for p in survey]
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path)
    assert tr.rms_error < 1e-9
    # 共线（非重合）点集在二维同样有唯一解
    survey = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    path = [apply_rt(R, t, p) for p in survey]
    tr = fit_rigid_transform(survey, path)
    assert_same_transform(tr, survey, path)
    assert tr.rms_error < 1e-9


def test_degenerate_all_coincident_rejected():
    with pytest.raises(ValueError):
        fit_rigid_transform([(5.0, 5.0)] * 3, [(1.0, 2.0)] * 3)
    with pytest.raises(ValueError):
        fit_rigid_transform([(0.0, 0.0), (1.0, 0.0)], [(1.0, 2.0)] * 2)


# ---------- API 集成测试 ----------


def post(body):
    return client.post(PATH, json=body)


def base_body(**over):
    body = {
        "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 5,
        "circles": [{"x": 0, "y": 15, "radius": 10}],
    }
    body.update(over)
    return body


def calib(survey, path, max_rms=1):
    return {
        "survey_points": [{"x": x, "y": y} for (x, y) in survey],
        "path_points": [{"x": x, "y": y} for (x, y) in path],
        "max_rms_error": max_rms,
    }


# 纯平移标定对：survey = path + (1000, 2000)
TRANSLATION = (-1000.0, -2000.0)
TRANSLATION_PAIRS = (
    [(1000, 2000), (1100, 2000), (1000, 2100)],
    [(0, 0), (100, 0), (0, 100)],
)


def test_api_pure_translation_calibration_tangent_matches_direct():
    """标定（纯平移）后的相切结论与直接请求逐项同源（三位展示一致）。"""
    survey, path = TRANSLATION_PAIRS
    # 圆心在全站仪坐标 (1000, 2015) → 施工坐标 (0, 15)，与路径相切
    body = base_body(
        circles=[{"x": 1000, "y": 2015, "radius": 10}],
        calibration=calib(survey, path),
    )
    r = post(body)
    assert r.status_code == 200, r.text
    data = r.json()
    cal = data["calibration"]
    assert cal["point_count"] == 3
    assert cal["rotation"] == [[1.0, 0.0], [0.0, 1.0]]
    assert cal["translation"] == {"x": -1000.0, "y": -2000.0}
    assert cal["rms_error"] == 0.0
    # 相切 → 碰撞；判定位置 (0,0)，距离 = 扩张半径 15
    assert data["feasible"] is False
    first = data["first_collision"]
    assert first["nearest"] == {"x": 0.0, "y": 0.0}
    assert first["distance"] == 15.0
    # 与「圆心直接给施工坐标、不带标定」的请求逐项一致（除 calibration 字段）
    direct = post(base_body()).json()
    for key in data:
        if key != "calibration":
            assert data[key] == direct[key], key
    assert direct["calibration"] is None


def test_api_ninety_degree_rotation_calibration():
    """九十度旋转标定：圆心经旋转 + 平移后命中路径，摘要与独立矩阵一致。"""
    R90 = rot_matrix(math.pi / 2)
    t = (300.0, -50.0)
    survey = [(0, 0), (100, 0), (0, 100), (100, 100)]
    path = [apply_rt(R90, t, p) for p in survey]
    # 想让变换后圆心落在 (0, 15)：survey 坐标为 R90⁻¹·((0,15) − t)
    cp = (0.0, 15.0)
    cs = apply_rt(((R90[0][0], R90[1][0]), (R90[0][1], R90[1][1])),
                  (0.0, 0.0), (cp[0] - t[0], cp[1] - t[1]))
    body = base_body(
        circles=[{"x": round(cs[0]), "y": round(cs[1]), "radius": 10}],
        calibration=calib(survey, path, max_rms=1),
    )
    r = post(body)
    assert r.status_code == 200, r.text
    data = r.json()
    cal = data["calibration"]
    assert cal["rotation"] == [[0.0, -1.0], [1.0, 0.0]]
    assert cal["translation"] == {"x": 300.0, "y": -50.0}
    assert cal["rms_error"] == 0.0
    # cs = R-90·(cp − t) ≈ (65, 300)：圆心恰为整数毫米（浮点尾差经 round 消除）
    assert (round(cs[0]), round(cs[1])) == (65, 300)
    assert data["circles"][0]["center"] == {"x": 0.0, "y": 15.0}
    assert data["first_collision"]["distance"] == 15.0


def test_api_slight_noise_within_threshold():
    rng = random.Random(7)
    R = rot_matrix(math.radians(10.0))
    t = (500.0, 500.0)
    survey = [(0, 0), (100, 0), (0, 100), (100, 100)]
    path = []
    for p in survey:
        q = apply_rt(R, t, p)
        path.append((q[0] + rng.uniform(-0.05, 0.05), q[1] + rng.uniform(-0.05, 0.05)))
    body = base_body(calibration=calib(survey, path, max_rms=1))
    r = post(body)
    assert r.status_code == 200, r.text
    cal = r.json()["calibration"]
    assert 0.0 < cal["rms_error"] < 1.0
    # 与独立矩阵计算的未舍入残差一致到三位展示
    _, _, rms_exp = oracle_fit(survey, path)
    assert cal["rms_error"] == round(rms_exp, 3)


def test_api_rms_over_threshold_422_no_results():
    survey, path = TRANSLATION_PAIRS
    bad_path = [path[0], path[1], (0, 150)]  # 第三对偏差 50mm
    body = base_body(calibration=calib(survey, bad_path, max_rms=1))
    r = post(body)
    assert r.status_code == 422
    data = r.json()
    assert data["ok"] is False
    assert "calibration.max_rms_error" in data["errors"]
    assert "超过阈值" in data["errors"]["calibration.max_rms_error"]
    # 不生成任何碰撞、侵入或复合侵入结果
    assert "feasible" not in data
    assert "collisions" not in data
    assert "intrusion_intervals" not in data
    assert "compound_intrusion_segments" not in data


def test_api_degenerate_point_sets_422():
    survey, path = TRANSLATION_PAIRS
    # survey 全部重合
    body = base_body(calibration=calib([(7, 7)] * 3, path))
    r = post(body)
    assert r.status_code == 422
    assert "calibration.survey_points" in r.json()["errors"]
    # path 全部重合
    body = base_body(calibration=calib(survey, [(3, 4)] * 3))
    r = post(body)
    assert r.status_code == 422
    assert "calibration.path_points" in r.json()["errors"]
    assert "feasible" not in r.json()


def test_api_calibration_structural_errors():
    survey, path = TRANSLATION_PAIRS
    # 对数不足 / 过多
    r = post(base_body(calibration=calib(survey[:1], path[:1])))
    assert r.status_code == 422
    assert "calibration.survey_points" in r.json()["errors"]
    big = [(float(i), float(i % 7)) for i in range(21)]
    r = post(base_body(calibration=calib(big, big)))
    assert r.status_code == 422
    assert "calibration.survey_points" in r.json()["errors"]
    # 两组不等长
    r = post(base_body(calibration=calib(survey, path[:2])))
    assert r.status_code == 422
    assert "calibration.path_points" in r.json()["errors"]
    # 非有限坐标（联合类型错误的字段键带 .float/.int 后缀，按前缀核对）
    r = post(base_body(calibration=calib([(0, 0), ("NaN", 0), (1, 1)], path)))
    assert r.status_code == 422
    assert any(
        k.startswith("calibration.survey_points[1].x") for k in r.json()["errors"]
    )
    # 非正阈值 / 布尔
    r = post(base_body(calibration=calib(survey, path, max_rms=0)))
    assert r.status_code == 422
    assert "calibration.max_rms_error" in r.json()["errors"]
    r = post(base_body(calibration=calib(survey, path, max_rms=True)))
    assert r.status_code == 422
    assert "calibration.max_rms_error" in r.json()["errors"]
    # 多余字段
    bad = calib(survey, path)
    bad["nope"] = 1
    r = post(base_body(calibration=bad))
    assert r.status_code == 422
    assert "calibration.nope" in r.json()["errors"]


def test_api_mirror_data_proper_rotation_only():
    """非共线镜像数据经 API：返回真旋转（det=+1），残差与独立矩阵一致。"""
    survey = [(0, 0), (10, 0), (0, 5), (10, 5), (3, 4)]
    path = [(-x, y) for (x, y) in survey]
    _, _, rms_exp = oracle_fit(survey, path)
    assert rms_exp > 1.0  # 镜像确实不可被真旋转精确拟合
    body = base_body(calibration=calib(survey, path, max_rms=1000))
    r = post(body)
    assert r.status_code == 200, r.text
    cal = r.json()["calibration"]
    (r00, r01), (r10, r11) = cal["rotation"]
    # 三位展示值仍满足真旋转性质（正交、det=+1）
    assert r00 * r11 - r01 * r10 == pytest.approx(1.0, abs=1e-3)
    assert r00 * r01 + r10 * r11 == pytest.approx(0.0, abs=1e-3)
    assert cal["rms_error"] == round(rms_exp, 3)


def test_api_large_coordinates_end_to_end():
    base = (2_513_000, 583_000)
    survey = [
        (base[0] + dx, base[1] + dy)
        for (dx, dy) in [(0, 0), (1200, 300), (400, 1500), (900, 800)]
    ]
    theta = math.radians(153.0)
    R = rot_matrix(theta)
    t = (-2_000_000.0, -1_000_000.0)
    path = [apply_rt(R, t, p) for p in survey]
    # 钻孔中心给整数毫米全站仪坐标；其施工坐标像由真值变换独立算出
    cs = (base[0] + 640, base[1] + 480)
    cp = apply_rt(R, t, cs)
    body = base_body(
        circles=[{"x": cs[0], "y": cs[1], "radius": 10}],
        calibration=calib(survey, path, max_rms=1),
    )
    r = post(body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["calibration"]["rms_error"] == 0.0
    # 大坐标下变换后圆心的三位展示仍与真值像一致
    assert data["circles"][0]["center"] == {
        "x": round(cp[0], 3),
        "y": round(cp[1], 3),
    }
    # 碰撞/区间/复合结论与「同一变换后几何直接跑精确链路」同源
    nodes = [(n["x"], n["y"]) for n in body["nodes"]]
    raw, intervals, compounds = analyze_path_full(nodes, [(cp, 10.0)], 5.0)
    assert data["collision_count"] == len(raw)
    assert len(data["intrusion_intervals"]) == len(intervals)
    assert len(data["compound_intrusion_segments"]) == len(compounds)
    for got, want in zip(data["collisions"], raw):
        assert got["nearest"] == {"x": round(want.nearest[0], 3),
                                  "y": round(want.nearest[1], 3)}
        assert got["distance"] == round(want.distance, 3)


def test_api_compound_intrusion_after_calibration_matches_direct():
    """标定后的复合侵入段与直接请求同源（三位展示逐项一致）。"""
    survey, path = TRANSLATION_PAIRS
    # 施工坐标下两圈 (20,0)/(30,0) r9、电缆 1 → 复合段 [20,30]
    body = {
        "nodes": [{"x": 0, "y": 0}, {"x": 100, "y": 0}],
        "cable_radius": 1,
        "circles": [
            {"x": 1020, "y": 2000, "radius": 9},
            {"x": 1030, "y": 2000, "radius": 9},
        ],
        "calibration": calib(survey, path),
    }
    r = post(body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["compound_intrusion_segments"]) == 1
    seg = data["compound_intrusion_segments"][0]
    assert seg["circle_indices"] == [0, 1]
    assert seg["start_mileage"] == 20.0
    assert seg["end_mileage"] == 30.0
    # 直接请求（圆心给施工坐标、不带标定）：除 calibration 外逐项一致
    direct_body = {k: v for k, v in body.items() if k != "calibration"}
    direct_body["circles"] = [
        {"x": 20, "y": 0, "radius": 9},
        {"x": 30, "y": 0, "radius": 9},
    ]
    direct = post(direct_body).json()
    for key in data:
        if key != "calibration":
            assert data[key] == direct[key], key


def test_api_omit_calibration_backward_compatible():
    """省略 calibration：响应逐项与旧版一致，calibration 为 null。"""
    data = post(base_body()).json()
    assert data["calibration"] is None
    assert data["feasible"] is False
    assert data["first_collision"]["nearest"] == {"x": 0.0, "y": 0.0}
    assert len(data["intrusion_intervals"]) == 1
