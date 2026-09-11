export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...options.headers },
    cache: 'no-store',
  });
  let data: any;
  try { data = await response.json(); } catch { throw new ApiError(response.status, '服务暂时不可用，请稍后重试'); }
  if (!response.ok) {
    const message = typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail)
      ? data.detail.map((item: any) => item.msg).join('；') : '操作失败，请重试';
    throw new ApiError(response.status, message);
  }
  return data;
}
export function write(method: string, data: unknown): RequestInit { return { method, body: JSON.stringify(data) }; }
export function errorText(error: unknown) { return error instanceof Error ? error.message : '操作失败，请重试'; }
export interface Question { id: number; text: string; hint: string; status: 'open' | 'paused'; version: number; rule_id: number }
export interface Word { term: string; count: number }
export interface Rules { stopwords: string[]; phrases: string[]; synonyms: Record<string, string> }
export interface Stats { question: Question; version: number; valid_count: number; total_count: number; word_count: number; words: Word[]; rules: Rules; rule_version: number; latest_at: string | null; job: { status: string; version: number }; updated_at: string }
export interface Answer { id: number; raw_text: string; status: string; created_at: string }
