import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { PrecheckResponse } from "./types";

/**
 * 改线预览前端契约（fetch 桩，不依赖真实 API）：
 * 1. 预览使用独立请求序号：乱序的预览响应不得覆盖新预览/已保存原方案；
 * 2. 取消预览、校验失败都只清候选视图，已保存原方案保持不变；
 * 3. 原线/候选线在同一版本快照中切换，SVG 与区间详情严格随数据源变化。
 */

const CIRCLES = [
  { center: { x: 0, y: 15 }, radius: 10, expanded_radius: 15 },
  { center: { x: 100, y: 15 }, radius: 10, expanded_radius: 15 },
];

function collision(seg: number, circle: number, x: number, mileage: number) {
  const cy = CIRCLES[circle].center.y;
  return {
    segment_index: seg,
    circle_index: circle,
    nearest: { x, y: 0 },
    distance: 15,
    expanded_radius: 15,
    circle_center: { x, y: cy },
    circle_radius: 10,
    cable_radius: 5,
    mileage,
  };
}

/** 原线：(-100,0)->(100,0) 直线 200mm，两个圆都相切（里程 100、200）。 */
const originalResult: PrecheckResponse = {
  feasible: false,
  cable_radius: 5,
  nodes: [
    { x: -100, y: 0 },
    { x: 100, y: 0 },
  ],
  circles: CIRCLES,
  collision_count: 2,
  first_collision: collision(0, 0, 0, 100) as never,
  collisions: [collision(0, 0, 0, 100), collision(0, 1, 100, 200)] as never,
  intrusion_intervals: [],
  compound_intrusion_segments: [],
};

/** 候选线：(-100,0)->(0,-40)->(100,0)，只消除全部碰撞（可敷设）。 */
const candidateNodes = [
  { x: -100, y: 0 },
  { x: 0, y: -40 },
  { x: 100, y: 0 },
];
const candidateResult = {
  feasible: true,
  cable_radius: 5,
  nodes: candidateNodes,
  circles: CIRCLES,
  collision_count: 0,
  first_collision: null,
  collisions: [],
  intrusion_intervals: [],
  compound_intrusion_segments: [],
};
const previewOk: PrecheckResponse = {
  ...originalResult,
  reroute_preview: {
    range: { start_index: 0, end_index: 1, replacement_point_count: 3 },
    candidate: candidateResult,
    circle_risks: [
      {
        circle_index: 0,
        eliminated: [collision(0, 0, 0, 100) as never],
        added: [],
        remaining: [],
      },
      {
        circle_index: 1,
        eliminated: [collision(0, 1, 100, 200) as never],
        added: [],
        remaining: [],
      },
    ],
    eliminated_count: 2,
    added_count: 0,
    remaining_count: 0,
  },
};

async function saveOriginal() {
  await userEvent.click(screen.getByTestId("submit"));
  await waitFor(() =>
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
  );
}

