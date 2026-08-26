"""Authorize http.transaction origin/path against compiled Core scope. Not a grant."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from zest.application.authorized_network_envelope import (
    AuthorizedNetworkEnvelope,
    derive_authorized_network_envelope,
)
from zest.application.program_research_context import (
    ProgramPolicyView,
    derive_loopback_only,
)
from zest.core.enums import ReasonCode, ScopeDecision
from zest.core.scope import ScopeCheck, ScopeEvaluationInput, ScopeRuleMatch
from zest.core.scope_compiler import CompiledScope, evaluate_scope_candidate
from zest.platform.url_normalize import normalize_url
from zest.research.types import ExperimentPlan
from zest.tools.browser_page_policy import validate_browser_page_arguments
from zest.tools.capabilities import (
    BROWSER_PAGE_CAPABILITY,
    HTTP_AUTHENTICATION_CAPABILITY,
    HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
    HTTP_RAW_EXCHANGE_CAPABILITY,
    HTTP_STATE_TRANSITION_CAPABILITY,
    HTTP_TRANSACTION_CAPABILITY,
)
from zest.tools.http_authentication_policy import validate_http_authentication_arguments
from zest.tools.http_raw_exchange_policy import validate_http_raw_exchange_arguments
from zest.tools.http_transaction_policy import validate_http_transaction_arguments
from zest.tools.registry import ArgumentValidationIssue

HTTP_SCOPE_CAPABILITIES = frozenset(
    {
        HTTP_TRANSACTION_CAPABILITY,
        HTTP_AUTHENTICATION_CAPABILITY,
        BROWSER_PAGE_CAPABILITY,
        HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
        HTTP_STATE_TRANSITION_CAPABILITY,
        HTTP_RAW_EXCHANGE_CAPABILITY,
    }
)


@dataclass(frozen=True)
class HttpTransactionScopeDecision:
    accepted: bool
    reason_code: ReasonCode | None
    input_rejected: bool = False
    scope_check: ScopeCheck | None = None
    envelope: AuthorizedNetworkEnvelope | None = None


def authorize_http_transaction_plan(
    plan: ExperimentPlan,
    compiled_scope: CompiledScope | None,
    program_policy: ProgramPolicyView | None = None,
) -> HttpTransactionScopeDecision:
    """Evaluate the typed HTTP destination. authorized_origin is not itself authority."""

    if plan.required_capability == HTTP_AUTHENTICATION_CAPABILITY:
        issue = validate_http_authentication_arguments(plan.action, plan.arguments)
        if issue is not None:
            return _from_argument_issue(issue)
        return _authorize_origin_path(plan, compiled_scope, program_policy)
    if plan.required_capability == BROWSER_PAGE_CAPABILITY:
        issue = validate_browser_page_arguments(plan.action, plan.arguments)
        if issue is not None:
            return _from_argument_issue(issue)
        return _authorize_origin_path(plan, compiled_scope, program_policy)
    if plan.required_capability in {
        HTTP_AUTHORIZATION_DIFFERENTIAL_CAPABILITY,
        HTTP_STATE_TRANSITION_CAPABILITY,
    }:
        return _authorize_origin_path(plan, compiled_scope, program_policy)
    if plan.required_capability == HTTP_RAW_EXCHANGE_CAPABILITY:
        issue = validate_http_raw_exchange_arguments(plan.action, plan.arguments)
        if issue is not None:
            return _from_argument_issue(issue)
        return _authorize_origin_path(plan, compiled_scope, program_policy)
    if plan.required_capability != HTTP_TRANSACTION_CAPABILITY:
        return HttpTransactionScopeDecision(accepted=True, reason_code=None)
    issue = validate_http_transaction_arguments(plan.action, plan.arguments)
    if issue is not None:
        return _from_argument_issue(issue)
    return _authorize_origin_path(plan, compiled_scope, program_policy)


def scope_evaluation_from_compiled_check(
    check: ScopeCheck,
    compiled: CompiledScope,
) -> ScopeEvaluationInput:
    """Turn one compiled-scope evaluation into Core's ScopeEvaluationInput."""

    by_id = {rule.rule_id: rule for rule in compiled.rules}
    matches = []
    for rule_id in check.matched_rule_ids:
        rule = by_id.get(rule_id)
        if rule is None:
            continue
        matches.append(
            ScopeRuleMatch(rule.rule_id, rule.effect, True, rule.source_reference)
        )
    return ScopeEvaluationInput(
        matches=tuple(matches),
        ambiguous=check.decision is ScopeDecision.REQUIRE_HUMAN_REVIEW,
    )


def _authorize_origin_path(
    plan: ExperimentPlan,
    compiled_scope: CompiledScope | None,
    program_policy: ProgramPolicyView | None = None,
) -> HttpTransactionScopeDecision:
    if compiled_scope is None:
        return HttpTransactionScopeDecision(
            accepted=False,
            reason_code=ReasonCode.SCOPE_NOT_EXPLICITLY_ALLOWED,
        )
    origin = str(plan.arguments["authorized_origin"]).strip().rstrip("/")
    path = str(plan.arguments.get("path", "/"))
    candidate = normalize_url(_request_url(origin, path))
    check = evaluate_scope_candidate(candidate, compiled_scope)
    if check.decision is ScopeDecision.ALLOW:
        loopback_only = derive_loopback_only(
            program_policy=program_policy,
            compiled_scope=compiled_scope,
        )
        return HttpTransactionScopeDecision(
            accepted=True,
            reason_code=None,
            scope_check=check,
            envelope=derive_authorized_network_envelope(
                candidate,
                compiled_scope,
                check,
                loopback_only=loopback_only,
            ),
        )
    return HttpTransactionScopeDecision(
        accepted=False,
        reason_code=check.reason_code,
        scope_check=check,
    )


def http_transaction_request_url(plan: ExperimentPlan) -> str:
    origin = str(plan.arguments["authorized_origin"]).strip().rstrip("/")
    path = str(plan.arguments.get("path", "/"))
    query = plan.arguments.get("query") or {}
    url = _request_url(origin, path)
    if isinstance(query, dict) and query:
        encoded = urlencode(sorted((str(key), str(value)) for key, value in query.items()))
        return f"{url}?{encoded}"
    return url


def _request_url(origin: str, path: str) -> str:
    parsed = urlsplit(origin)
    if parsed.path not in {"", "/"}:
        return f"{origin}{path}" if path.startswith("/") else f"{origin}/{path}"
    return f"{origin}{path}"


def _from_argument_issue(issue: ArgumentValidationIssue) -> HttpTransactionScopeDecision:
    if issue.reason_code == "UNEXPECTED_ARGUMENT" and "session_context_reference" in issue.message:
        return HttpTransactionScopeDecision(
            accepted=False,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            input_rejected=True,
        )
    if issue.reason_code in {"INVALID_ARGUMENT_TYPE", "UNEXPECTED_ARGUMENT", "MISSING_REQUIRED_ARGUMENT"}:
        return HttpTransactionScopeDecision(
            accepted=False,
            reason_code=ReasonCode.SCHEMA_MISMATCH,
            input_rejected=True,
        )
    return HttpTransactionScopeDecision(accepted=False, reason_code=ReasonCode.SCHEMA_MISMATCH)
