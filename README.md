# 隧道电缆绕孔预检器

隧道电缆折线绕开钻孔作业面时，即使折线本身没有穿过孔位，**电缆半径仍可能侵入安全圈**；
而“相切”边界最容易产生分歧。本工具对每条闭线段做统一的二维检测：

1. 对每条**闭线段**（含两个端点），求孔圆心到该线段的**唯一最近点**及距离；
   - 垂足在线段内部 → 垂足；垂足落在段外 → 裁剪到较近端点。
2. 扩张半径 = **禁入圈半径 + 电缆半径**。
3. 当 `距离 ≤ 扩张半径` 即判碰撞——**相切（恰好相等）也判碰撞**，避免边界争议；
   最近点统一作为「判定位置」；端点碰撞或整段位于圈内时规则完全相同。
4. 多处碰撞按 **(线段下标, 禁入圈输入顺序)** 升序返回，首个为突出展示项。
5. 计算全程使用 IEEE-754 双精度；响应中的展示坐标/距离四舍五入至三位小数。

## 连续侵入区间（可施工定位）

除逐线段的最近点结论外，接口还返回 `intrusion_intervals`：把每条线段与扩张圆的
**闭集交集**（一维参数区间）映射为累计里程，并在满足严格条件时**跨拐点合并**，
让作业人员沿路径顺序看到每个连续侵入区间的：

- 所属 `circle_index`（禁入圈）；
- 进入线段 `entry_segment_index` / 离开线段 `exit_segment_index`；
- 进入点 `entry` / 离开点 `exit` 坐标（未舍入双精度计算，展示三位小数）；
- `start_mileage` / `end_mileage` 起止累计里程、`length` 侵入长度；
- `pieces`：区间覆盖到的各线段片段（进入/离开点、里程、长度），前端据此
  在 SVG 上高亮对应折线（零长相切点渲染为圆点）。

区间规则：

- 闭集交集覆盖**相切零长点**（`t0 == t1`）、**端点进入**与**整段位于圈内**（`[0,1]`）；
- 跨拐点仅在**同一 `circle_index`**、前段参数严格到 `t1 == 1`（确实到达公共节点）
  且后段从 `t0 == 0`（从该节点继续）时合并；
- **自交路径的同坐标异里程、不同禁入圈、仅三位舍入后相等的边界一律保持独立**；
  合并前后覆盖的片段集合与总长度一致；
- 区间按 **(起始里程, 禁入圈输入序)** 升序。

## 复合侵入段（多圈同时命中）

`compound_intrusion_segments` 给出沿累计里程**同时落入至少两个扩张圈**的**最大分段**，
供作业人员判断“一处要同时躲开几个孔”。几何层在**舍入前**对每个圈的精确闭区间
做一次事件扫描，并在每个事件里程严格区分三种活动集合：

- **左邻域** `(x-ε, x)`、**点态** `{x}`（含仅在该点相切的圈）、**右邻域** `(x, x+ε)`；
- 里程轴被切成点单元格 `{x}` 与开区间单元格 `(x_i, x_{i+1})`；活动圈集合
  （≥2 个圈）相同的连续单元格合并为最大分段。

因此边界拓扑完全按闭集语义保留：

- `A=[0,5]`、`B=[5,10]` 会在里程 5 保留一个**双圈零长点**（两个开邻域各只单圈）；
- `A=[0,10]`、`B=[2,8]`、`C=[8,12]` 在 8 处产生**独立三圈点**，且不并入左右两个
  两圈段的闭端点——左段 `end_inclusive=false`、右段 `start_inclusive=false`。

每个分段详情含：

- 升序 `circle_indices`（恒为该段活动圈集合）；
- 起止累计里程与坐标（未舍入双精度计算，展示三位小数）；
- `start_inclusive` / `end_inclusive`（开邻域起点为 `false`）；
- `pieces`：**按原线段切分**的片段（拐点零长点在相邻两段各得一个同坐标零长片段），
  前端 SVG 与详情列表共用同一 `compound_intrusion_segments` 数组逐段高亮，
  正长片段画红色虚线、多圈零长点画醒目圆环。

**仅显示值相等（如 4.9996 / 5.0004 同显 5.000）、实际端点不同、自交路径同坐标
异里程，一律不合并。** 分段按 **(未舍入起始里程, circle_indices 字典序)** 排列；
可敷设（无任何重叠命中）时为空数组。

