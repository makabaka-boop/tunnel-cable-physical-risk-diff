import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { API_BASE } from "../api/client";

/**
 * 验收约定：这些用例通过真实 fetch 调用运行中的 API（由 verify 服务启动）。
 * VITE_APIBASE 指向 API 根地址（默认 http://localhost:8000）。
 * 若 API 不可达则失败而不是跳过——保证“真实请求”被实际核对。
 */
const BASE = API_BASE || "http://localhost:8000";

beforeAll(async () => {
  const res = await fetch(`${BASE}/api/health`);
  if (!res.ok) throw new Error(`验收要求真实 API 在线：${BASE} 不可达`);
});

async function submit() {
  await userEvent.click(screen.getByTestId("submit"));
}

describe("真实请求 + 录入 + 高亮（App）", () => {
  it("录入节点/禁入圈顺序并真实请求：相切场景判定不可敷设且突出首个碰撞", async () => {
    render(<App />);

    // 默认示例：节点 (-100,0)->(100,0)，电缆半径 5，孔圆心 (0,15) 半径 10
    // 圆心距折线 15 == 扩张半径 15 -> 相切即碰撞
    await submit();

    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );
    // SVG 中出现扩张圈、禁入圈、首个碰撞星标与判定连线
    expect(screen.getByTestId("forbidden-circle-0")).toBeInTheDocument();
    expect(screen.getByTestId("expanded-circle-0")).toBeInTheDocument();
    expect(screen.getByTestId("first-collision-marker")).toBeInTheDocument();
    expect(screen.getByTestId("collision-0-0")).toBeInTheDocument();

    const detail = screen.getByTestId("first-collision-detail").textContent ?? "";
    expect(detail).toContain("线段 #0");
    expect(detail).toContain("禁入圈 #0");
    expect(detail).toContain("(0, 0)"); // 最近点为 (0,0)
    expect(detail).toContain("15"); // 距离=扩张半径
  });

  it("调整为可敷设路线后显示“可敷设”，并清除旧碰撞高亮", async () => {
    render(<App />);
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "30"); // 距离30 > 15
    await submit();

    await waitFor(() => expect(screen.getByTestId("banner-ok")).toBeInTheDocument());
    expect(screen.getByTestId("banner-ok").textContent).toContain("可敷设");
    expect(screen.queryByTestId("first-collision-marker")).not.toBeInTheDocument();
  });

  it("多处碰撞：突出首个，并列出其余（按线段/禁入圈升序）", async () => {
    render(<App />);
    // 增加一个节点，形成两段；再增加第二个孔
    await userEvent.click(screen.getByTestId("add-node"));
    const n2x = screen.getByTestId("node-2-x");
    const n2y = screen.getByTestId("node-2-y");
    await userEvent.clear(n2x);
    await userEvent.type(n2x, "100");
    await userEvent.clear(n2y);
    await userEvent.type(n2y, "100");

    await userEvent.click(screen.getByTestId("add-circle"));
    const c1x = screen.getByTestId("circle-1-x");
    const c1y = screen.getByTestId("circle-1-y");
    const c1r = screen.getByTestId("circle-1-radius");
    await userEvent.clear(c1x);
    await userEvent.type(c1x, "100");
    await userEvent.clear(c1y);
    await userEvent.type(c1y, "50"); // 线段1 穿过
    await userEvent.clear(c1r);
    await userEvent.type(c1r, "10");

    await submit();

    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );
    // 首个：线段0 × 孔0；其余列表含 线段1 × 孔1
    const rest = screen.getByTestId("rest-collisions");
    expect(rest.textContent).toContain("线段 #1");
    expect(rest.textContent).toContain("禁入圈 #1");
    // 两个碰撞标记都在图上，首个为星标
    expect(screen.getByTestId("collision-0-0")).toBeInTheDocument();
    expect(screen.getByTestId("collision-1-1")).toBeInTheDocument();
  });

  it("非法输入（非正半径）不发请求，返回字段级错误并清除旧结论", async () => {
    render(<App />);
    // 先得到一个碰撞结论
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    // 改成非法半径
    await userEvent.clear(screen.getByTestId("cable-radius"));
    await userEvent.type(screen.getByTestId("cable-radius"), "0");
    await submit();

    await waitFor(() => expect(screen.getByTestId("banner-error")).toBeInTheDocument());
    expect(screen.getByTestId("err-cable_radius").textContent).toContain("正数");
    // 旧结论被清除：画布与碰撞横幅都不存在
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
  });

  it("相邻重复节点触发字段错误（录入顺序被保留在列表中）", async () => {
    render(<App />);
    await userEvent.clear(screen.getByTestId("node-1-x"));
    await userEvent.type(screen.getByTestId("node-1-x"), "-100");
    // 节点1 变为 (-100,0) 与节点0 重复
    await submit();
    await waitFor(() => expect(screen.getByTestId("banner-error")).toBeInTheDocument());
    const nodeList = screen.getByTestId("node-list");
    // 两行录入仍按输入顺序存在
    expect(within(nodeList).getAllByText(/#\d/).length).toBeGreaterThanOrEqual(2);
  });

  it("连续侵入区间：相切场景展示零长区间并在 SVG 高亮同一点位", async () => {
    render(<App />);
    // 默认示例：相切于 (0,0)，累计里程 100
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    const panel = await screen.findByTestId("interval-panel");
    const rows = within(panel).getAllByRole("listitem");
    expect(rows.length).toBe(1);
    const row = rows[0].textContent ?? "";
    expect(row).toContain("禁入圈 #0");
    expect(row).toContain("相切零长点");
    expect(row).toContain("100"); // 起止累计里程 = 100
    // SVG 上的零长高亮圆点（与详情同一数组渲染）
    const mark = document.querySelector('[data-testid="intrusion-c0-s0"]');
    expect(mark).toBeInTheDocument();
    expect(mark?.tagName.toLowerCase()).toBe("circle");
  });

  it("拐点双相切合并：一个跨两段的零长区间，SVG 高亮两段片段", async () => {
    render(<App />);
    // 路径 (0,0)->(10,0)->(10,10)，圆 (11,-1)，扩张半径 sqrt2
    // 电缆半径 1，孔半径 sqrt2-1 ≈ 0.414
    await userEvent.clear(screen.getByTestId("cable-radius"));
    await userEvent.type(screen.getByTestId("cable-radius"), "1");
    await userEvent.clear(screen.getByTestId("node-0-x"));
    await userEvent.type(screen.getByTestId("node-0-x"), "0");
    const n1x = screen.getByTestId("node-1-x");
    const n1y = screen.getByTestId("node-1-y");
    await userEvent.clear(n1x);
    await userEvent.type(n1x, "10");
    await userEvent.clear(n1y);
    await userEvent.type(n1y, "0");
    await userEvent.click(screen.getByTestId("add-node"));
    await userEvent.clear(screen.getByTestId("node-2-x"));
    await userEvent.type(screen.getByTestId("node-2-x"), "10");
    await userEvent.clear(screen.getByTestId("node-2-y"));
    await userEvent.type(screen.getByTestId("node-2-y"), "10");

    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "11");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "-1");
    const radiusInput = screen.getByTestId("circle-0-radius");
    await userEvent.clear(radiusInput);
    await userEvent.click(radiusInput);
    await userEvent.keyboard("{Control>}a{/Control}");
    // sqrt(2)-1 的 17 位有效数字；扩张半径 = 1 + 它，双精度恰为 sqrt2
    await userEvent.type(radiusInput, "0.41421356237309515");
    expect((radiusInput as HTMLInputElement).value).toBe("0.41421356237309515");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("interval-panel")).toBeInTheDocument(),
    );
    const rows = within(screen.getByTestId("interval-panel")).getAllByRole("listitem");
    // 双相切合并为一个区间
    expect(rows.length).toBe(1);
    const text = rows[0].textContent ?? "";
    expect(text).toContain("进入线段 #0");
    expect(text).toContain("离开线段 #1");
    expect(text).toContain("相切零长点");
    // 两段片段都高亮，且点/折线在公共拐点 (10,0)
    expect(document.querySelector('[data-testid="intrusion-c0-s0"]')).toBeInTheDocument();
    expect(document.querySelector('[data-testid="intrusion-c0-s1"]')).toBeInTheDocument();
  });

  it("重叠禁入圈：各自产生独立区间并以不同颜色片段高亮", async () => {
    render(<App />);
    // 直线 0..100；两个圈都在中部覆盖路径
    await userEvent.click(screen.getByTestId("add-circle"));
    const c1x = screen.getByTestId("circle-1-x");
    const c1y = screen.getByTestId("circle-1-y");
    const c1r = screen.getByTestId("circle-1-radius");
    await userEvent.clear(c1x);
    await userEvent.type(c1x, "0");
    await userEvent.clear(c1y);
    await userEvent.type(c1y, "8"); // 距线 8，扩张 15 -> 侵入
    await userEvent.clear(c1r);
    await userEvent.type(c1r, "10");
    // 圈0 也放到 (0,8)（与圈1 完全重叠，不同 circle_index）
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "8");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("interval-panel")).toBeInTheDocument(),
    );
    const rows = within(screen.getByTestId("interval-panel")).getAllByRole("listitem");
    expect(rows.length).toBe(2);
    // 两个片段（同线段、不同圈）都高亮
    const p0 = document.querySelector('[data-testid="intrusion-c0-s0"]');
    const p1 = document.querySelector('[data-testid="intrusion-c1-s0"]');
    expect(p0).toBeInTheDocument();
    expect(p1).toBeInTheDocument();
    expect(p0?.getAttribute("stroke")).not.toBe(p1?.getAttribute("stroke"));
  });

  it("近相切未命中：双精度有真实间隙（三位展示相同）仍判可敷设且无侵入区间", async () => {
    render(<App />);
    // 路径 (0,0)->(1,0)；圆 (3,10) r5.1978 cable5 => 扩张 10.1978，
    // 圆心距线 sqrt(104)=10.198039… > R；二者三位展示均为 10.198。
    await userEvent.clear(screen.getByTestId("node-0-x"));
    await userEvent.type(screen.getByTestId("node-0-x"), "0");
    await userEvent.clear(screen.getByTestId("node-1-x"));
    await userEvent.type(screen.getByTestId("node-1-x"), "1");
    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "3");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "10");
    await userEvent.clear(screen.getByTestId("circle-0-radius"));
    await userEvent.type(screen.getByTestId("circle-0-radius"), "5.1978");

    await submit();
    await waitFor(() => expect(screen.getByTestId("banner-ok")).toBeInTheDocument());
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    // 可敷设：高亮层存在但没有任何片段子元素
    const layer = document.querySelector('[data-testid="intrusion-layer"]');
    expect(layer?.children.length).toBe(0);
  });

  it("复合侵入段：重叠扩张圈产出同数组详情与 SVG 高亮（含圈序/里程/片段）", async () => {
    render(<App />);
    // 单段 0->100，电缆半径 1；两个 r=9 的圈扩张到 10：
    // 圈0 (20,0) -> 路径侵入 [10,30]，圈1 (30,0) -> [20,40]，
    // 复合段为 [20,30]，circle_indices=[0,1]。
    await userEvent.clear(screen.getByTestId("cable-radius"));
    await userEvent.type(screen.getByTestId("cable-radius"), "1");
    await userEvent.clear(screen.getByTestId("node-0-x"));
    await userEvent.type(screen.getByTestId("node-0-x"), "0");
    await userEvent.clear(screen.getByTestId("node-1-x"));
    await userEvent.type(screen.getByTestId("node-1-x"), "100");
    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "20");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "0");
    await userEvent.clear(screen.getByTestId("circle-0-radius"));
    await userEvent.type(screen.getByTestId("circle-0-radius"), "9");

    await userEvent.click(screen.getByTestId("add-circle"));
    await userEvent.clear(screen.getByTestId("circle-1-x"));
    await userEvent.type(screen.getByTestId("circle-1-x"), "30");
    await userEvent.clear(screen.getByTestId("circle-1-y"));
    await userEvent.type(screen.getByTestId("circle-1-y"), "0");
    await userEvent.clear(screen.getByTestId("circle-1-radius"));
    await userEvent.type(screen.getByTestId("circle-1-radius"), "9");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("compound-panel")).toBeInTheDocument(),
    );

    const panel = screen.getByTestId("compound-list");
    // 只取顶层分段行：pieces 内嵌的 <ol><li> 同样是 listitem，不能计入
    const rows = within(panel).getAllByRole("listitem").filter(
      (li) => li.parentElement === panel,
    );
    expect(rows.length).toBe(1);
    const row = rows[0];
    // 圈序升序且同时列出
    expect(row.textContent).toContain("禁入圈 #0");
    expect(row.textContent).toContain("禁入圈 #1");
    expect(row.textContent).toContain("复合侵入 10 mm");
    const mileage = within(row).getByTestId("compound-0-mileage").textContent ?? "";
    expect(mileage).toContain("[20");
    expect(mileage).toContain("30]");
    const endpoints = within(row).getByTestId("compound-0-endpoints").textContent ?? "";
    expect(endpoints).toContain("[(20, 0)");
    expect(endpoints).toContain("(30, 0)]");
    // pieces 按原线段切分（单段），与 SVG 同源数组
    const pieces = within(row).getAllByTestId(/compound-0-piece-/);
    expect(pieces.length).toBe(1);
    expect(pieces[0].textContent).toContain("线段 #0");
    expect(pieces[0].textContent).toContain("圈 0/1");

    // SVG 同一 compound_intrusion_segments 数组高亮
    const mark = document.querySelector('[data-testid="compound-c0-1-s0"]');
    expect(mark).toBeInTheDocument();
    expect(mark?.tagName.toLowerCase()).toBe("path");
    // 普通单圈区间高亮仍各自存在（旧能力不回归）
    expect(document.querySelector('[data-testid="intrusion-c0-s0"]')).toBeInTheDocument();
    expect(document.querySelector('[data-testid="intrusion-c1-s0"]')).toBeInTheDocument();
  });

  it("复合侵入段：双圈在拐点的零长相切点画圆环，pieces 跨两条原线段", async () => {
    render(<App />);
    // 路径 (0,0)->(10,0)->(20,0)，电缆 1；圈 A(0,0) r9 -> [0,10]，
    // 圈 B(20,0) r9 -> [10,20]：里程 10 为双圈零长点，pieces 跨段 0/1。
    await userEvent.clear(screen.getByTestId("cable-radius"));
    await userEvent.type(screen.getByTestId("cable-radius"), "1");
    await userEvent.clear(screen.getByTestId("node-0-x"));
    await userEvent.type(screen.getByTestId("node-0-x"), "0");
    const n1x = screen.getByTestId("node-1-x");
    const n1y = screen.getByTestId("node-1-y");
    await userEvent.clear(n1x);
    await userEvent.type(n1x, "10");
    await userEvent.clear(n1y);
    await userEvent.type(n1y, "0");
    await userEvent.click(screen.getByTestId("add-node"));
    await userEvent.clear(screen.getByTestId("node-2-x"));
    await userEvent.type(screen.getByTestId("node-2-x"), "20");
    await userEvent.clear(screen.getByTestId("node-2-y"));
    await userEvent.type(screen.getByTestId("node-2-y"), "0");

    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "0");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "0");
    await userEvent.clear(screen.getByTestId("circle-0-radius"));
    await userEvent.type(screen.getByTestId("circle-0-radius"), "9");
    await userEvent.click(screen.getByTestId("add-circle"));
    await userEvent.clear(screen.getByTestId("circle-1-x"));
    await userEvent.type(screen.getByTestId("circle-1-x"), "20");
    await userEvent.clear(screen.getByTestId("circle-1-y"));
    await userEvent.type(screen.getByTestId("circle-1-y"), "0");
    await userEvent.clear(screen.getByTestId("circle-1-radius"));
    await userEvent.type(screen.getByTestId("circle-1-radius"), "9");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("compound-panel")).toBeInTheDocument(),
    );
    const panel = screen.getByTestId("compound-list");
    const rows = within(panel).getAllByRole("listitem").filter(
      (li) => li.parentElement === panel,
    );
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain("多圈相切零长点");
    const mileage = within(rows[0]).getByTestId("compound-0-mileage").textContent ?? "";
    expect(mileage).toContain("[10");
    expect(mileage).toContain("10]");
    const pieces = within(rows[0]).getAllByTestId(/compound-0-piece-/);
    expect(pieces.length).toBe(2);
    // 两段零长片段在拐点 (10,0)，SVG 画两个圆环
    const m0 = document.querySelector('[data-testid="compound-c0-1-s0"]');
    const m1 = document.querySelector('[data-testid="compound-c0-1-s1"]');
    expect(m0?.tagName.toLowerCase()).toBe("circle");
    expect(m1?.tagName.toLowerCase()).toBe("circle");
  });

  it("在途请求期间重置：慢响应返回后旧区间不恢复", async () => {    // 直接用 fetch 桩：让真实客户端请求挂起，重置后再放行
    let release: (() => void) | null = null;
    const realFetch = window.fetch.bind(window) as typeof window.fetch;
    const slowFetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.endsWith("/api/precheck")) {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      }
      return realFetch(input as RequestInfo, init as RequestInit);
    });
    vi.stubGlobal("fetch", slowFetch);

    render(<App />);
    await submit();
    await waitFor(() => expect(slowFetch).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByTestId("reset"));
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();

    (release as (() => void) | null)?.();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  // ---------- 现场标定（calibration）----------

  /** 录入一对控制点（必要时先扩充行）。 */
  async function fillPair(
    i: number,
    surveyX: string,
    surveyY: string,
    pathX: string,
    pathY: string,
  ) {
    const type = async (testid: string, v: string) => {
      const el = screen.getByTestId(testid);
      await userEvent.clear(el);
      await userEvent.type(el, v);
    };
    await type(`pair-${i}-survey-x`, surveyX);
    await type(`pair-${i}-survey-y`, surveyY);
    await type(`pair-${i}-path-x`, pathX);
    await type(`pair-${i}-path-y`, pathY);
  }

  it("标定（纯平移）：变换摘要展示且相切结论 / SVG 高亮与后端同源", async () => {
    render(<App />);
    // 默认路径 (-100,0)->(100,0)、电缆 5、孔半径 10；
    // survey = path + (1000,2000)，孔心 (1000,2015) → 施工坐标 (0,15) 相切
    await userEvent.click(screen.getByTestId("calibration-enabled"));
    await userEvent.click(screen.getByTestId("add-pair")); // 第三对
    await fillPair(0, "1000", "2000", "0", "0");
    await fillPair(1, "1100", "2000", "100", "0");
    await fillPair(2, "1000", "2100", "0", "100");
    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "1000");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "2015");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    // 变换摘要：恒等旋转、平移 (-1000,-2000)、残差 0
    const panel = screen.getByTestId("calibration-panel");
    expect(panel).toBeInTheDocument();
    expect(screen.getByTestId("calibration-rms").textContent).toContain("0");
    expect(screen.getByTestId("calibration-rotation").textContent).toContain(
      "[[1, 0], [0, 1]]",
    );
    expect(screen.getByTestId("calibration-translation").textContent).toContain(
      "(-1000, -2000)",
    );
    // 相切结论与 SVG 高亮同源（同一未舍入批次的三位展示）
    const detail = screen.getByTestId("first-collision-detail").textContent ?? "";
    expect(detail).toContain("(0, 0)");
    expect(detail).toContain("15");
    expect(document.querySelector('[data-testid="intrusion-c0-s0"]')).toBeInTheDocument();
    expect(screen.getByTestId("first-collision-marker")).toBeInTheDocument();
  });

  it("标定（九十度旋转）：复合侵入段与 SVG 高亮和直接坐标场景一致", async () => {
    render(<App />);
    // 路径 0->100，电缆 1；survey 经 R90(x,y)=(-y,x) 即施工坐标。
    // 孔 (0,-20)/(0,-30) r9 → 施工坐标 (20,0)/(30,0)，扩张 10 → 复合段 [20,30]
    await userEvent.clear(screen.getByTestId("cable-radius"));
    await userEvent.type(screen.getByTestId("cable-radius"), "1");
    await userEvent.clear(screen.getByTestId("node-0-x"));
    await userEvent.type(screen.getByTestId("node-0-x"), "0");
    await userEvent.clear(screen.getByTestId("node-1-x"));
    await userEvent.type(screen.getByTestId("node-1-x"), "100");

    await userEvent.click(screen.getByTestId("calibration-enabled"));
    await userEvent.click(screen.getByTestId("add-pair"));
    await fillPair(0, "0", "0", "0", "0");
    await fillPair(1, "100", "0", "0", "100");
    await fillPair(2, "0", "100", "-100", "0");

    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "0");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "-20");
    await userEvent.clear(screen.getByTestId("circle-0-radius"));
    await userEvent.type(screen.getByTestId("circle-0-radius"), "9");
    await userEvent.click(screen.getByTestId("add-circle"));
    await userEvent.clear(screen.getByTestId("circle-1-x"));
    await userEvent.type(screen.getByTestId("circle-1-x"), "0");
    await userEvent.clear(screen.getByTestId("circle-1-y"));
    await userEvent.type(screen.getByTestId("circle-1-y"), "-30");
    await userEvent.clear(screen.getByTestId("circle-1-radius"));
    await userEvent.type(screen.getByTestId("circle-1-radius"), "9");

    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("compound-panel")).toBeInTheDocument(),
    );
    // 旋转摘要为九十度真旋转
    expect(screen.getByTestId("calibration-rotation").textContent).toContain(
      "[[0, -1], [1, 0]]",
    );
    const panel = screen.getByTestId("compound-list");
    const rows = within(panel).getAllByRole("listitem").filter(
      (li) => li.parentElement === panel,
    );
    expect(rows.length).toBe(1);
    expect(rows[0].textContent).toContain("复合侵入 10 mm");
    const mileage = within(rows[0]).getByTestId("compound-0-mileage").textContent ?? "";
    expect(mileage).toContain("[20");
    expect(mileage).toContain("30]");
    // SVG 同一 compound_intrusion_segments 数组高亮
    const mark = document.querySelector('[data-testid="compound-c0-1-s0"]');
    expect(mark).toBeInTheDocument();
    expect(mark?.tagName.toLowerCase()).toBe("path");
  });

  it("标定残差超阈值：422 字段错误，旧结论（含标定摘要）作废", async () => {
    render(<App />);
    // 先拿到一个旧结论
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    // 第三对偏差 50mm，阈值 1mm → 后端 422
    await userEvent.click(screen.getByTestId("calibration-enabled"));
    await userEvent.click(screen.getByTestId("add-pair"));
    await fillPair(0, "1000", "2000", "0", "0");
    await fillPair(1, "1100", "2000", "100", "0");
    await fillPair(2, "1000", "2100", "0", "150");
    await submit();

    await waitFor(() => expect(screen.getByTestId("banner-error")).toBeInTheDocument());
    expect(
      screen.getByTestId("err-calibration.max_rms_error").textContent,
    ).toContain("超过阈值");
    // 旧结论全部清除：无场景、无碰撞横幅、无标定摘要
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("calibration-panel")).not.toBeInTheDocument();
  });

  it("控制点全部重合：本地校验失败不发请求，旧结论作废", async () => {
    render(<App />);
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId("calibration-enabled"));
    await fillPair(0, "5", "5", "0", "0");
    await fillPair(1, "5", "5", "1", "0"); // survey 全部重合
    await submit();

    await waitFor(() => expect(screen.getByTestId("banner-error")).toBeInTheDocument());
    expect(screen.getByTestId("err-calibration-points").textContent).toContain("重合");
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("calibration-panel")).not.toBeInTheDocument();
  });

  it("重置使标定结论与录入一并作废", async () => {
    render(<App />);
    await userEvent.click(screen.getByTestId("calibration-enabled"));
    await userEvent.click(screen.getByTestId("add-pair"));
    await fillPair(0, "1000", "2000", "0", "0");
    await fillPair(1, "1100", "2000", "100", "0");
    await fillPair(2, "1000", "2100", "0", "100");
    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "1000");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "2015");
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("calibration-panel")).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId("reset"));
    expect(screen.queryByTestId("calibration-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    // 标定录入恢复初始（未启用）
    expect(
      (screen.getByTestId("calibration-enabled") as HTMLInputElement).checked,
    ).toBe(false);
  });

  it("请求失败（网络异常）使旧结论与标定摘要作废", async () => {
    render(<App />);
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    const realFetch = window.fetch.bind(window) as typeof window.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.endsWith("/api/precheck")) {
          throw new Error("网络不可达");
        }
        return realFetch(input as RequestInfo, init as RequestInit);
      }),
    );
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("network-error")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("calibration-panel")).not.toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  // ---------- 一次性改线预览 ----------

  it("改线预览：相切原线绕孔后候选可敷设，同一快照切换原线/候选线与风险摘要", async () => {
    render(<App />);
    // 默认：(-100,0)->(100,0)、电缆5、孔(0,15)r10 → (0,0) 相切碰撞，里程100
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId("reroute-enabled"));
    // 首尾端点自动锁定为 (-100,0)/(100,0)，且只读
    expect(
      (screen.getByTestId("reroute-point-0-x") as HTMLInputElement).readOnly,
    ).toBe(true);
    expect((screen.getByTestId("reroute-point-0-x") as HTMLInputElement).value).toBe(
      "-100",
    );
    // 中间加折点 (0,-30)：新路径距孔（扩张15）足够远
    await userEvent.click(screen.getByTestId("add-reroute-point"));
    const mx = screen.getByTestId("reroute-point-1-x");
    const my = screen.getByTestId("reroute-point-1-y");
    await userEvent.clear(mx);
    await userEvent.type(mx, "0");
    await userEvent.clear(my);
    await userEvent.type(my, "-30");

    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(screen.getByTestId("view-switch")).toBeInTheDocument());

    // 候选线：可敷设、无碰撞标记、风险摘要显示“消除 1”
    await waitFor(() => expect(screen.getByTestId("banner-ok")).toBeInTheDocument());
    expect(screen.queryByTestId("first-collision-marker")).not.toBeInTheDocument();
    const title = screen.getByTestId("reroute-diff-title").textContent ?? "";
    expect(title).toContain("消除 1");
    expect(title).toContain("新增 0");
    const risk = screen.getByTestId("risk-eliminated-0-0").textContent ?? "";
    expect(risk).toContain("线段 #0");
    expect(risk).toContain("里程 100");
    // 候选 SVG：3 个节点的下绕折线（原线只有 2 个节点）
    expect(document.querySelectorAll('.scene [data-testid^="node-"]').length).toBe(3);
    const midNode = document.querySelector('[data-testid="node-1"]');
    expect(midNode).toBeInTheDocument();

    // 切回原线：相切碰撞与里程 100 的区间恢复，摘要隐藏
    await userEvent.click(screen.getByTestId("view-original"));
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("first-collision-marker")).toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();

    // 再切候选线：同一快照直接恢复，无需再请求
    await userEvent.click(screen.getByTestId("view-candidate"));
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();
    expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument();

    // 取消预览：只剩原线
    await userEvent.click(screen.getByTestId("reroute-cancel"));
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
  });

  it("改线引入新风险：候选不可敷设且摘要按圈给出“新增”", async () => {
    render(<App />);
    // 原线无碰撞：孔放远处 (40,-40)，原线 y=0 距离 40 > 扩张 15
    await userEvent.clear(screen.getByTestId("circle-0-x"));
    await userEvent.type(screen.getByTestId("circle-0-x"), "40");
    await userEvent.clear(screen.getByTestId("circle-0-y"));
    await userEvent.type(screen.getByTestId("circle-0-y"), "-40");
    await submit();
    await waitFor(() => expect(screen.getByTestId("banner-ok")).toBeInTheDocument());

    // 改线中间折点 (0,-60)：第二段 (0,-60)->(100,0) 穿过扩张圈（垂距≈3.4），
    // 第一段相离，故恰好新增 1 处风险（候选线段下标 1）。
    await userEvent.click(screen.getByTestId("reroute-enabled"));
    await userEvent.click(screen.getByTestId("add-reroute-point"));
    const mx = screen.getByTestId("reroute-point-1-x");
    const my = screen.getByTestId("reroute-point-1-y");
    await userEvent.clear(mx);
    await userEvent.type(mx, "0");
    await userEvent.clear(my);
    await userEvent.type(my, "-60");

    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(screen.getByTestId("view-switch")).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );
    const title = screen.getByTestId("reroute-diff-title").textContent ?? "";
    expect(title).toContain("新增 1");
    expect(screen.getByTestId("risk-added-0-1")).toBeInTheDocument();
    expect(screen.queryByTestId("risk-eliminated-0-0")).not.toBeInTheDocument();
    // 切回原线仍可敷设（原方案未被改线结果覆盖）
    await userEvent.click(screen.getByTestId("view-original"));
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();
  });

  it("改线区间下标非法：本地拦截不发请求，已保存原方案保留", async () => {
    render(<App />);
    await submit();
    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByTestId("reroute-enabled"));
    await userEvent.clear(screen.getByTestId("reroute-end"));
    await userEvent.type(screen.getByTestId("reroute-end"), "0"); // start 0 == end 0
    await userEvent.click(screen.getByTestId("reroute-preview"));

    await waitFor(() =>
      expect(screen.getByTestId("reroute-banner-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("err-reroute-range").textContent).toContain("严格小于");
    // 原方案仍在，且没有进入候选视图
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
  });
});
