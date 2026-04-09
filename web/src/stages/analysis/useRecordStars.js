import { useState, useCallback } from 'react';
import { isStarred, toggleStarred } from './recordStars.js';

export function useRecordStars(pipelineId) {
  const [rev, setRev] = useState(0);
  const check = useCallback(
    (openalexId) => isStarred(pipelineId, openalexId),
    [pipelineId, rev],
  );
  const toggle = useCallback(
    (openalexId) => {
      toggleStarred(pipelineId, openalexId);
      setRev((r) => r + 1);
    },
    [pipelineId],
  );
  return { isStarred: check, toggleStar: toggle };
}
