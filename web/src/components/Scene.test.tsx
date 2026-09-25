import { render, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Scene } from "./Scene";
import type { PrecheckResponse } from "../types";

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

describe("Scene", () => {
  it("突出接口返回的首个碰撞，而不是按距离自行改选", () => {
    const { getByTestId } = render(<Scene result={orderedCollisionsResult} />);

    const firstGroup = getByTestId("collision-0-0");
    expect(within(firstGroup).getByTestId("first-collision-marker")).toBeInTheDocument();

    const deeperGroup = getByTestId("collision-1-1");
    expect(within(deeperGroup).queryByTestId("first-collision-marker")).not.toBeInTheDocument();
    expect(deeperGroup.querySelector(".other-hit")).toBeInTheDocument();
  });

  it("视窗完整纳入扩张安全圈，避免巨大安全边界落在画布外", () => {
    const result: PrecheckResponse = {
      feasible: false,
      cable_radius: 100,
      nodes: [
        { x: -1, y: 0 },
        { x: 1, y: 0 },
      ],
      circles: [
        {
          center: { x: 0, y: 0 },
          radius: 1,
          expanded_radius: 101,
        },
      ],
      collision_count: 1,
      first_collision: {
        segment_index: 0,
        circle_index: 0,
        nearest: { x: 0, y: 0 },
        distance: 0,
        expanded_radius: 101,
        circle_center: { x: 0, y: 0 },
        circle_radius: 1,
        cable_radius: 100,
      },
      collisions: [
        {
          segment_index: 0,
          circle_index: 0,
          nearest: { x: 0, y: 0 },
          distance: 0,
          expanded_radius: 101,
          circle_center: { x: 0, y: 0 },
          circle_radius: 1,
          cable_radius: 100,
        },
      ],
      intrusion_intervals: [],
      compound_intrusion_segments: [],
    };

    const { getByTestId } = render(<Scene result={result} />);
    const expanded = getByTestId("expanded-circle-0");

    // 202mm 的安全圈按 480px 可用高度缩放，半径应为 240px，圆周边界贴近视窗边缘。
    expect(Number(expanded.getAttribute("r"))).toBeCloseTo(240);
    expect(Number(expanded.getAttribute("cx"))).toBeCloseTo(280);
    expect(Number(expanded.getAttribute("cy"))).toBeCloseTo(280);
  });

  it("按 intrusion_intervals 高亮折线片段（与详情同源数组），零长相切画成点", () => {
    const result: PrecheckResponse = {
      feasible: false,
      cable_radius: 1,
      nodes: [
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 10, y: 10 },
      ],
      circles: [
        { center: { x: 10, y: 0 }, radius: 1, expanded_radius: 2 },
      ],
      collision_count: 2,
      first_collision: null,
      collisions: [],
      intrusion_intervals: [
        {
          circle_index: 0,
          entry_segment_index: 0,
          exit_segment_index: 1,
          entry: { x: 8, y: 0 },
          exit: { x: 10, y: 2 },
          start_mileage: 8,
          end_mileage: 12,
          length: 4,
          pieces: [
            {
              segment_index: 0,
              circle_index: 0,
              entry: { x: 8, y: 0 },
              exit: { x: 10, y: 0 },
              start_mileage: 8,
              end_mileage: 10,
              length: 2,
            },
            {
              segment_index: 1,
              circle_index: 0,
              entry: { x: 10, y: 0 },
              exit: { x: 10, y: 2 },
              start_mileage: 10,
              end_mileage: 12,
              length: 2,
            },
          ],
        },
      ],
      compound_intrusion_segments: [],
    };

    const { getByTestId } = render(<Scene result={result} />);
    const seg0 = getByTestId("intrusion-c0-s0");
    const seg1 = getByTestId("intrusion-c0-s1");
    expect(seg0.tagName.toLowerCase()).toBe("path");
    expect(seg1.tagName.toLowerCase()).toBe("path");
    // 两个片段颜色一致（同一禁入圈）
    expect(seg0.getAttribute("stroke")).toBe(seg1.getAttribute("stroke"));
  });

  it("零长相切区间渲染为圆点而非折线", () => {
    const result: PrecheckResponse = {
      feasible: false,
      cable_radius: 1,
      nodes: [{ x: 0, y: 0 }, { x: 10, y: 0 }],
      circles: [{ center: { x: 5, y: 1 }, radius: 1, expanded_radius: 2 }],
      collision_count: 1,
      first_collision: null,
      collisions: [],
      intrusion_intervals: [
        {
          circle_index: 0,
          entry_segment_index: 0,
          exit_segment_index: 0,
          entry: { x: 5, y: 0 },
          exit: { x: 5, y: 0 },
          start_mileage: 5,
          end_mileage: 5,
          length: 0,
          pieces: [
            {
              segment_index: 0,
              circle_index: 0,
              entry: { x: 5, y: 0 },
              exit: { x: 5, y: 0 },
              start_mileage: 5,
              end_mileage: 5,
              length: 0,
            },
          ],
        },
      ],
      compound_intrusion_segments: [],
    };
    const { getByTestId } = render(<Scene result={result} />);
    const mark = getByTestId("intrusion-c0-s0");
    expect(mark.tagName.toLowerCase()).toBe("circle");
  });

  it("复合侵入段由同一数组逐原线段高亮：跨段画虚线、零长点画圆环", () => {
    const result: PrecheckResponse = {
      feasible: false,
      cable_radius: 1,
      nodes: [
        { x: 0, y: 0 },
        { x: 10, y: 0 },
        { x: 20, y: 0 },
      ],
      circles: [
        { center: { x: 0, y: 0 }, radius: 9, expanded_radius: 10 },
        { center: { x: 20, y: 0 }, radius: 9, expanded_radius: 10 },
      ],
      collision_count: 2,
      first_collision: null,
      collisions: [],
      intrusion_intervals: [],
      compound_intrusion_segments: [
        {
          // 圈 0/1 在拐点 (10,0) 双圈零长相切点：pieces 跨两条线段
          circle_indices: [0, 1],
          start_mileage: 10,
          end_mileage: 10,
          start: { x: 10, y: 0 },
          end: { x: 10, y: 0 },
          start_inclusive: true,
          end_inclusive: true,
          length: 0,
          pieces: [
            {
              segment_index: 0,
              circle_indices: [0, 1],
              entry: { x: 10, y: 0 },
              exit: { x: 10, y: 0 },
              start_mileage: 10,
              end_mileage: 10,
              length: 0,
            },
            {
              segment_index: 1,
              circle_indices: [0, 1],
              entry: { x: 10, y: 0 },
              exit: { x: 10, y: 0 },
              start_mileage: 10,
              end_mileage: 10,
              length: 0,
            },
          ],
        },
      ],
    };

    const { getByTestId } = render(<Scene result={result} />);
    const p0 = getByTestId("compound-c0-1-s0");
    const p1 = getByTestId("compound-c0-1-s1");
    // 两个零长片段都画成圆环（circle），且与详情 pieces 同坐标
    expect(p0.tagName.toLowerCase()).toBe("circle");
    expect(p1.tagName.toLowerCase()).toBe("circle");
    expect(p0.getAttribute("cx")).toBe(p1.getAttribute("cx"));
  });

  it("复合侵入段正长度片段渲染为红色虚线，testid 编码全部圈序", () => {
    const result: PrecheckResponse = {
      feasible: false,
      cable_radius: 1,
      nodes: [{ x: 0, y: 0 }, { x: 100, y: 0 }],
      circles: [
        { center: { x: 20, y: 0 }, radius: 9, expanded_radius: 10 },
        { center: { x: 30, y: 0 }, radius: 9, expanded_radius: 10 },
      ],
      collision_count: 2,
      first_collision: null,
      collisions: [],
      intrusion_intervals: [],
      compound_intrusion_segments: [
        {
          circle_indices: [0, 1],
          start_mileage: 20,
          end_mileage: 30,
          start: { x: 20, y: 0 },
          end: { x: 30, y: 0 },
          start_inclusive: true,
          end_inclusive: true,
          length: 10,
          pieces: [
            {
              segment_index: 0,
              circle_indices: [0, 1],
              entry: { x: 20, y: 0 },
              exit: { x: 30, y: 0 },
              start_mileage: 20,
              end_mileage: 30,
              length: 10,
            },
          ],
        },
      ],
    };

    const { getByTestId } = render(<Scene result={result} />);
    const frag = getByTestId("compound-c0-1-s0");
    expect(frag.tagName.toLowerCase()).toBe("path");
    expect(frag.getAttribute("stroke-dasharray")).not.toBeNull();
  });
});
