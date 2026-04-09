import QAView from '../views/QAView';

/** IRR (inter-rater) for the active pipeline only (pipeline workflow stage). */
export default function QaStage({ pipeline }) {
  return <QAView lockedPipelineId={pipeline.id} />;
}