扫描阶段时间 **O(P log P + R)**、空间 **O(P + A + R)**（P 为精确片段数、
A 为峰值活动圈数、R 为输出规模），事件以整数位掩码增量维护，不在每个事件重查
全部圈；与空间粗筛叠加后仍满足 20000 线段 × 2000 局部稀疏禁入圈 3 秒验收。

性能：后端不是无条件对所有「线段 × 禁入圈」解二次方程，而是先做
**x 扫描线 + y 包围盒的空间粗筛**（只允许多报、不漏候选，容差仅吸收数值相切的
舍入噪声），只对粗筛命中的组合精确求交。20000 条线段、2000 个与路径包围盒
交错但局部稀疏的禁入圈在 3 秒内完成，且输出顺序确定。

前端对每次“新提交 / 字段校验失败 / 请求失败 / 重置”立即作废旧结论（含旧区间），
并用请求序号忽略在途旧响应——**乱序返回的旧响应不能恢复旧区间**。

## 现场标定（可选 calibration）

隧道复测时，**钻孔中心来自全站仪坐标系，而电缆路径使用施工局部坐标**。
请求可携带可选 `calibration`，先以现场控制点完成标定再执行绕孔预检：

- `survey_points` / `path_points`：**2～20 对**控制点（全站仪坐标 ↔ 施工局部坐标），
  两组**等长**、坐标**有限**且各自**不能全部重合**；`max_rms_error` 为**正数**残差阈值；
- 后端以**未舍入双精度**求 survey → path 的**最小二乘保距刚体变换**——
  只允许**旋转 + 平移**（去质心后 `θ = atan2(Σs'×p', Σs'·p')` 闭式解，
  结构上 det = +1，**不得缩放、不得镜像**），并返回
  `rotation`（2x2 行主序）、`translation`、`rms_error`（展示值三位小数）；
- **输入退化或残差超阈值**时返回定位明确的 422
  （`calibration.survey_points` / `calibration.path_points` /
  `calibration.max_rms_error`），**不生成任何碰撞、侵入或复合侵入结果**；
- 标定成功后**只变换禁入圈圆心**，半径、电缆路径与电缆半径维持现有语义，
  再复用同一粗筛与精确几何链路——所有排序、区间拓扑和三位展示都来自
  同一批未舍入结果（质心/协方差用 `math.fsum` 累加，残差在去质心坐标上
  计算，全站仪 1e6 mm 大坐标下稳定）；
- **省略 `calibration` 时请求与响应逐项兼容**（响应中 `calibration` 为 `null`）。

前端提供成对控制点编辑（2～20 对，可增删）与残差、旋转矩阵、平移摘要；
校验失败、请求失败、重置与乱序响应同样使标定结论作废旧。

```json
{
  "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
  "cable_radius": 5,
  "circles": [{"x": 1000, "y": 2015, "radius": 10}],
  "calibration": {
    "survey_points": [{"x": 1000, "y": 2000}, {"x": 1100, "y": 2000}, {"x": 1000, "y": 2100}],
    "path_points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 0, "y": 100}],
    "max_rms_error": 1
  }
}
```

响应新增（其余字段语义不变）：

```json
{
  "calibration": {
    "point_count": 3,
    "rotation": [[1.0, 0.0], [0.0, 1.0]],
    "translation": {"x": -1000.0, "y": -2000.0},
    "rms_error": 0.0
  }
}
```

## 一次性改线预览（reroute）

新增钻孔需要把一小段既定电缆折线改线时，请求可携带可选 `reroute`，在**不改动
原方案响应**的前提下，用同一套标定与精确几何规则同时计算**原线**与**候选线**：

- `start_index` / `end_index`：被替换的连续节点区间（含两端，
  `0 ≤ start < end < 节点数`）；
- `replacement_points`：接入两端的替代折点（2～若干个，整数毫米），**首点必须与
  `nodes[start_index]` 精确重合、末点必须与 `nodes[end_index]` 精确重合**
  （不容差），替代折线内部不允许相邻重合折点；
- 端点不衔接、区间非法、拼接产生零长段、坐标非整数等一律返回定位明确的 422
  （`reroute.start_index` / `reroute.end_index` /
  `reroute.replacement_points[i].x|y`），**不生成原线或候选线任何结论**。

