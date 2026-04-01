// ── Shared components ─────────────────────────────────────────────────────────

function Terminal({ lines, className = '' }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);
  return (
    <div ref={ref}
      className={`terminal bg-gray-900 text-green-400 p-4 h-96 overflow-y-auto whitespace-pre-wrap ${className}`}>
      {lines.length === 0
        ? <span className="text-gray-600">Waiting for output...</span>
        : lines.join('\n')}
    </div>
  );
}

// ── Login ─────────────────────────────────────────────────────────────────────

function LoginOverlay({ visible, onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  if (!visible) return null;

  const submit = async e => {
    e.preventDefault();
    setError('');
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const d = await res.json();
      if (!res.ok) { setError(d.error || 'Login failed'); return; }
      onLogin(d);
    } catch (err) { setError(err.message); }
  };

  return (
    <div className="fixed inset-0 bg-gray-900/80 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-2xl p-8 w-full max-w-sm">
        <h1 className="text-2xl font-bold mb-1">OpenAlex Pipeline</h1>
        <p className="text-gray-500 text-sm mb-6">Sign in to continue</p>
        <form onSubmit={submit} className="space-y-4">
          <input type="text" placeholder="Username" value={username}
            onChange={e => setUsername(e.target.value)} required autoFocus
            className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          <input type="password" placeholder="Password" value={password}
            onChange={e => setPassword(e.target.value)} required
            className="w-full border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500" />
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <button type="submit"
            className="w-full bg-blue-600 text-white py-2 rounded-lg font-semibold hover:bg-blue-700 transition">
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}

// ── Nav ───────────────────────────────────────────────────────────────────────

function NavBar({ me, view, runningCount, onShowHome, onShowJobs, onLogout }) {
  return (
    <nav className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between sticky top-0 z-40 shadow-sm">
      <button onClick={onShowHome}
        className="flex items-center gap-2 font-bold text-lg text-blue-700 hover:text-blue-900 transition">
        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
            d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
        </svg>
        OpenAlex Pipeline
      </button>
      <div className="flex items-center gap-3">
        <button onClick={onShowJobs}
          className={`text-sm transition px-3 py-1 rounded border flex items-center gap-1.5
            ${view === 'jobs' ? 'text-blue-700 border-blue-300 bg-blue-50' : 'text-gray-500 hover:text-blue-700 border-gray-200 hover:border-blue-300'}`}>
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2"
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
          </svg>
          Jobs
          {runningCount > 0 && (
            <span className="text-xs bg-yellow-400 text-yellow-900 font-bold rounded-full px-1.5 animate-pulse">
              {runningCount}
            </span>
          )}
        </button>
        <span className="text-sm text-gray-500">{me?.username}</span>
        <button onClick={onLogout}
          className="text-sm text-gray-500 hover:text-red-600 transition px-3 py-1 rounded border border-gray-200 hover:border-red-300">
          Sign out
        </button>
      </div>
    </nav>
  );
}

