// Shared API client — thin wrapper around fetch with 401 handling.

let _onUnauth = null;

export function setUnauthHandler(fn) {
  _onUnauth = fn;
}

export async function apiFetch(url, opts = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (res.status === 401) {
    if (_onUnauth) _onUnauth();
    throw new Error('Session expired');
  }
  return res;
}

export function timeAgo(ts) {
  if (!ts) return '';
  const secs = Math.floor(Date.now() / 1000 - ts);
  if (secs < 60)    return `${secs}s ago`;
  if (secs < 3600)  return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function stripAnsi(str) {
  return String(str ?? '')
    .replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')
    .replace(/\x1b[()][A-Z]/g, '')
    .replace(/\r/g, '');
}