候选折线 = 原前缀 + 替代折点 + 原后缀。里程规则：

- **未改动前缀沿用原里程**（逐段相同）；
- **改线后缀按新路径长度重新累计**（候选段下标 = 原下标 + 段数平移量），
  “仍存在”风险同时给出两条线各自的未舍入里程，差值即里程平移；
- 全部比较只使用未舍入双精度与线段结构键 `(原线段下标, 禁入圈)`，
  **不依赖三位小数展示值，也不按坐标配对**——自交路径上同坐标、不同里程的
  事件位于不同原线段，永远不会被误认为同一事件。

响应顶层仍是原线结论（字段与旧版逐项一致），新增 `reroute_preview`：

- `range`：替换区间回显；
- `candidate`：候选线的完整预检结论（字段语义与顶层一致，含候选折线 nodes、
  circles 与全部碰撞/侵入区间/复合侵入段）；
- `circle_risks`：按禁入圈归类的风险变化——
  - `eliminated`：原线被替换区间段上的碰撞（含区间端点上的碰撞是否保留，
    取决于候选接入段是否仍命中）；
  - `added`：候选替换段上的碰撞；
  - `remaining`：前缀/后缀同一条原线段上仍存在的碰撞（携带
    `original_mileage` / `candidate_mileage`）；
- 另有 `eliminated_count` / `added_count` / `remaining_count` 汇总。

```json
{
  "reroute": {
    "start_index": 0, "end_index": 1,
    "replacement_points": [
      {"x": -100, "y": 0}, {"x": 0, "y": -30}, {"x": 100, "y": 0}
    ]
  }
}
```

前端在**同一版本快照**中切换「原线（已保存方案）/ 候选改线」：SVG 路径、
禁入圈、碰撞标记、连续侵入区间、复合侵入段与风险摘要都与各自结论严格同源
（切换只换数据源，不重新请求）。预览请求使用**独立请求序号**：

- 取消预览、改线字段校验失败、预览 422/网络失败、乱序返回的旧预览响应，
  都只影响候选视图，**绝不覆盖已保存的原方案**；
- 未生成预览（取消/失败）时切换条不出现，画面只能显示原方案；
- 不提交 `reroute` 时旧接口与交互保持原样（响应 `reroute_preview` 为 `null`）。


## 技术栈

- 后端：Python 3.12 + FastAPI + Pydantic v2（`api/`）
- 前端：TypeScript + React 18 + Vite（`web/`），SVG 绘制路径、禁入圈与判定位置
- 测试：pytest（穿越/端点/相切/圈内线段/排序/字段错误/连续侵入区间合并与独立边界/
  粗筛不漏候选/20000×2000 稀疏性能/标定独立矩阵核对与退化、镜像、大坐标/
  改线夹具：切点消除与新增、跨拐点、前缀沿用与后缀里程平移、自交同坐标异里程、
  端点衔接 422、标定同源）、
  Vitest + Testing Library
  （录入校验、**真实 HTTP 请求**、首个碰撞高亮、侵入区间明细与 SVG 片段高亮、
  拐点双相切合并、重叠禁入圈、标定摘要与 422 作废、乱序响应作废与旧结论清除、
  改线原线/候选线快照切换、风险摘要、取消/校验失败不覆盖原方案、预览乱序作废）

## 目录

```
api/                       FastAPI 服务
  app/geometry.py          自实现二维：最近点 + 碰撞检测 + 闭集区间/跨拐点合并/空间粗筛（双精度）
  app/calibration.py       survey→path 最小二乘刚体标定（仅旋转+平移，双精度，fsum 累加）
  app/reroute.py           一次性改线：候选折线拼接/端点与拼接校验/消除·新增·仍存在风险差分
  app/schemas.py           Pydantic 模型与字段级校验（含 calibration、reroute 结构校验）
  app/main.py              /api/precheck、/api/health、422 字段错误
  tests/                   pytest（碰撞、区间语义、合并边界、性能与错误、
                           标定：独立矩阵计算核对纯平移/九十度旋转/噪声/超阈值/
                           退化/镜像/大坐标，标定后相切与复合侵入同源性，
                           test_reroute：切点/跨拐点/里程平移/自交/422/标定同源）
web/                       React + Vite
  src/lib/validation.ts    前端同构校验（字段键与后端一致，含 calibration.*）
  src/components/Scene.tsx SVG 场景（路径/禁入圈/扩张圈/判定位置/侵入区间片段）
  src/test/App.real.test.tsx  对真实运行 API 的 Vitest 验收（含标定全流程）
Dockerfile.verify          验收镜像（Python 3.12 + Node 20）
docker-compose.yml         web / api / verify（一次性）
scripts/verify.sh          验收编排
```

