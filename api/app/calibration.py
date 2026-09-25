"""全站仪坐标 → 施工局部坐标的最小二乘刚体标定（未舍入双精度）。

隧道复测时，钻孔中心来自全站仪坐标系，电缆路径使用施工局部坐标；
作业人员先用现场控制点对完成标定，再执行绕孔预检。

只拟合**旋转 + 平移**（保距、不缩放、不镜像）：

1. 两组控制点各自去质心：``s' = s − s̄``，``p' = p − p̄``；
2. 二维最优旋转角有闭式解 ``θ = atan2(B, A)``，其中
   ``A = Σ(s'·p')``、``B = Σ(s'×p')``——它最大化 ``Σ p'·R(θ)s'``，
   结构上恒给出 det = +1 的真旋转，不可能退化为镜像；
3. 平移 ``t = p̄ − R·s̄``；
4. ``rms_error = sqrt(mean ‖R·s_i + t − p_i‖²)``，残差在去质心坐标上
   计算，避免大坐标（全站仪 1e6 mm 量级）下的灾难性抵消。

调用方（schemas）已保证：两组等长、2～20 对、坐标有限、各自不全重合；
此时 A、B 不同时为 0，旋转角唯一确定（共线点集在二维同样有唯一解）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence, Tuple

Point = Tuple[float, float]
# 行主序 ((r00, r01), (r10, r11))
Matrix2 = Tuple[Tuple[float, float], Tuple[float, float]]


@dataclass(frozen=True)
class RigidTransform:
    """survey → path 的最小二乘刚体变换（未舍入双精度）。"""

    rotation: Matrix2       # 2x2 真旋转矩阵（det = +1，无缩放无镜像）
    translation: Point      # 平移向量
    rms_error: float        # 控制点残差均方根（毫米）
    point_count: int        # 控制点对数

    def apply(self, p: Point) -> Point:
        """把 survey 坐标系的点变换到 path 坐标系。"""
        (r00, r01), (r10, r11) = self.rotation
        return (
            r00 * p[0] + r01 * p[1] + self.translation[0],
            r10 * p[0] + r11 * p[1] + self.translation[1],
        )


def fit_rigid_transform(
    survey_points: Sequence[Point],
    path_points: Sequence[Point],
) -> RigidTransform:
    """求 survey → path 的最小二乘刚体变换（仅旋转 + 平移）。

    全程 IEEE-754 双精度、不做任何舍入；质心与协方差累加使用
    :func:`math.fsum`，保证大坐标控制点下的稳定性。
    """
    n = len(survey_points)
    if n != len(path_points) or n < 2:
        raise ValueError("survey_points 与 path_points 必须等长且至少 2 对")

    sx = math.fsum(p[0] for p in survey_points) / n
    sy = math.fsum(p[1] for p in survey_points) / n
    px = math.fsum(p[0] for p in path_points) / n
    py = math.fsum(p[1] for p in path_points) / n

    # A = Σ(s'·p')，B = Σ(s'×p')；去质心后数值量级只取决于点集展宽，
    # 与坐标原点（全站仪大坐标）无关。
    a_acc = []
    b_acc = []
    for (sx_i, sy_i), (px_i, py_i) in zip(survey_points, path_points):
        ux, uy = sx_i - sx, sy_i - sy
        vx, vy = px_i - px, py_i - py
        a_acc.append(ux * vx + uy * vy)
        b_acc.append(ux * vy - uy * vx)
    A = math.fsum(a_acc)
    B = math.fsum(b_acc)
    if A == 0.0 and B == 0.0:
        # 非退化点集（各自不全重合）在二维必然 A、B 不同为 0；兜底防御。
        raise ValueError("控制点构型退化，无法确定唯一刚体变换")

    theta = math.atan2(B, A)
    c = math.cos(theta)
    s = math.sin(theta)
    rotation: Matrix2 = ((c, -s), (s, c))
    translation: Point = (px - (c * sx - s * sy), py - (s * sx + c * sy))

    # 残差在去质心坐标上计算：R·s + t − p = R·s' − p'，避免大坐标抵消。
    residuals = []
    for (sx_i, sy_i), (px_i, py_i) in zip(survey_points, path_points):
        ux, uy = sx_i - sx, sy_i - sy
        vx, vy = px_i - px, py_i - py
        rx = c * ux - s * uy - vx
        ry = s * ux + c * uy - vy
        residuals.append(rx * rx + ry * ry)
    rms_error = math.sqrt(math.fsum(residuals) / n)

    return RigidTransform(
        rotation=rotation,
        translation=translation,
        rms_error=rms_error,
        point_count=n,
    )