async function enableAndFillReroute() {
  await userEvent.click(screen.getByTestId("reroute-enabled"));
  expect(screen.getByTestId("reroute-editor")).toBeInTheDocument();
  // 端点 (-100,0)/(100,0) 已自动锁定；插入一个中间折点改成 (0,-40)
  await userEvent.click(screen.getByTestId("add-reroute-point"));
  const mx = screen.getByTestId("reroute-point-1-x");
  const my = screen.getByTestId("reroute-point-1-y");
  await userEvent.clear(mx);
  await userEvent.type(mx, "0");
  await userEvent.clear(my);
  await userEvent.type(my, "-40");
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("改线预览：快照切换与视图一致性", () => {
  it("候选线可敷设：切换原线/候选线时 SVG 路径与风险摘要严格同源", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => previewOk,
      }),
    );
    render(<App />);
    await saveOriginal();

    // 默认原线：两个碰撞标记
    expect(screen.getByTestId("collision-0-0")).toBeInTheDocument();
    expect(screen.getByTestId("collision-0-1")).toBeInTheDocument();
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();

    await enableAndFillReroute();
    await userEvent.click(screen.getByTestId("reroute-preview"));

    // 预览成功：自动切到候选视图，出现切换条与风险摘要
    await waitFor(() => expect(screen.getByTestId("view-switch")).toBeInTheDocument());
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    const diff = screen.getByTestId("reroute-diff-panel");
    expect(diff.textContent).toContain("消除 2");
    expect(screen.getByTestId("risk-eliminated-0-0")).toBeInTheDocument();
    expect(screen.queryByTestId(/risk-added/)).not.toBeInTheDocument();

    // 候选 SVG 路径节点数为 3（三段？两折点段），原线为 2
    let nodeMarkers = document.querySelectorAll(".scene [data-testid^=\"node-\"]");
    expect(nodeMarkers.length).toBe(3);

    // 切回原线：碰撞标记回来，风险摘要消失，SVG 恢复 2 节点
    await userEvent.click(screen.getByTestId("view-original"));
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("collision-0-0")).toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    nodeMarkers = document.querySelectorAll(".scene [data-testid^=\"node-\"]");
    expect(nodeMarkers.length).toBe(2);

    // 再切候选：摘要与 3 节点路径再次出现（同一快照，不重新请求）
    const callsBefore = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    await userEvent.click(screen.getByTestId("view-candidate"));
    expect(screen.getByTestId("reroute-diff-panel")).toBeInTheDocument();
    nodeMarkers = document.querySelectorAll(".scene [data-testid^=\"node-\"]");
    expect(nodeMarkers.length).toBe(3);
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      callsBefore,
    );
  });

  it("取消预览只移除候选视图，已保存原方案不被覆盖", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => previewOk,
      }),
    );
    render(<App />);
    await saveOriginal();
    await enableAndFillReroute();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(screen.getByTestId("view-switch")).toBeInTheDocument());
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("reroute-cancel"));
    // 取消后：切换条/摘要/候选横幅消失；原线碰撞结论仍在
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.getByTestId("collision-0-0")).toBeInTheDocument();
    // 取消按钮恢复禁用（无预览可取消）
    expect(
      (screen.getByTestId("reroute-cancel") as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("改线本地校验失败：不发预览请求，原方案保持显示", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => previewOk,
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await saveOriginal();
    const callsAfterSave = fetchMock.mock.calls.length;

    await userEvent.click(screen.getByTestId("reroute-enabled"));
    // 起点折点被改成与边界节点不一致（首尾本应锁定，直接破坏中间值：
    // 这里把区间终点下标改非法）
    await userEvent.clear(screen.getByTestId("reroute-end"));
    await userEvent.type(screen.getByTestId("reroute-end"), "9");
    await userEvent.click(screen.getByTestId("reroute-preview"));

    await waitFor(() =>
      expect(screen.getByTestId("reroute-banner-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("err-reroute-range").textContent).toContain("内的整数");
    // 没有发出新请求；原线碰撞结论仍在
    expect(fetchMock.mock.calls.length).toBe(callsAfterSave);
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
  });
});

describe("改线预览：过期（乱序）响应不得覆盖", () => {
  it("第一次预览慢响应在取消后到达：不恢复候选视图，不影响原方案", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === "string" ? input : input.toString();
        // 主提交立即返回；仅预览请求挂起
        if (!url.endsWith("/api/precheck")) throw new Error("unexpected url");
        const bodyText = init?.body ? String(init.body) : "";
        if (!bodyText.includes("reroute")) {
          return { ok: true, status: 200, json: async () => originalResult };
        }
        return await new Promise((resolve) => {
          resolvers.push(resolve);
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await saveOriginal();

    await enableAndFillReroute();
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(resolvers.length).toBe(1));
    // 候选视图在途：显示 loading，原方案仍可见（preview 未到达）
    expect(screen.getByTestId("preview-loading")).toBeInTheDocument();
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();

    // 用户取消预览
    await userEvent.click(screen.getByTestId("reroute-cancel"));
    expect(screen.queryByTestId("preview-loading")).not.toBeInTheDocument();

    // 旧预览响应此刻才返回：不得画出候选视图
    resolvers[0]({ ok: true, status: 200, json: async () => previewOk });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
  });

  it("连续两次预览：先返回的旧响应不得覆盖后一次预览", async () => {
    // 第一次预览：仍有一个碰撞（新增风险）；第二次预览：全部消除
    const oneCollision: PrecheckResponse = {
      ...originalResult,
      reroute_preview: {
        range: { start_index: 0, end_index: 1, replacement_point_count: 3 },
        candidate: {
          ...candidateResult,
          feasible: false,
          collision_count: 1,
          first_collision: collision(1, 0, 100, 0) as never,
          collisions: [collision(1, 0, 100, 0)] as never,
        },
        circle_risks: [
          {
            circle_index: 0,
            eliminated: [collision(0, 0, 0, 100) as never],
            added: [collision(1, 0, 100, 0) as never],
            remaining: [],
          },
          {
            circle_index: 1,
            eliminated: [collision(0, 1, 100, 200) as never],
            added: [],
            remaining: [],
          },
        ],
        eliminated_count: 2,
        added_count: 1,
        remaining_count: 0,
      },
    };

    const resolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, init?: RequestInit) => {
        const bodyText = init?.body ? String(init.body) : "";
        if (!bodyText.includes("reroute")) {
          return { ok: true, status: 200, json: async () => originalResult };
        }
        return await new Promise((resolve) => {
          resolvers.push(resolve);
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await saveOriginal();

    await enableAndFillReroute();
    // 第一次预览（在途）
    await userEvent.click(screen.getByTestId("reroute-preview"));
    await waitFor(() => expect(resolvers.length).toBe(1));
    // 再次点击生成第二次预览（第一次随即过期）
    await userEvent.click(screen.getByTestId("reroute-preview"));
    expect(resolvers.length).toBe(2);

    // 第二次（新）先返回：全部消除
    resolvers[1]({ ok: true, status: 200, json: async () => previewOk });
    await waitFor(() => expect(screen.getByTestId("banner-ok")).toBeInTheDocument());
    const title = screen.getByTestId("reroute-diff-title").textContent ?? "";
    expect(title).toContain("消除 2");

    // 第一次（旧）随后返回：不得覆盖
    resolvers[0]({ ok: true, status: 200, json: async () => oneCollision });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.getByTestId("banner-ok")).toBeInTheDocument();
    expect(screen.queryAllByTestId(/risk-added/).length).toBe(0);
    // 原线仍可一键切回且不受乱序影响
    await userEvent.click(screen.getByTestId("view-original"));
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    // 摘要在原线视图隐藏
    expect(screen.queryByTestId("reroute-diff-panel")).not.toBeInTheDocument();
  });

  it("预览 422：字段错误只挂预览区，原方案仍显示且可继续切换", async () => {
    let n = 0;
    const fetchMock = vi.fn(async () => {
      n += 1;
      if (n === 1) {
        return { ok: true, status: 200, json: async () => originalResult };
      }
      return {
        ok: false,
        status: 422,
        json: async () => ({
          ok: false,
          errors: {
            "reroute.replacement_points[0].x":
              "首个接入折点必须与被替换区间起点精确重合",
          },
        }),
      };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await saveOriginal();
    await enableAndFillReroute();
    await userEvent.click(screen.getByTestId("reroute-preview"));

    await waitFor(() =>
      expect(screen.getByTestId("reroute-banner-error")).toBeInTheDocument(),
    );
    // 原方案碰撞结论未被覆盖
    expect(screen.getByTestId("banner-collision")).toBeInTheDocument();
    expect(screen.queryByTestId("view-switch")).not.toBeInTheDocument();
    // 错误文案可定位到端点字段
    const panel = document.querySelector('[data-testid="reroute-editor"]');
    expect(panel?.textContent).toContain("精确重合");
  });
});
