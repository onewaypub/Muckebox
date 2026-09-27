// SPDX-FileCopyrightText: 2026 Muckebox contributors
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Small fetch wrapper: JSON, timeouts, the CSRF header on mutations.

const TIMEOUT_MS = 6000;

export class ApiError extends Error {
  constructor(status, code, retryIn = null) {
    super(code);
    this.status = status;
    this.code = code;
    this.retryIn = retryIn;
  }
}

export async function request(method, path, { body, etag, timeout = TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = { Accept: "application/json" };
  if (method !== "GET") headers["X-Muckebox"] = "1";
  if (etag) headers["If-None-Match"] = etag;
  let init = { method, headers, signal: controller.signal, cache: "no-store" };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    throw new ApiError(0, error.name === "AbortError" ? "timeout" : "offline");
  } finally {
    clearTimeout(timer);
  }
  noteServerTime(response.headers.get("Date"));
  if (response.status === 304) {
    return { status: 304, data: null, etag };
  }
  let data = null;
  if ((response.headers.get("Content-Type") || "").includes("json")) {
    data = await response.json().catch(() => null);
  }
  if (!response.ok) {
    const error = (data && data.error) || {};
    const fallback = response.status === 413 ? "request_too_large" : "http_" + response.status;
    throw new ApiError(response.status, error.code || fallback, error.retry_in);
  }
  return { status: response.status, data, etag: response.headers.get("ETag") };
}

// The tablet's own clock may be off; countdowns use the server's time instead.
let serverOffsetMs = 0;

function noteServerTime(header) {
  const serverMs = Date.parse(header || "");
  if (Number.isFinite(serverMs)) serverOffsetMs = serverMs - Date.now();
}

/** The server's current time in milliseconds (to the second). */
export function serverNow() {
  return Date.now() + serverOffsetMs;
}

export const get = (path, options) => request("GET", path, options);
export const post = (path, body, options) => request("POST", path, { ...options, body });
