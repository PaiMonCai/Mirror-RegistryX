export type ApiMethod = 'GET' | 'POST' | 'PUT' | 'DELETE';

export interface ApiErrorPayload {
  code: string;
  message: string;
  suggestion?: string;
  details?: Record<string, unknown>;
}

export type ApiClient = <T = any>(method: ApiMethod, path: string, body?: unknown) => Promise<T>;

export class ApiError extends Error {
  status: number;
  code: string;
  suggestion?: string;
  details: Record<string, unknown>;

  constructor(status: number, payload: Partial<ApiErrorPayload> | string) {
    const message = typeof payload === 'string' ? payload : payload.message || `${status} 请求失败`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = typeof payload === 'string' ? 'API_ERROR' : payload.code || 'API_ERROR';
    this.suggestion = typeof payload === 'string' ? undefined : payload.suggestion;
    this.details = typeof payload === 'string' ? {} : payload.details || {};
  }

  get userMessage() {
    return this.suggestion ? `${this.message}｜${this.suggestion}` : this.message;
  }
}

function parseLegacyError(status: number, text: string): ApiErrorPayload {
  if (!text) return { code: 'API_ERROR', message: `${status} 请求失败` };
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object') {
      return {
        code: typeof parsed.code === 'string' ? parsed.code : 'API_ERROR',
        message: typeof parsed.message === 'string' ? parsed.message : String(parsed.detail || text),
        suggestion: typeof parsed.suggestion === 'string' ? parsed.suggestion : undefined,
        details: parsed.details && typeof parsed.details === 'object' ? parsed.details : {},
      };
    }
  } catch {
    // Keep the original response text.
  }
  return { code: 'API_ERROR', message: text };
}

export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage;
  if (error instanceof Error) return error.message;
  return String(error);
}

export function createApiClient(): ApiClient {
  return async function api<T = any>(method: ApiMethod, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    const response = await fetch(`/api${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new ApiError(response.status, parseLegacyError(response.status, text));
    }
    return response.json() as Promise<T>;
  };
}
