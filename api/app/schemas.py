"""Pydantic 输入/输出模型与字段级校验。

所有非法情形都产生携带字段定位的错误，交由异常处理器转为 422：
非有限数值、节点不足、非正半径、相邻重复节点、非整数毫米坐标、布尔值等。

可选 `calibration`（现场控制点标定）：2～20 对 survey（全站仪）/ path
（施工局部）控制点与正数 max_rms_error；两组等长、坐标有限且各自不能
全部重合。省略时请求/响应与旧版逐项兼容。
"""

from __future__ import annotations

import math
from typing import Annotated, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic.functional_validators import BeforeValidator
from pydantic.types import StrictFloat, StrictInt


def _strict_mm_int(v):
    """整数毫米：只接受 int（拒绝 bool）；float/字符串一律拒绝。

    这样 JSON 中的 "NaN"/"Infinity"/"1.5" 等都无法借宽松解析混入。
    """
    if isinstance(v, bool):
        raise ValueError("必须是整数毫米数值，不能是布尔值")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("必须是有限数值（不能是 NaN 或无穷）")
        raise ValueError("坐标必须是整数毫米，不接受小数")
    raise ValueError("必须是整数毫米数值")


def _positive_finite(v):
    """正数半径：接受有限的 int/float，拒绝布尔、NaN、Infinity 与非正数值。"""
    if isinstance(v, bool):
        raise ValueError("半径必须是正数，不能是布尔值")
    if isinstance(v, int):
        f = float(v)
    elif isinstance(v, float):
        f = v
    else:
        # 字符串等类型：交给 StrictFloat/StrictInt 核心报类型错误。
        return v
    if not math.isfinite(f):
        raise ValueError("半径必须是有限正数（不能是 NaN 或无穷）")
    if f <= 0:
        raise ValueError("半径必须为正数")
    return f


# 整数毫米坐标；外层 StrictInt 确保 "NaN" 之类字符串不被宽松解析。
MmInt = Annotated[StrictInt, BeforeValidator(_strict_mm_int)]
# 严格非负整数下标（拒绝布尔、浮点、字符串；取值范围在端点处校验）。
StrictIndex = Annotated[StrictInt, BeforeValidator(_strict_mm_int)]
# 正数半径（可以是小数毫米）；StrictFloat/StrictInt 拒绝字符串。
PositiveRadius = Annotated[
    StrictFloat | StrictInt, BeforeValidator(_positive_finite)
]


def _finite_number(v):
    """有限数值：接受有限的 int/float，拒绝布尔、NaN、Infinity 与字符串。"""
    if isinstance(v, bool):
        raise ValueError("必须是有限数值，不能是布尔值")
    if isinstance(v, (int, float)):
        f = float(v)
        if not math.isfinite(f):
            raise ValueError("必须是有限数值（不能是 NaN 或无穷）")
        return f
    # 字符串等类型：交给 StrictFloat/StrictInt 核心报类型错误。
    return v


def _positive_number(v):
    """正数阈值：接受有限的 int/float，拒绝布尔、NaN、Infinity 与非正数值。"""
    if isinstance(v, bool):
        raise ValueError("必须是正数，不能是布尔值")
    if isinstance(v, (int, float)):
        f = float(v)
        if not math.isfinite(f):
            raise ValueError("必须是有限正数（不能是 NaN 或无穷）")
        if f <= 0:
            raise ValueError("必须是正数")
        return f
    return v


# 控制点坐标：有限数值（全站仪坐标可以带小数，不限整数毫米）。
FiniteNumber = Annotated[StrictFloat | StrictInt, BeforeValidator(_finite_number)]
# 正数阈值（max_rms_error）。
PositiveNumber = Annotated[StrictFloat | StrictInt, BeforeValidator(_positive_number)]


class StrictPointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: MmInt
    y: MmInt


class StrictCircleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: MmInt
    y: MmInt
    radius: PositiveRadius


def _all_coincident(points: List["CalibrationPointIn"]) -> bool:
    """控制点是否全部重合（退化构型无法确定刚体变换）。"""
    first = points[0]
    return all(p.x == first.x and p.y == first.y for p in points)


class CalibrationPointIn(BaseModel):
    """单个控制点：有限数值坐标（全站仪/施工坐标均可带小数）。"""

    model_config = ConfigDict(extra="forbid")

    x: FiniteNumber
    y: FiniteNumber


