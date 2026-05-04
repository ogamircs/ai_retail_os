/**
 * apiFetch contract tests.
 *
 * Pin the typed JSON helper's error path so the cockpit can rely on
 * `try / catch (ApiError)` for every fetch in the codebase. Pure-
 * function tests — no DOM, no React, just `vitest` + a stub fetch.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "../lib/api";

describe("apiFetch", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  const mockResponse = (init: {
    ok: boolean;
    status: number;
    body?: string;
    json?: unknown;
    statusText?: string;
  }) => {
    return {
      ok: init.ok,
      status: init.status,
      statusText: init.statusText ?? "",
      text: async () => init.body ?? "",
      json: async () => {
        if (init.json !== undefined) return init.json;
        throw new SyntaxError("invalid json");
      },
    } as Response;
  };

  it("returns parsed JSON on 2xx", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockResponse({ ok: true, status: 200, json: { hello: "world" } }),
    );
    const data = await apiFetch<{ hello: string }>("/api/x");
    expect(data).toEqual({ hello: "world" });
  });

  it("throws ApiError carrying status + url on non-2xx", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockResponse({ ok: false, status: 422, body: "not happy" }),
    );
    await expect(apiFetch("/api/y")).rejects.toMatchObject({
      name: "ApiError",
      status: 422,
      url: "/api/y",
    });
  });

  it("throws ApiError(0) when fetch itself rejects (network error)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(
      new TypeError("Failed to fetch"),
    );
    const err = (await apiFetch("/api/z").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.url).toBe("/api/z");
  });

  it("throws ApiError(0) when JSON parse fails", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      mockResponse({ ok: true, status: 200 }), // no json -> parse throws
    );
    await expect(apiFetch("/api/w")).rejects.toMatchObject({
      status: 0,
      name: "ApiError",
    });
  });
});
