const KEY = 'oa-analysis-stage-prefs';

export function loadAnalysisStagePrefs(pipelineId) {
  try {
    const raw = sessionStorage.getItem(KEY);
    const o = raw ? JSON.parse(raw) : {};
    const p = o[String(pipelineId)] || {};
    return {
      subsetId: p.subsetId != null ? Number(p.subsetId) : null,
      datasetOpen: p.datasetOpen !== false,
    };
  } catch {
    return { subsetId: null, datasetOpen: true };
  }
}

export function saveAnalysisStagePrefs(pipelineId, patch) {
  try {
    const raw = sessionStorage.getItem(KEY);
    const o = raw ? JSON.parse(raw) : {};
    const cur = o[String(pipelineId)] || {};
    o[String(pipelineId)] = { ...cur, ...patch };
    sessionStorage.setItem(KEY, JSON.stringify(o));
  } catch {
    /* ignore */
  }
}

const QA_IRR_SUBSET = (id) => `oa-qa-irr-subset-${id}`;

/** Persist QA / IRR dataset dropdown per pipeline. */
export function loadQaIrrSubsetId(pipelineId) {
  try {
    return sessionStorage.getItem(QA_IRR_SUBSET(String(pipelineId))) ?? '';
  } catch {
    return '';
  }
}

export function saveQaIrrSubsetId(pipelineId, subsetIdStr) {
  try {
    sessionStorage.setItem(QA_IRR_SUBSET(String(pipelineId)), String(subsetIdStr ?? ''));
  } catch {
    /* ignore */
  }
}
