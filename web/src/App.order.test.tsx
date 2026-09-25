import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { PrecheckResponse } from "./types";

const orderedCollisionsResult: PrecheckResponse = {
  feasible: false,
  cable_radius: 1,
  nodes: [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 20, y: 0 },
  ],
  circles: [
    { center: { x: 5, y: 2 }, radius: 1.1, expanded_radius: 2.1 },
    { center: { x: 15, y: 0 }, radius: 1, expanded_radius: 2 },
  ],
  collision_count: 2,
  first_collision: {
    segment_index: 0,
    circle_index: 0,
    nearest: { x: 5, y: 0 },
    distance: 2,
    expanded_radius: 2.1,
    circle_center: { x: 5, y: 2 },
    circle_radius: 1.1,
    cable_radius: 1,
  },
  collisions: [
    {
      segment_index: 0,
      circle_index: 0,
      nearest: { x: 5, y: 0 },
      distance: 2,
      expanded_radius: 2.1,
      circle_center: { x: 5, y: 2 },
      circle_radius: 1.1,
      cable_radius: 1,
    },
    {
      segment_index: 1,
      circle_index: 1,
      nearest: { x: 15, y: 0 },
      distance: 0,
      expanded_radius: 2,
      circle_center: { x: 15, y: 0 },
      circle_radius: 1,
      cable_radius: 1,
    },
  ],
  intrusion_intervals: [],
  compound_intrusion_segments: [],
};

describe("App collision details", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => orderedCollisionsResult,
      }),
    );
  });

  it("详情突出接口首个碰撞，其余列表不重复该碰撞", async () => {
    render(<App />);
    await userEvent.click(screen.getByTestId("submit"));

    await waitFor(() =>
      expect(screen.getByTestId("banner-collision")).toBeInTheDocument(),
    );

    const detail = screen.getByTestId("first-collision-detail").textContent ?? "";
    expect(detail).toContain("线段 #0");
    expect(detail).toContain("禁入圈 #0");
    expect(detail).toContain("距离 = 2");

    const rest = screen.getByTestId("rest-collisions").textContent ?? "";
    expect(rest).toContain("线段 #1");
    expect(rest).toContain("禁入圈 #1");
    expect(rest).toContain("距离 0");
    expect(rest).not.toContain("线段 #0");
  });
});
