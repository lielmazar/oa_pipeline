const KEY = (pipelineId) => `oa-pipeline-record-stars-${pipelineId}`;

export function loadStarredSet(pipelineId) {
  try {
    const raw = localStorage.getItem(KEY(pipelineId));
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch {
    return new Set();
  }
}

export function saveStarredSet(pipelineId, set) {
  try {
    localStorage.setItem(KEY(pipelineId), JSON.stringify([...set]));
  } catch {
    /* ignore */
  }
}

export function toggleStarred(pipelineId, openalexId) {
  const id = String(openalexId ?? '');
  if (!id) return false;
  const s = loadStarredSet(pipelineId);
  if (s.has(id)) {
    s.delete(id);
  } else {
    s.add(id);
  }
  saveStarredSet(pipelineId, s);
  return s.has(id);
}

export function isStarred(pipelineId, openalexId) {
  return loadStarredSet(pipelineId).has(String(openalexId ?? ''));
}