## 一键启动

```bash
docker compose up --build
# Web:  http://localhost:${WEB_PORT:-8080}
# API:  http://localhost:${API_PORT:-8000}/api/health
```

覆盖宿主端口：

```bash
WEB_PORT=9090 API_PORT=9000 docker compose up --build
# 或复制 .env.example 为 .env 后修改
```

## 一次性验收

```bash
docker compose build api verify
docker compose --profile verify run --rm verify
```

`verify` 服务会：等待 API 健康 → 运行后端 pytest → 用 Vitest 对 Compose 中
**真实运行的 API** 发起请求并核对渲染/高亮 → 执行前端生产构建；全部成功退出码为 0。

## 本地开发（无 Docker）

```bash
# 后端（容器固定 3.12；3.11 亦可运行测试）
pip install -r api/requirements-dev.txt
cd api && PYTHONPATH=. uvicorn app.main:app --reload

# 前端（Vite 已配置 /api 代理到 localhost:8000）
cd web && npm install && npm run dev
npm test                                            # Vitest（需先启动 API）
VITE_API_BASE=http://127.0.0.1:8000 npm test
```

## 接口

`POST /api/precheck`

```json
{
  "nodes": [{"x": -100, "y": 0}, {"x": 100, "y": 0}],
  "cable_radius": 5,
  "circles": [{"x": 0, "y": 15, "radius": 10}]
}
```

- 路径节点与圆心坐标为**整数毫米**；电缆/禁入圈半径为**正数**。
- 上例圆心距折线 15mm，扩张半径 = 10 + 5 = 15，属**相切 → 碰撞**，
  判定位置（最近点）为 `(0, 0)`。
- 合法且无碰撞返回 `feasible: true`，页面显示「✅ 可敷设」。

响应（碰撞时）：

```json
{
  "feasible": false,
  "collision_count": 1,
  "first_collision": {
    "segment_index": 0, "circle_index": 0,
    "nearest": {"x": 0.0, "y": 0.0},
    "distance": 15.0, "expanded_radius": 15.0
  },
  "collisions": [ ],
  "intrusion_intervals": [
    {
      "circle_index": 0,
      "entry_segment_index": 0, "exit_segment_index": 0,
      "entry": {"x": 0.0, "y": 0.0}, "exit": {"x": 0.0, "y": 0.0},
      "start_mileage": 100.0, "end_mileage": 100.0, "length": 0.0,
      "pieces": [ ]
    }
  ]
}
```

`feasible / collision_count / first_collision / collisions` 的含义与排序保持不变；
`intrusion_intervals` 按 (起始里程, 禁入圈输入序) 排列，可敷设时为空数组。
`compound_intrusion_segments` 为新增字段（旧字段逐项兼容），按
(未舍入起始里程, circle_indices 字典序) 排列，无多圈重叠时为空数组；示例：

```json
{
  "circle_indices": [0, 1],
  "start_mileage": 20.0, "end_mileage": 30.0,
  "start": {"x": 20.0, "y": 0.0}, "end": {"x": 30.0, "y": 0.0},
  "start_inclusive": true, "end_inclusive": true,
  "length": 10.0,
  "pieces": [
    {"segment_index": 0, "circle_indices": [0, 1],
     "entry": {"x": 20.0, "y": 0.0}, "exit": {"x": 30.0, "y": 0.0},
     "start_mileage": 20.0, "end_mileage": 30.0, "length": 10.0}
  ]
}
```

所有交点、里程与合并判断使用未舍入双精度，仅响应展示值按三位小数处理。

### 字段级错误（HTTP 422）

非有限数值（NaN/Infinity 或其字符串形式）、节点不足 2 个、非正半径、相邻重复节点、
非整数毫米坐标、布尔值、多余字段等，均返回字段级错误且**不产生任何预检结论**；
前端会清除旧结论并标红对应字段：

```json
{ "ok": false, "errors": { "nodes[1].x": "必须是有限数值（不能是 NaN 或无穷）" } }
```
