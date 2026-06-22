// Single fetch wrapper for the whole app. Same-origin in dev (Vite proxy) and in
// prod (served by FastAPI), so the httponly auth cookie always rides along.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

function redirectToLogin() {
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(here)}`);
}

async function parseBody(res: Response): Promise<unknown> {
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("application/json")) {
    return res.json();
  }
  const text = await res.text();
  return text || null;
}

type RequestOptions = Omit<RequestInit, "body"> & { body?: unknown };

export async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers, ...rest } = options;
  const init: RequestInit = { credentials: "include", ...rest, headers: { ...headers } };

  if (body instanceof FormData) {
    // Let the browser set the multipart boundary.
    init.body = body;
  } else if (body !== undefined) {
    init.headers = { "Content-Type": "application/json", ...init.headers };
    init.body = JSON.stringify(body);
  }

  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError(0, "Сеть недоступна", err);
  }

  if (res.status === 401) {
    redirectToLogin();
    throw new ApiError(401, "Требуется авторизация");
  }

  const payload = await parseBody(res);
  if (!res.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? (payload as { detail: unknown }).detail
        : payload;
    const message = typeof detail === "string" ? detail : `Ошибка ${res.status}`;
    throw new ApiError(res.status, message, detail);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  // multipart helper for file uploads to /ui/* form endpoints
  form: <T>(path: string, data: FormData) => request<T>(path, { method: "POST", body: data }),
};
