export default function NavBar({ me, view, filterRunning, onShowHome, onShowQA, onLogout }) {
  const btn = (label, active, onClick) => (
    <button onClick={onClick}
      className={`text-sm px-3 py-1.5 rounded-lg transition ${
        active ? 'bg-white/20 font-semibold' : 'hover:bg-white/10'
      }`}>
      {label}
    </button>
  );

  return (
    <nav className="h-12 bg-blue-700 text-white flex items-center px-4 gap-2 sticky top-0 z-30 shadow">
      <span className="font-bold text-sm mr-3 tracking-tight">OA Pipeline</span>
      {btn('Pipelines', view === 'home' || view === 'pipeline', onShowHome)}
      {btn('QA / IRR',  view === 'qa', onShowQA)}
      <div className="ml-auto flex items-center gap-3">
        {filterRunning && (
          <span className="text-xs bg-green-500 text-white px-2 py-0.5 rounded-full animate-pulse">
            filtering…
          </span>
        )}
        <span className="text-xs text-blue-200">{me?.username}</span>
        <button onClick={onLogout}
          className="text-xs px-2 py-1 rounded border border-blue-500 hover:bg-blue-600 transition">
          Log out
        </button>
      </div>
    </nav>
  );
}