class CalibrationIn(BaseModel):
    """可选现场标定：survey（全站仪）→ path（施工局部）控制点对。

    2～20 对、两组等长、坐标有限且各自不能全部重合；max_rms_error
    为正数阈值，拟合残差超过它时整次请求以 422 拒绝（不产生任何结论）。
    """

    model_config = ConfigDict(extra="forbid")

    survey_points: Annotated[
        List[CalibrationPointIn], Field(min_length=2, max_length=20)
    ]
    path_points: Annotated[
        List[CalibrationPointIn], Field(min_length=2, max_length=20)
    ]
    max_rms_error: PositiveNumber

    @field_validator("survey_points")
    @classmethod
    def _survey_not_all_coincident(cls, points: List[CalibrationPointIn]):
        if _all_coincident(points):
            raise ValueError("survey_points 全部重合，无法确定刚体变换")
        return points

    @field_validator("path_points")
    @classmethod
    def _path_matches_survey(cls, points: List[CalibrationPointIn], info):
        survey = info.data.get("survey_points")
        if survey is not None and len(points) != len(survey):
            raise ValueError(
                f"survey_points 与 path_points 必须等长（{len(survey)} ≠ {len(points)}）"
            )
        if _all_coincident(points):
            raise ValueError("path_points 全部重合，无法确定刚体变换")
        return points


class RerouteIn(BaseModel):
    """一次性改线预览：替换连续节点区间 ``nodes[start_index..end_index]``。

    ``replacement_points`` 为接入两端的替代折点（含区间两端）：首点必须与
    ``nodes[start_index]`` 精确重合、末点必须与 ``nodes[end_index]`` 精确
    重合；跨字段的区间/端点/拼接校验在端点处完成。坐标与主路径同为整数
    毫米，替代折线内部相邻折点不得重合。
    """

    model_config = ConfigDict(extra="forbid")

    start_index: StrictIndex
    end_index: StrictIndex
    replacement_points: Annotated[List[StrictPointIn], Field(min_length=2)]


class PrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: Annotated[List[StrictPointIn], Field(min_length=2)]
    cable_radius: PositiveRadius
    circles: List[StrictCircleIn]
    # 可选现场标定；省略时行为与旧版完全一致。
    calibration: Optional[CalibrationIn] = None
    # 可选一次性改线预览；省略时请求/响应与旧版逐项兼容。
    reroute: Optional[RerouteIn] = None

    @field_validator("nodes")
    @classmethod
    def _reject_adjacent_duplicate_nodes(cls, nodes: List[StrictPointIn]):
        # 相邻重复节点会产生退化（零长）线段；错误挂在 nodes 字段上。
        for i in range(len(nodes) - 1):
            a = nodes[i]
            b = nodes[i + 1]
            if a.x == b.x and a.y == b.y:
                raise ValueError(
                    f"相邻节点 #{i} 与 #{i + 1} 完全重合，禁止相邻重复节点"
                )
        return nodes


class PointOut(BaseModel):
    x: float
    y: float


class CollisionOut(BaseModel):
    segment_index: int
    circle_index: int
    nearest: PointOut          # 判定位置（展示坐标，四舍五入至三位小数）
    distance: float            # 圆心到判定位置的距离（三位小数）
    expanded_radius: float     # 禁入圈半径 + 电缆半径（三位小数）
    circle_center: PointOut
    circle_radius: float
    cable_radius: float


class CircleOut(BaseModel):
    center: PointOut
    radius: float            # 展示用（三位小数）
    expanded_radius: float   # radius + cable_radius（三位小数）


class IntervalPieceOut(BaseModel):
    """区间内单条线段上的侵入片段（展示值，三位小数）。"""

    segment_index: int
    circle_index: int
    entry: PointOut             # 进入点
    exit: PointOut              # 离开点（与进入点相同即相切零长点）
    start_mileage: float        # 进入点累计里程
    end_mileage: float          # 离开点累计里程
    length: float               # 片段侵入长度（零长相切为 0）


class IntrusionIntervalOut(BaseModel):
    """可施工定位的连续侵入区间（展示值，三位小数）。"""

    circle_index: int
    entry_segment_index: int    # 进入线段
    exit_segment_index: int     # 离开线段
    entry: PointOut
    exit: PointOut
    start_mileage: float        # 区间起点累计里程
    end_mileage: float          # 区间终点累计里程
    length: float               # 区间侵入长度（end - start；零长相切为 0）
    pieces: List[IntervalPieceOut]  # 覆盖到的线段片段（路径顺序），供 SVG 高亮


class CompoundPieceOut(BaseModel):
    """复合侵入段在一条原线段上的片段（展示值，三位小数）。"""

    segment_index: int
    circle_indices: List[int]   # 该片段上同时活动的全部禁入圈（升序）
    entry: PointOut
    exit: PointOut
    start_mileage: float
    end_mileage: float
    length: float               # 零长点（拐点相切）为 0


