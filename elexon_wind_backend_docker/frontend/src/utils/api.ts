const BASE = '/api/v1';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<{ data: T; processTimeMs: number }> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `API error ${res.status}`);
  }
  const processTimeMs = parseFloat(res.headers.get('X-Process-Time-Ms') || '0');
  const data = await res.json() as T;
  return { data, processTimeMs };
}
