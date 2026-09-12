from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "src/zest/application/discovery/runner.py"

text = RUNNER.read_text(encoding="utf-8")


def replace_once(label: str, old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    "import followup compiler",
    "from zest.application.discovery.control_events import ingest_control_event_from_worker_result\n"
    "from zest.application.discovery.project import (\n"
    "    reconcile_missing_projections,\n"
    ")\n",
    "from zest.application.discovery.control_events import ingest_control_event_from_worker_result\n"
    "from zest.application.discovery.reauthorization_followup import (\n"
    "    compile_allowed_same_origin_redirect_frontier,\n"
    ")\n"
    "from zest.application.discovery.project import (\n"
    "    reconcile_missing_projections,\n"
    ")\n",
)

replace_once(
    "compile positive reauthorization followup",
    """        reauthorization_explicit_oos = False
        if (
            loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
            and start.compiled_scope is not None
            and isinstance(loop.reauthorization_request, Mapping)
        ):
            reauthorization_check = evaluate_reauthorization_request(
                loop.reauthorization_request, start.compiled_scope
            )
            reauthorization_explicit_oos = _explicit_out_of_scope(reauthorization_check)
""",
    """        reauthorization_explicit_oos = False
        reauthorization_resolved = False
        reauthorization_followup = None
        if (
            loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
            and start.compiled_scope is not None
            and isinstance(loop.reauthorization_request, Mapping)
        ):
            reauthorization_check = evaluate_reauthorization_request(
                loop.reauthorization_request, start.compiled_scope
            )
            reauthorization_explicit_oos = _explicit_out_of_scope(reauthorization_check)
            reauthorization_followup = compile_allowed_same_origin_redirect_frontier(
                loop.reauthorization_request,
                reauthorization_check,
                record=record,
                normalized_origin=start.config.normalized_origin,
            )
""",
)

anchor = "        reauthorization_followup = None\n"
pos = text.find(anchor)
if pos < 0:
    raise SystemExit("capture control event: reauthorization anchor missing")
prefix = text[:pos]
suffix = text[pos:]
old_loop = """        with self._uow_factory.open() as uow:
            for result in uow.worker_results.list_for_research_run(research_run_id):
                ingest_control_event_from_worker_result(
                    uow, result, created_at=now, target_reference=target_reference
                )
            reconcile_missing_projections(
"""
new_loop = """        with self._uow_factory.open() as uow:
            reauthorization_control_event_id = None
            for result in uow.worker_results.list_for_research_run(research_run_id):
                control_event = ingest_control_event_from_worker_result(
                    uow, result, created_at=now, target_reference=target_reference
                )
                if (
                    loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED
                    and loop.worker_result_id is not None
                    and result.worker_result_id == loop.worker_result_id
                    and control_event is not None
                ):
                    reauthorization_control_event_id = control_event.control_event_id
            reconcile_missing_projections(
"""
if suffix.count(old_loop) != 1:
    raise SystemExit(
        "capture control event: expected one post-execution ingest loop, "
        f"found {suffix.count(old_loop)}"
    )
suffix = suffix.replace(old_loop, new_loop, 1)
text = prefix + suffix

replace_once(
    "resolve allowed reauthorization",
    """                if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
                    if reauthorization_explicit_oos:
                        uow.experiments.set_execution_state(
                            experiment_id, ExperimentExecutionState.BLOCKED.value
                        )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "BLOCKED_SCOPE",
                            now,
                            reason_code="REAUTHORIZATION_TARGET_OUT_OF_SCOPE",
                        )
                    else:
                        self._append_event(
                            uow, record.frontier_id, "AWAITING_REAUTHORIZATION", now
                        )
""",
    """                if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
                    if reauthorization_explicit_oos:
                        uow.experiments.set_execution_state(
                            experiment_id, ExperimentExecutionState.BLOCKED.value
                        )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "BLOCKED_SCOPE",
                            now,
                            reason_code="REAUTHORIZATION_TARGET_OUT_OF_SCOPE",
                        )
                    elif (
                        reauthorization_followup is not None
                        and reauthorization_control_event_id is not None
                    ):
                        # The original request has already stopped at the redirect.
                        # Close that control branch; never redispatch its attempt.
                        uow.experiments.set_execution_state(
                            experiment_id, ExperimentExecutionState.BLOCKED.value
                        )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "AWAITING_REAUTHORIZATION",
                            now,
                            reason_code="CORE_REEVALUATION_REQUIRED",
                        )
                        existing_followup = next(
                            (
                                item
                                for item in uow.frontier_items.list_for_research_run(
                                    research_run_id
                                )
                                if item.dedupe_identity
                                == reauthorization_followup.dedupe_identity
                            ),
                            None,
                        )
                        if existing_followup is None:
                            self._insert_control_frontier(
                                uow,
                                reauthorization_followup,
                                created_at=now,
                                control_event_id=reauthorization_control_event_id,
                            )
                        self._append_event(
                            uow,
                            record.frontier_id,
                            "SUPERSEDED",
                            now,
                            reason_code="REAUTHORIZATION_ALLOWED_FRESH_WORK",
                        )
                        reauthorization_resolved = True
                    else:
                        self._append_event(
                            uow, record.frontier_id, "AWAITING_REAUTHORIZATION", now
                        )
""",
)

