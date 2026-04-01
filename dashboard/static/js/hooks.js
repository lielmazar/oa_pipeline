// ── Hooks ─────────────────────────────────────────────────────────────────────

function useJobStream(jobId, onDone) {
  const [lines, setLines] = useState([]);
  const [status, setStatus] = useState(null);
  const esRef = useRef(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => { onDoneRef.current = onDone; }, [onDone]);

  useEffect(() => {
    if (!jobId) return;
    setLines([]);
    setStatus(null);
    let lastSeq = -1;
    let cancelled = false;

    // Always fetch buffered output first — shows existing lines immediately
    // and gives us the last seq number to resume SSE from.
    async function start() {
      try {
        const r = await fetch(`/api/jobs/${jobId}/output`);
        if (!r.ok || cancelled) return;
        const d = await r.json();
        if (cancelled) return;
        if (d.lines && d.lines.length > 0) {
          setLines(d.lines.map(stripAnsi));
          lastSeq = d.last_seq ?? (d.lines.length - 1);
        }
        setStatus(d.status || null);
        if (d.status !== 'running') {
          // Job already finished — buffered output is complete
          if (onDoneRef.current) onDoneRef.current(d);
          return;
        }
      } catch {}

      // Job is still running — open SSE from after the last buffered line
      connect();
    }

    function connect() {
      if (cancelled) return;
      const es = new EventSource(`/api/jobs/${jobId}/stream?after=${lastSeq}`);
      esRef.current = es;

      es.addEventListener('line', e => {
        try {
          const d = JSON.parse(e.data);
          if (d.seq > lastSeq) {
            setLines(prev => [...prev, stripAnsi(d.text)]);
            lastSeq = d.seq;
          }
        } catch {
          setLines(prev => [...prev, stripAnsi(e.data)]);
        }
      });

      es.addEventListener('done', e => {
        es.close(); esRef.current = null;
        try {
          const d = JSON.parse(e.data);
          setStatus(d.status);
          if (onDoneRef.current) onDoneRef.current(d);
        } catch { setStatus('done'); }
      });

      es.onerror = () => {
        es.close(); esRef.current = null;
        if (cancelled) return;
        setTimeout(async () => {
          if (cancelled) return;
          try {
            const r = await fetch(`/api/jobs/${jobId}/output`);
            if (!r.ok || cancelled) return;
            const d = await r.json();
            if (cancelled) return;
            // Catch up on any lines we missed while disconnected
            if (d.lines && d.lines.length > 0) {
              const newLastSeq = d.last_seq ?? (d.lines.length - 1);
              if (newLastSeq > lastSeq) {
                // Append only new lines
                const newLines = d.lines.slice(lastSeq + 1);
                if (newLines.length > 0) setLines(prev => [...prev, ...newLines.map(stripAnsi)]);
                lastSeq = newLastSeq;
              }
            }
            if (d.status === 'running') {
              connect();
            } else {
              setStatus(d.status);
              if (onDoneRef.current) onDoneRef.current(d);
            }
          } catch {}
        }, 2000);
      };
    }

    start();
    return () => {
      cancelled = true;
      if (esRef.current) { esRef.current.close(); esRef.current = null; }
    };
  }, [jobId]);

  return { lines, status };
}

function useInterval(cb, delay) {
  const cbRef = useRef(cb);
  useEffect(() => { cbRef.current = cb; }, [cb]);
  useEffect(() => {
    if (delay == null) return;
    const id = setInterval(() => cbRef.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

