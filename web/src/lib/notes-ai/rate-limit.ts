const WINDOW_MS = 60_000;
const MAX_REQUESTS = 10;

type RateLimitEntry = { count: number; resetAt: number };

const entries = new Map<string, RateLimitEntry>();

export function checkNotesAiRateLimit(key: string, now = Date.now()) {
  const current = entries.get(key);
  if (!current || current.resetAt <= now) {
    entries.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return { allowed: true, remaining: MAX_REQUESTS - 1, retryAfterSeconds: 0 };
  }
  if (current.count >= MAX_REQUESTS) {
    return {
      allowed: false,
      remaining: 0,
      retryAfterSeconds: Math.max(1, Math.ceil((current.resetAt - now) / 1_000)),
    };
  }
  current.count += 1;
  return {
    allowed: true,
    remaining: MAX_REQUESTS - current.count,
    retryAfterSeconds: 0,
  };
}

export function resetNotesAiRateLimitForTests() {
  entries.clear();
}
