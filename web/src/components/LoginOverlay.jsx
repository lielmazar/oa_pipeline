import { useState } from 'react';
import { apiFetch } from '../api/client';

export default function LoginOverlay({ visible, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError]       = useState('');
  const [loading, setLoading]   = useState(false);

  if (!visible) return null;

  const submit = async e => {
    e.preventDefault();
    setLoading(true); setError('');
    try {
      const res = await apiFetch('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || 'Invalid credentials'); return; }
      onLogin(data);
    } catch (err) {
      setError(err.message || 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-gray-900/80 flex items-center justify-center z-50">
      <form onSubmit={submit}
        className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-8 space-y-5">
        <h1 className="text-xl font-bold text-gray-900">OA Pipeline</h1>
        {error && <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">{error}</p>}
        <label className="block space-y-1">
          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Username</span>
          <input autoFocus value={username} onChange={e => setUsername(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
        </label>
        <label className="block space-y-1">
          <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">Password</span>
          <input type="password" value={password} onChange={e => setPassword(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
        </label>
        <button type="submit" disabled={loading}
          className="w-full bg-blue-600 text-white rounded-lg py-2.5 text-sm font-semibold hover:bg-blue-700 disabled:opacity-50 transition">
          {loading ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}
