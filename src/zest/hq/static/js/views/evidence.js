import { collection, unavailable, viewHeader } from "./common.js";
export function render(context) {
  if (!context.analysis) return `${viewHeader("Evidence Chain", "Observation to human-reviewed Finding lineage.")}${unavailable()}`;
  const a = context.analysis;
  return `${viewHeader("Evidence Chain", "Lineage is displayed only where persisted IDs prove the relationship.")}${collection(a, ["evidence_chain", "evidence"], "Evidence", context, ["evidence_id", "polarity", "created_at"])}${collection(a, ["evidence_chain", "evidence_admissions"], "Evidence admissions", context, ["admission_id", "outcome", "created_at"])}${collection(a, ["evidence_chain", "candidates"], "Candidates", context, ["candidate_id", "state", "created_at"])}${collection(a, ["evidence_chain", "verifications"], "Verifications", context, ["verification_id", "state", "created_at"])}${collection(a, ["evidence_chain", "finding_proposals"], "Finding proposals", context, ["proposal_id", "state", "created_at"])}${collection(a, ["evidence_chain", "findings"], "Findings", context, ["finding_id", "classification", "created_at"])}`;
}
