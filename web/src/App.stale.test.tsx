import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { PrecheckResponse } from "./types";

function collisionResult(intervalStart: number): PrecheckResponse {
  return {
    feasible: false,
    cable_radius: 5,
    nodes: [
      { x: -100, y: 0 },
      { x: 100, y: 0 },
    ],
    circles: [{ center: { x: 0, y: 15 }, radius: 10, expanded_radius: 15 }],
    collision_count: 1,
    first_collision: {
      segment_index: 0,
      circle_index: 0,
      nearest: { x: intervalStart, y: 0 },
      distance: 15,
      expanded_radius: 15,
      circle_center: { x: 0, y: 15 },
      circle_radius: 10,
      cable_radius: 5,
    },
    collisions: [
      {
        segment_index: 0,
        circle_index: 0,
        nearest: { x: intervalStart, y: 0 },
        distance: 15,
        expanded_radius: 15,
        circle_center: { x: 0, y: 15 },
        circle_radius: 10,
        cable_radius: 5,
      },
    ],
    intrusion_intervals: [
      {
        circle_index: 0,
        entry_segment_index: 0,
        exit_segment_index: 0,
        entry: { x: intervalStart, y: 0 },
        exit: { x: intervalStart, y: 0 },
        start_mileage: 100 + intervalStart,
        end_mileage: 100 + intervalStart,
        length: 0,
        pieces: [
          {
            segment_index: 0,
            circle_index: 0,
            entry: { x: intervalStart, y: 0 },
            exit: { x: intervalStart, y: 0 },
            start_mileage: 100 + intervalStart,
            end_mileage: 100 + intervalStart,
            length: 0,
          },
        ],
      },
    ],
    compound_intrusion_segments: [],
  };
}

describe("乱序（stale）响应不能恢复旧区间", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("第一次请求迟迟返回时，用户重置/再次提交后旧响应到达也不恢复旧区间", async () => {
    const oldResolve: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(async () => {
      await new Promise((resolve) => oldResolve.push(resolve));
      return {
        ok: true,
        status: 200,
        json: async () => collisionResult(-99),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // 在途期间重置：旧结论（未来到达）必须作废
    await userEvent.click(screen.getByTestId("reset"));
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();

    // 旧响应此刻才返回
    oldResolve[0](undefined);
    // 等一个微任务链，确认页面没有把旧区间画回来
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByTestId("interval-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("banner-collision")).not.toBeInTheDocument();
    expect(screen.queryByTestId("scene")).not.toBeInTheDocument();

    // 第二次提交成功：显示新数组
    fetchMock.mockImplementationOnce(async () => ({
      ok: true,
      status: 200,
      json: async () => collisionResult(0),
    }));
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() =>
      expect(screen.getByTestId("interval-panel")).toBeInTheDocument(),
    );
    expect(screen.getAllByTestId(/^interval-0$/).length).toBeGreaterThan(0);
  });

  it("在途请求期间重置再提交：旧请求晚于新响应返回，也不能覆盖新结论", async () => {
    const resolvers: Array<(v: unknown) => void> = [];
    const fetchMock = vi.fn(
      () =>
        new Promise((resolve) => {
          resolvers.push(resolve);
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    // 第一次提交（在途）
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    // 重置使在途请求作废，然后立即再次提交
    await userEvent.click(screen.getByTestId("reset"));
    await userEvent.click(screen.getByTestId("submit"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // 第二次（新）先返回
    resolvers[1]({
      ok: true,
      status: 200,
      json: async () => collisionResult(1),
    });
    await waitFor(() =>
      expect(screen.getByTestId("interval-panel")).toBeInTheDocument(),
    );

    // 第一次（旧）随后返回：不得覆盖
    resolvers[0]({
      ok: true,
      status: 200,
      json: async () => collisionResult(-99),
    });
    await Promise.resolve();
    await Promise.resolve();
    const badge = screen.getByTestId("interval-0-badge").textContent;
    expect(badge).toContain("相切零长点");
    // 新结论的坐标 1 被显示（旧结论 -99 不应出现）
    const row = screen.getByTestId("interval-0").textContent ?? "";
    expect(row).not.toContain("-99");
  });
});