replace_once(
    "return resolved reauthorization",
    """        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                (
                    "BLOCKED_SCOPE"
                    if reauthorization_explicit_oos
                    else "REAUTHORIZATION_REQUIRED"
                ),
                record.frontier_id,
                experiment_id,
                worker_invoked,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
                worker_status=loop.status.value,
                observation_id=observation_id,
                discovery_exhausted=False,
            )
""",
    """        if loop.status is ResearchLoopStatus.REAUTHORIZATION_REQUIRED:
            reauthorization_stop_reason = (
                "BLOCKED_SCOPE"
                if reauthorization_explicit_oos
                else (
                    bound_stop
                    if reauthorization_resolved
                    else "REAUTHORIZATION_REQUIRED"
                )
            )
            return SurfaceDiscoveryCycleResult(
                research_run_id,
                reauthorization_stop_reason,
                record.frontier_id,
                experiment_id,
                worker_invoked,
                eligible_before=eligible_before,
                eligible_after=eligible_after,
                selected_goal_kind=record.goal_kind,
                selected_path=record.candidate_path,
                compiled_capability=plan.required_capability,
                worker_status=loop.status.value,
                observation_id=observation_id,
                discovery_exhausted=(
                    audit.discovery_exit_allowed
                    if reauthorization_resolved
                    and reauthorization_stop_reason is None
                    else False
                ),
            )
""",
)

replace_once(
    "insert control-derived frontier helper",
    """        self._append_event(uow, item.frontier_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, item.frontier_id, "ELIGIBLE", created_at, sequence=2)

    def _ensure_hypothesis(self, uow, research_run_id: str, *, created_at) -> None:
""",
    """        self._append_event(uow, item.frontier_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, item.frontier_id, "ELIGIBLE", created_at, sequence=2)

    def _insert_control_frontier(
        self,
        uow,
        item: FrontierItem,
        *,
        created_at,
        control_event_id: str,
    ) -> None:
        \"\"\"Persist fresh work derived from a control event, not from seed authority.\"\"\"

        uow.frontier_items.insert(
            FrontierItemRecord(
                frontier_id=item.frontier_id,
                research_run_id=item.research_run_id,
                strategy_version=item.strategy_version,
                goal_kind=item.goal_kind.value,
                candidate_origin=item.candidate_origin,
                candidate_path=item.candidate_path,
                identity_id=item.identity_id,
                proposed_capability=item.proposed_capability,
                proposed_action=item.proposed_action,
                expected_side_effect=item.expected_side_effect,
                budget_class=item.budget_class,
                structural_signature=item.structural_signature,
                dedupe_identity=item.dedupe_identity,
                created_at=created_at,
                session_context_id=item.session_context_id,
                scope_hint=item.scope_hint,
                attributes=item.attributes,
                current_state="ELIGIBLE",
                state_version=2,
            )
        )
        uow.frontier_sources.insert(
            FrontierSourceRecord(
                source_row_id=new_opaque_id(),
                research_run_id=item.research_run_id,
                frontier_id=item.frontier_id,
                created_at=created_at,
                control_event_id=control_event_id,
            )
        )
        self._append_event(uow, item.frontier_id, "CREATED", created_at, sequence=1)
        self._append_event(uow, item.frontier_id, "ELIGIBLE", created_at, sequence=2)

    def _ensure_hypothesis(self, uow, research_run_id: str, *, created_at) -> None:
""",
)

RUNNER.write_text(text, encoding="utf-8")
print("PATCH=PASS")
print(f"RUNNER={RUNNER}")
print("SEMANTICS=Core ALLOW + same-origin + read-only redirect -> fresh frontier")
print("OLD_ATTEMPT_REDISPATCH=NO")
print("CROSS_ORIGIN_AUTO_ALLOW=NO")
print("MUTATING_REDIRECT_AUTO_ALLOW=NO")
