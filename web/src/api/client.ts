import type {
  FieldErrors,
  PrecheckPayload,
  PrecheckResponse,
} from "../types";

// 浏览器同源（nginx / Vite 代理）时默认走 /api；
// 验收容器通过 VITE_API_BASE 指向真实 API 地址。
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export class ValidationError extends Error {
  errors: FieldErrors;
  constructor(errors: FieldErrors) {
    super("预检输入不合法");
    this.name = "ValidationError";
    this.errors = errors;
  }
}

export async function precheck(
  payload: PrecheckPayload,
): Promise<PrecheckResponse> {
  const res = await fetch(`${API_BASE}/api/precheck`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (res.status === 422) {
    const data = await res.json();
    throw new ValidationError(data?.errors ?? {});
  }
  if (!res.ok) {
    throw new Error(`服务异常（HTTP ${res.status}）`);
  }
  return (await res.json()) as PrecheckResponse;
}
