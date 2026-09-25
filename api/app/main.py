"""FastAPI 应用：隧道电缆绕孔预检（可选全站仪 → 施工坐标现场标定、一次性改线预览）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .calibration import fit_rigid_transform
from .geometry import (
    Collision,
    CompoundIntrusionSegment,
    IntrusionInterval,
    analyze_path_full,
)
from .reroute import (
    CircleRiskSummary,
    PersistedRisk,
    RiskEvent,
    build_candidate_nodes,
    diff_risks,
    validate_reroute,
)
from .schemas import (
    CalibrationOut,
    CandidateRouteOut,
    CircleOut,
    CircleRiskOut,
    CollisionOut,
    CompoundIntrusionSegmentOut,
    CompoundPieceOut,
    IntervalPieceOut,
    IntrusionIntervalOut,
    PersistedRiskOut,
    PointOut,
    PrecheckRequest,
    PrecheckResponse,
    ReroutePreviewOut,
    RerouteRangeOut,
    RiskEventOut,
)

app = FastAPI(title="隧道电缆绕孔预检器", version="1.0.0")

# 前端通过 Vite dev server / 浏览器直连时需要 CORS；
# 容器内 nginx 同源代理 /api 也不受影响。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def round3(v: float) -> float:
    """四舍五入至三位小数（仅用于展示输出，计算内部始终双精度）。"""
    return round(float(v) + 0.0, 3)


def _loc_to_field(loc: tuple) -> str:
    """把 Pydantic 的错误定位转成前端可索引的字段键。

    例：('body', 'nodes', 0, 'x') -> 'nodes[0].x'
    """
    parts = [p for p in loc if p != "body"]
    key = ""
    for p in parts:
        if isinstance(p, int) or (isinstance(p, str) and p.isdigit()):
            key += f"[{p}]"
        else:
            key = f"{key}.{p}" if key else str(p)
    return key or "_root"


_MESSAGES = {
    "finite_number": "必须是有限数值（不能是 NaN 或无穷）",
    "greater_than": "半径必须为正数",
    "missing": "该字段缺失",
    "string_type": "必须是数值，不能是字符串",
    "int_from_float": "坐标必须是整数毫米",
    "bool_type": "不能是布尔值",
    "too_short": "列表项数量不足",
    "too_long": "列表项数量过多",
}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """转换为统一的字段级错误结构。任何校验失败都不产生预检结论。"""
    errors: Dict[str, str] = {}
    for err in exc.errors():
        field = _loc_to_field(tuple(err.get("loc", ())))
        etype = err.get("type", "")
        msg = err.get("msg", "输入不合法")
        if etype in _MESSAGES:
            text = _MESSAGES[etype]
        elif etype == "value_error":
            text = msg.replace("Value error, ", "").strip()
        else:
            text = msg
        errors.setdefault(field, text)
    return JSONResponse(
        status_code=422,
        content={"ok": False, "errors": errors},
    )


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


def _circle_views(
    circles: List[Tuple[Any, float]], cable_radius: float
) -> List[CircleOut]:
    return [
        CircleOut(
            center=PointOut(x=round3(center[0]), y=round3(center[1])),
            radius=round3(r),
            expanded_radius=round3(r + cable_radius),
        )
        for (center, r) in circles
    ]


def _collision_views(
    raw: List[Collision],
    circles: List[Tuple[Any, float]],
    cable_radius: float,
    rpt,
) -> List[CollisionOut]:
    return [
        CollisionOut(
            segment_index=c.segment_index,
            circle_index=c.circle_index,
            nearest=rpt(c.nearest),
            distance=round3(c.distance),
            expanded_radius=round3(c.expanded_radius),
            circle_center=rpt(circles[c.circle_index][0]),
            circle_radius=round3(circles[c.circle_index][1]),
            cable_radius=round3(cable_radius),
        )
        for c in raw
    ]


def _interval_views(intervals: List[IntrusionInterval], rpt) -> List[IntrusionIntervalOut]:
    return [
        IntrusionIntervalOut(
            circle_index=iv.circle_index,
            entry_segment_index=iv.entry_segment_index,
            exit_segment_index=iv.exit_segment_index,
            entry=rpt(iv.entry_point),
            exit=rpt(iv.exit_point),
            start_mileage=round3(iv.start_mileage),
            end_mileage=round3(iv.end_mileage),
            length=round3(iv.length),
            pieces=[
                IntervalPieceOut(
                    segment_index=p.segment_index,
                    circle_index=p.circle_index,
                    entry=rpt(p.entry_point),
                    exit=rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in iv.pieces
            ],
        )
        for iv in intervals
    ]


def _compound_views(
    compounds: List[CompoundIntrusionSegment], rpt
) -> List[CompoundIntrusionSegmentOut]:
    return [
        CompoundIntrusionSegmentOut(
            circle_indices=list(seg.circle_indices),
            start_mileage=round3(seg.start_mileage),
            end_mileage=round3(seg.end_mileage),
            start=rpt(seg.start_point),
            end=rpt(seg.end_point),
            start_inclusive=seg.start_inclusive,
            end_inclusive=seg.end_inclusive,
            length=round3(seg.length),
            pieces=[
                CompoundPieceOut(
                    segment_index=p.segment_index,
                    circle_indices=list(p.circle_indices),
                    entry=rpt(p.entry_point),
                    exit=rpt(p.exit_point),
                    start_mileage=round3(p.start_mileage),
                    end_mileage=round3(p.end_mileage),
                    length=round3(p.length),
                )
                for p in seg.pieces
            ],
        )
        for seg in compounds
    ]


def _route_view(
    nodes: List[Tuple[float, float]],
    circles: List[Tuple[Tuple[float, float], float]],
    cable_radius: float,
    raw: List[Collision],
    intervals: List[IntrusionInterval],
    compounds: List[CompoundIntrusionSegment],
    rpt,
) -> Dict[str, Any]:
    """一套几何结论 → 序列化字段（原线/候选线完全同源，避免两条链路漂移）。"""
    collision_views = _collision_views(raw, circles, cable_radius, rpt)
    return {
        "feasible": len(collision_views) == 0,
        "cable_radius": round3(cable_radius),
        "nodes": [PointOut(x=round3(x), y=round3(y)) for (x, y) in nodes],
        "circles": _circle_views(circles, cable_radius),
        "collision_count": len(collision_views),
        "first_collision": collision_views[0] if collision_views else None,
        "collisions": collision_views,
        "intrusion_intervals": _interval_views(intervals, rpt),
        "compound_intrusion_segments": _compound_views(compounds, rpt),
    }


def _risk_event_view(ev: RiskEvent, rpt) -> RiskEventOut:
    return RiskEventOut(
        segment_index=ev.segment_index,
        circle_index=ev.circle_index,
        nearest=rpt(ev.nearest),
        distance=round3(ev.distance),
        expanded_radius=round3(ev.expanded_radius),
        mileage=round3(ev.mileage),
    )


def _persisted_risk_view(pr: PersistedRisk, rpt) -> PersistedRiskOut:
    return PersistedRiskOut(
        segment_index=pr.segment_index,
        circle_index=pr.circle_index,
        nearest=rpt(pr.nearest),
        distance=round3(pr.distance),
        expanded_radius=round3(pr.expanded_radius),
        original_mileage=round3(pr.original_mileage),
        candidate_mileage=round3(pr.candidate_mileage),
    )


def _circle_risk_views(summaries: List[CircleRiskSummary], rpt) -> List[CircleRiskOut]:
    views: List[CircleRiskOut] = []
    for s in summaries:
        views.append(
            CircleRiskOut(
                circle_index=s.circle_index,
                eliminated=[_risk_event_view(e, rpt) for e in s.eliminated],
                added=[_risk_event_view(e, rpt) for e in s.added],
                remaining=[_persisted_risk_view(p, rpt) for p in s.remaining],
            )
        )
    return views


@app.post("/api/precheck", response_model=PrecheckResponse)
def precheck(payload: PrecheckRequest) -> PrecheckResponse:
    nodes = [(n.x, n.y) for n in payload.nodes]
    circles = [((c.x, c.y), c.radius) for c in payload.circles]

    # 可选现场标定：先以未舍入双精度拟合 survey → path 刚体变换，
    # 残差超阈值直接 422（不生成任何碰撞/侵入/复合侵入结论）；
    # 通过后只变换禁入圈圆心，半径、电缆路径与电缆半径语义不变，
    # 后续粗筛与精确几何链路完全复用，排序/区间拓扑/三位展示同源。
    calibration_view: Optional[CalibrationOut] = None
    if payload.calibration is not None:
        survey = [(p.x, p.y) for p in payload.calibration.survey_points]
        path = [(p.x, p.y) for p in payload.calibration.path_points]
        transform = fit_rigid_transform(survey, path)
        if transform.rms_error > payload.calibration.max_rms_error:
            raise RequestValidationError(
                errors=[
                    {
                        "loc": ("body", "calibration", "max_rms_error"),
                        "msg": (
                            "Value error, 标定残差 RMS "
                            f"{transform.rms_error:.6g} mm 超过阈值 "
                            f"{payload.calibration.max_rms_error:.6g} mm"
                        ),
                        "type": "value_error",
                    }
                ]
            )
        circles = [(transform.apply(center), r) for (center, r) in circles]
        (r00, r01), (r10, r11) = transform.rotation
        calibration_view = CalibrationOut(
            point_count=transform.point_count,
            rotation=[
                [round3(r00), round3(r01)],
                [round3(r10), round3(r11)],
            ],
            translation=PointOut(
                x=round3(transform.translation[0]),
                y=round3(transform.translation[1]),
            ),
            rms_error=round3(transform.rms_error),
        )

    # 可选一次性改线预览：区间/端点/拼接的跨字段校验失败同样以字段级 422
    # 拒绝，且不产生原线或候选线的任何结论（与“校验失败不覆盖原方案”一致）。
    reroute_spec: Optional[Tuple[int, int, List[Tuple[float, float]]]] = None
    if payload.reroute is not None:
        replacement = [(p.x, p.y) for p in payload.reroute.replacement_points]
        reroute_errors = validate_reroute(
            nodes=nodes,
            start_index=payload.reroute.start_index,
            end_index=payload.reroute.end_index,
            replacement_points=replacement,
        )
        if reroute_errors:
            raise RequestValidationError(
                errors=[
                    {"loc": ("body",) + loc, "msg": f"Value error, {msg}", "type": "value_error"}
                    for loc, msg in reroute_errors
                ]
            )
        reroute_spec = (
            payload.reroute.start_index,
            payload.reroute.end_index,
            replacement,
        )

    def rpt(p) -> PointOut:
        return PointOut(x=round3(p[0]), y=round3(p[1]))

    # ---- 原线（已保存方案）：永远照常计算，结论在响应顶层 ----
    raw: List[Collision]
    intervals: List[IntrusionInterval]
    compounds: List[CompoundIntrusionSegment]
    raw, intervals, compounds = analyze_path_full(
        nodes=nodes,
        circles=circles,
        cable_radius=payload.cable_radius,
    )
    original_fields = _route_view(
        nodes, circles, payload.cable_radius, raw, intervals, compounds, rpt
    )

    # ---- 候选线：同一标定后的禁入圈、同一套精确几何规则再算一次 ----
    preview_view: Optional[ReroutePreviewOut] = None
    if reroute_spec is not None:
        start_index, end_index, replacement = reroute_spec
        candidate_nodes = build_candidate_nodes(
            nodes, start_index, end_index, replacement
        )
        c_raw, c_intervals, c_compounds = analyze_path_full(
            nodes=candidate_nodes,
            circles=circles,
            cable_radius=payload.cable_radius,
        )
        candidate_fields = _route_view(
            candidate_nodes,
            circles,
            payload.cable_radius,
            c_raw,
            c_intervals,
            c_compounds,
            rpt,
        )
        candidate_view = CandidateRouteOut(**candidate_fields)

        summaries = diff_risks(
            original_nodes=nodes,
            candidate_nodes=candidate_nodes,
            start_index=start_index,
            end_index=end_index,
            original_collisions=raw,
            candidate_collisions=c_raw,
            original_intervals=intervals,
            candidate_intervals=c_intervals,
        )
        circle_risk_views = _circle_risk_views(summaries, rpt)
        eliminated_count = sum(len(s.eliminated) for s in summaries)
        added_count = sum(len(s.added) for s in summaries)
        remaining_count = sum(len(s.remaining) for s in summaries)
        preview_view = ReroutePreviewOut(
            range=RerouteRangeOut(
                start_index=start_index,
                end_index=end_index,
                replacement_point_count=len(replacement),
            ),
            candidate=candidate_view,
            circle_risks=circle_risk_views,
            eliminated_count=eliminated_count,
            added_count=added_count,
            remaining_count=remaining_count,
        )

    return PrecheckResponse(
        **original_fields,
        calibration=calibration_view,
        reroute_preview=preview_view,
    )