class CompoundIntrusionSegmentOut(BaseModel):
    """同时落入至少两个扩张圈的最大连续分段（展示值，三位小数）。"""

    circle_indices: List[int]        # 恒为该分段的活动圈集合（升序）
    start_mileage: float             # 起始累计里程（未舍入排序，展示三位）
    end_mileage: float               # 终止累计里程
    start: PointOut                  # 起点坐标
    end: PointOut                    # 终点坐标
    start_inclusive: bool            # 是否包含起始里程（开邻域起点为 false）
    end_inclusive: bool              # 是否包含终止里程
    length: float                    # end_mileage - start_mileage（零长点为 0）
    pieces: List[CompoundPieceOut]   # 按原线段切分的片段，供 SVG 高亮


class CalibrationOut(BaseModel):
    """标定结果摘要（展示值，三位小数；变换本身以未舍入双精度应用）。"""

    point_count: int             # 控制点对数
    rotation: List[List[float]]  # 2x2 行主序真旋转矩阵（det = +1）
    translation: PointOut        # 平移向量
    rms_error: float             # 控制点残差均方根（毫米）


class RiskEventOut(BaseModel):
    """消除/新增风险事件（展示值，三位小数；比较内部为未舍入双精度）。"""

    segment_index: int           # 该结论所属路径自身的线段下标
    circle_index: int
    nearest: PointOut            # 判定位置
    distance: float
    expanded_radius: float
    mileage: float               # 判定位置在该路径上的累计里程


class PersistedRiskOut(BaseModel):
    """仍存在的风险：同一条原线段上原线/候选线各自的里程（展示三位）。

    前缀段两里程相等；后缀段里程按新路径长度重新累计，差值即里程平移。
    """

    segment_index: int           # 原线（= 候选前缀）线段下标
    circle_index: int
    nearest: PointOut
    distance: float
    expanded_radius: float
    original_mileage: float
    candidate_mileage: float


class CircleRiskOut(BaseModel):
    """单个禁入圈的改线风险变化摘要（空列表表示该类无变化）。"""

    circle_index: int
    eliminated: List[RiskEventOut] = Field(default_factory=list)
    added: List[RiskEventOut] = Field(default_factory=list)
    remaining: List[PersistedRiskOut] = Field(default_factory=list)


class RerouteRangeOut(BaseModel):
    """本次预览替换的连续节点区间（回显，供前端区间详情定位）。"""

    start_index: int
    end_index: int
    replacement_point_count: int


class CandidateRouteOut(BaseModel):
    """候选改线的完整预检结论（与 /api/precheck 顶层字段同语义）。

    circles 与原线共用同一批（标定后的）禁入圈视图；nodes 为拼接后的
    候选折线；其余碰撞/区间/复合侵入均由同一套未舍入几何链路计算。
    """

    feasible: bool
    cable_radius: float
    nodes: List[PointOut]
    circles: List[CircleOut]
    collision_count: int
    first_collision: CollisionOut | None = None
    collisions: List[CollisionOut]
    intrusion_intervals: List[IntrusionIntervalOut] = Field(default_factory=list)
    compound_intrusion_segments: List[CompoundIntrusionSegmentOut] = Field(
        default_factory=list
    )


class ReroutePreviewOut(BaseModel):
    """一次性改线预览：区间回显、候选线全套结论与按圈归类的风险摘要。"""

    range: RerouteRangeOut
    candidate: CandidateRouteOut
    circle_risks: List[CircleRiskOut]
    eliminated_count: int
    added_count: int
    remaining_count: int


class PrecheckResponse(BaseModel):
    feasible: bool
    cable_radius: float      # 展示用（三位小数）
    nodes: List[PointOut]
    circles: List[CircleOut]
    collision_count: int
    first_collision: CollisionOut | None = None
    collisions: List[CollisionOut]
    intrusion_intervals: List[IntrusionIntervalOut] = Field(default_factory=list)
    compound_intrusion_segments: List[CompoundIntrusionSegmentOut] = Field(
        default_factory=list
    )
    # 请求带 calibration 时给出标定摘要；省略时为 null（旧字段逐项兼容）。
    calibration: Optional[CalibrationOut] = None
    # 请求带 reroute 时给一次性改线预览（原线结论仍在本对象顶层）；
    # 省略时为 null，旧接口逐项兼容。
    reroute_preview: Optional[ReroutePreviewOut] = None
