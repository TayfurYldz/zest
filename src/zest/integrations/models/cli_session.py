"""Authenticated CLI/session ModelPort adapter. Codex CLI is an AGENT_RUNTIME.

Documented flags only. Does not scrape credentials. Does not use --yolo.
Models are operational configuration, not architectural identity.
A diagnostic echo that ignores ModelCallRequest is not MODELPORT_COMPATIBLE.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Callable, Mapping, Protocol

from zest.platform.argv_process import (
    ArgvProcessConfig,
    ArgvProcessResult,
    ArgvProcessStatus,
    resolve_executable,
    run_argv,
)
from zest.platform.readiness import RuntimeReadiness, readiness_from_flags
from zest.integrations.models.json_schemas import (
    DIAGNOSTIC_OUTPUT_SCHEMA,
    decode_transport_output_for_request,
    schema_for_request,
)
from zest.research.model_port import (
    ContentPolicyBlockedError,
    ModelCallRequest,
    ModelCallResult,
    ModelPortError,
    ModelRole,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUsageLimitError,
    ProviderTimeoutError,
    RuntimeCancelledError,
    RuntimeProcessError,
    RuntimeUnavailableError,
    StructuredOutputTransportError,
)
from zest.research.model_runtime import (
    RuntimeClass,
    RuntimeKind,
    RuntimeOutcome,
    cli_session_runtime_identity,
)
from zest.research.output_contracts import diagnostic_readiness_instructions
from zest.tools.capabilities import CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY

UNRESTRICTED_MARKERS = frozenset({"*", "all", "unrestricted", "shell", "yolo", "danger-full-access"})
FORBIDDEN_CLI_FLAGS = frozenset(
    {
        "--yolo",
        "--full-auto",
        "danger-full-access",
        "dangerously-bypass-approvals-and-sandbox",
    }
)
CODEX_USAGE_QUOTA_MARKERS = (
    "hit your usage limit",
    "usage limit",
    "quota exceeded",
    "insufficient_quota",
)
CODEX_TRANSIENT_RATE_LIMIT_MARKERS = (
    "rate limit",
    "ratelimit",
    "429",
)
CODEX_USAGE_LIMIT_MARKERS = (
    *CODEX_USAGE_QUOTA_MARKERS,
    *CODEX_TRANSIENT_RATE_LIMIT_MARKERS,
)
USAGE_LIMIT_DETAIL = "Codex CLI usage/rate limit reached"
ALLOWED_SANDBOX = "read-only"
CODEX_DIAGNOSTIC_TIMEOUT_MS = 60_000
CODEX_SKIP_GIT_REPO_CHECK_FLAG = "--skip-git-repo-check"
NON_GIT_WORKING_DIRECTORY_DETAIL = (
    "Codex CLI refused a non-git working directory; isolated diagnostics require "
    "--skip-git-repo-check"
)
CODEX_NON_GIT_WORKING_DIRECTORY_MARKERS = (
    "not a git repository",
    "not inside a git repository",
    "must be run from a git repository",
    "must be run inside a git repository",
    "outside a git repository",
    "requires a git repository",
)
CODEX_CONTENT_POLICY_REFUSAL_MARKERS = (
    "content_policy",
    "content_policy_violation",
    "content_filter",
    "safety_refusal",
    "content policy violation",
    "content policy refusal",
    "content policy blocked",
    "blocked by content policy",
    "moderation refusal",
    "refused by moderation",
    "model refused for safety",
    "response blocked by safety system",
)
CODEX_MODELS_ENV = "ZEST_CODEX_MODELS"
CODEX_EXECUTABLE_ENV = "ZEST_CODEX_EXECUTABLE"
DEFAULT_CODEX_EXECUTABLE = "codex"
CONFIGURATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
STRUCTURED_OUTPUT_SCHEMA = DIAGNOSTIC_OUTPUT_SCHEMA
DIAGNOSTIC_APPLICATION_OUTPUT = {"diagnostic": True}

# Operational defaults only. Override with ZEST_CODEX_MODELS.
DEFAULT_CODEX_MODEL_ENTRIES = (
    "codex-cli-terra=gpt-5.6-terra",
    "codex-cli-gpt55=gpt-5.5",
)


class CodexCliConfigurationError(ValueError):
    """Invalid Codex CLI runtime configuration. Fail closed. Not a research conclusion."""


class CodexArgvRunner(Protocol):
    def __call__(
        self,
        argv: tuple[str, ...],
        *,
        stdin_bytes: bytes | None = None,
    ) -> ArgvProcessResult: ...


ArgvRunner = Callable[..., ArgvProcessResult]


@dataclass(frozen=True)
class CodexCliRuntimeConfiguration:
    """One replaceable Codex CLI ModelRuntime configuration. Not vendor lock-in."""

    configuration_id: str
    model: str
    executable: str
    sandbox: str = ALLOWED_SANDBOX
    ephemeral: bool = True
    ignore_user_config: bool = True
    isolated_non_repository_cwd: bool = True
    skip_git_repo_check: bool = True
    runtime_kind: str = RuntimeKind.CLI_SESSION.value
    runtime_class: str = RuntimeClass.AGENT_RUNTIME.value

    def __post_init__(self) -> None:
        if not CONFIGURATION_ID_PATTERN.fullmatch(self.configuration_id):
            raise CodexCliConfigurationError("configuration_id is invalid")
        if not isinstance(self.model, str) or not self.model.strip():
            raise CodexCliConfigurationError("model must be a non-empty string")
        if not isinstance(self.executable, str) or not self.executable.strip():
            raise CodexCliConfigurationError("executable must be a non-empty string")
        if self.sandbox != ALLOWED_SANDBOX:
            raise CodexCliConfigurationError("sandbox must be read-only")
        if self.ephemeral is not True:
            raise CodexCliConfigurationError("ephemeral must be true")
        if self.ignore_user_config is not True:
            raise CodexCliConfigurationError("ignore_user_config must be true")
        if self.isolated_non_repository_cwd is not True:
            raise CodexCliConfigurationError("isolated_non_repository_cwd must be true")
        if self.skip_git_repo_check is not True:
            raise CodexCliConfigurationError("skip_git_repo_check must be true")
        if self.runtime_kind != RuntimeKind.CLI_SESSION.value:
            raise CodexCliConfigurationError("runtime_kind must be CLI_SESSION")
        if self.runtime_class != RuntimeClass.AGENT_RUNTIME.value:
            raise CodexCliConfigurationError("runtime_class must be AGENT_RUNTIME")
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "executable", self.executable.strip())

    def runtime_configuration(self) -> dict[str, object]:
        return {
            "sandbox": self.sandbox,
            "ephemeral": self.ephemeral,
            "ignore_user_config": self.ignore_user_config,
            "isolated_non_repository_cwd": self.isolated_non_repository_cwd,
            "skip_git_repo_check": self.skip_git_repo_check,
            "executable": self.executable,
        }


@dataclass(frozen=True)
class CliRuntimeAvailability:
    available: bool
    outcome: RuntimeOutcome
    executable: str | None
    version: str | None
    detail: str
    readiness: RuntimeReadiness | None = None
    configuration_id: str | None = None
    model: str | None = None
    configuration_fingerprint: str | None = None

    def to_mapping(self) -> dict[str, str | bool | None]:
        payload: dict[str, str | bool | None] = {
            "available": self.available,
            "outcome": self.outcome.value,
            "executable": self.executable,
            "version": self.version,
            "detail": self.detail,
            "configuration_id": self.configuration_id,
            "model": self.model,
            "configuration_fingerprint": self.configuration_fingerprint,
            "unavailable_is_not_pass": True,
        }
        if self.readiness is not None:
            mapping = self.readiness.to_mapping()
            payload["installed"] = mapping["installed"]
            payload["version_known"] = mapping["version_known"]
            payload["auth_ready"] = mapping["auth_ready"]
            payload["diagnostic_ready"] = mapping["diagnostic_ready"]
            payload["modelport_compatible"] = mapping["modelport_compatible"]
            payload["benchmark_compatible"] = mapping["benchmark_compatible"]
            payload["stage"] = mapping["stage"]
        return payload


def derive_codex_configuration_id(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.strip().lower()).strip("-")
    if not slug:
        raise CodexCliConfigurationError("model produced an empty configuration id")
    return f"codex-cli-{slug}"


def parse_codex_model_configurations(
    raw: str | None,
    *,
    executable: str = DEFAULT_CODEX_EXECUTABLE,
) -> tuple[CodexCliRuntimeConfiguration, ...]:
    if raw is None or not str(raw).strip():
        entries = DEFAULT_CODEX_MODEL_ENTRIES
    else:
        entries = tuple(part.strip() for part in str(raw).split(","))
    if any(item == "" for item in entries):
        raise CodexCliConfigurationError("empty Codex model configuration entry")
    parsed: list[CodexCliRuntimeConfiguration] = []
    seen_ids: set[str] = set()
    seen_models: set[str] = set()
    for entry in entries:
        if "=" in entry:
            configuration_id, model = entry.split("=", 1)
            configuration_id = configuration_id.strip()
            model = model.strip()
        else:
            model = entry.strip()
            configuration_id = derive_codex_configuration_id(model)
        if not configuration_id or not model:
            raise CodexCliConfigurationError("empty Codex model configuration entry")
        if configuration_id in seen_ids or model in seen_models:
            raise CodexCliConfigurationError("duplicate Codex model configuration")
        seen_ids.add(configuration_id)
        seen_models.add(model)
        parsed.append(
            CodexCliRuntimeConfiguration(
                configuration_id=configuration_id,
                model=model,
                executable=executable,
            )
        )
    if not parsed:
        raise CodexCliConfigurationError("no Codex model configurations")
    return tuple(parsed)


def load_codex_model_configurations(env: Mapping[str, str] | None = None) -> tuple[CodexCliRuntimeConfiguration, ...]:
    source = dict(environ if env is None else env)
    executable = (source.get(CODEX_EXECUTABLE_ENV) or DEFAULT_CODEX_EXECUTABLE).strip()
    if not executable:
        raise CodexCliConfigurationError("executable must be a non-empty string")
    return parse_codex_model_configurations(source.get(CODEX_MODELS_ENV), executable=executable)


def _run(
    runner: ArgvRunner | None,
    argv: tuple[str, ...],
    *,
    stdin_bytes: bytes | None = None,
    timeout_ms: int = 5_000,
    working_directory: Path | None = None,
) -> ArgvProcessResult:
    if runner is not None:
        return runner(argv, stdin_bytes=stdin_bytes)
    return run_argv(
        argv,
        config=ArgvProcessConfig(executable=argv[0], working_directory=working_directory),
        stdin_bytes=stdin_bytes,
        timeout_ms=timeout_ms,
    )


def _codex_exec_argv(
    executable: str,
    model: str,
    schema_path: Path,
    *,
    skip_git_repo_check: bool,
) -> tuple[str, ...]:
    """Build the documented Codex exec argv for a bounded diagnostic request.

    ``--skip-git-repo-check`` is only used for the adapter-owned diagnostic temp
    cwd. The cwd is a freshly created private directory, repository source is not
    exposed through it, the sandbox remains read-only, the session is ephemeral,
    the request asks for structured diagnostic output only, and no tools are
    requested. This is not repository trust and not a production authorization
    bypass.
    """

    argv = (
        executable,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        *((CODEX_SKIP_GIT_REPO_CHECK_FLAG,) if skip_git_repo_check else ()),
        "--sandbox",
        ALLOWED_SANDBOX,
        "-m",
        model,
        "--output-schema",
        str(schema_path),
        "-",
    )
    if any(flag in argv for flag in FORBIDDEN_CLI_FLAGS):
        raise ModelPortError("unrestricted CLI flags are rejected")
    if "--sandbox" in argv:
        sandbox_index = argv.index("--sandbox")
        if sandbox_index + 1 >= len(argv) or argv[sandbox_index + 1] != ALLOWED_SANDBOX:
            raise ModelPortError("sandbox must be read-only")
    return argv


def probe_codex_cli(
    *,
    executable_name: str = DEFAULT_CODEX_EXECUTABLE,
    runner: ArgvRunner | None = None,
    configuration: CodexCliRuntimeConfiguration | None = None,
    live_probe: bool = False,
) -> CliRuntimeAvailability:
    executable_name = configuration.executable if configuration is not None else executable_name
    configuration_id = configuration.configuration_id if configuration is not None else None
    model = configuration.model if configuration is not None else None
    if runner is not None:
        path = executable_name
    else:
        path = resolve_executable(executable_name)
        if path is None:
            readiness = readiness_from_flags(
                installed=False,
                detail="codex executable not found on PATH",
            )
            identity = None
            fingerprint = None
            if configuration is not None:
                identity = cli_session_runtime_identity(
                    adapter_id="codex.cli.session",
                    runtime_id=configuration.configuration_id,
                    session_reference="local-authenticated-cli-session",
                    model_id=configuration.model,
                    runtime_configuration=configuration.runtime_configuration(),
                )
                fingerprint = identity.configuration_fingerprint
            return CliRuntimeAvailability(
                available=False,
                outcome=RuntimeOutcome.UNAVAILABLE,
                executable=None,
                version=None,
                detail=readiness.detail,
                readiness=readiness,
                configuration_id=configuration_id,
                model=model,
                configuration_fingerprint=fingerprint,
            )
    identity = None
    fingerprint = None
    if configuration is not None:
        identity = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id=configuration.configuration_id,
            session_reference="local-authenticated-cli-session",
            model_id=configuration.model,
            runtime_configuration=configuration.runtime_configuration(),
        )
        fingerprint = identity.configuration_fingerprint
    result = _run(runner, (path, "--version"), timeout_ms=5_000)
    if result.status is ArgvProcessStatus.UNAVAILABLE:
        readiness = readiness_from_flags(
            installed=False,
            executable=path,
            detail=result.reason or "codex --version unavailable",
        )
        return CliRuntimeAvailability(
            available=False,
            outcome=RuntimeOutcome.UNAVAILABLE,
            executable=path,
            version=None,
            detail=readiness.detail,
            readiness=readiness,
            configuration_id=configuration_id,
            model=model,
            configuration_fingerprint=fingerprint,
        )
    if result.status is ArgvProcessStatus.TIMED_OUT:
        readiness = readiness_from_flags(
            installed=True,
            executable=path,
            detail="codex --version timed out",
        )
        return CliRuntimeAvailability(
            available=False,
            outcome=RuntimeOutcome.TIMED_OUT,
            executable=path,
            version=None,
            detail=readiness.detail,
            readiness=readiness,
            configuration_id=configuration_id,
            model=model,
            configuration_fingerprint=fingerprint,
        )
    if result.status is not ArgvProcessStatus.COMPLETED:
        readiness = readiness_from_flags(
            installed=True,
            executable=path,
            detail=_safe_process_failure_detail(result),
        )
        return CliRuntimeAvailability(
            available=False,
            outcome=RuntimeOutcome.PROCESS_FAILED,
            executable=path,
            version=None,
            detail=readiness.detail,
            readiness=readiness,
            configuration_id=configuration_id,
            model=model,
            configuration_fingerprint=fingerprint,
        )
    version = (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr).strip() else "unknown"
    if identity is not None:
        identity = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id=configuration.configuration_id,
            runtime_version=version,
            session_reference="local-authenticated-cli-session",
            model_id=configuration.model,
            runtime_configuration=configuration.runtime_configuration(),
        )
        fingerprint = identity.configuration_fingerprint
    auth = _run(runner, (path, "login", "status"), timeout_ms=5_000)
    auth_ready = auth.status is ArgvProcessStatus.COMPLETED
    auth_detail = "codex login status succeeded" if auth_ready else (
        "codex login status reported unauthenticated"
        if auth.status is ArgvProcessStatus.PROCESS_FAILED
        else (auth.reason or "codex login status not confirmed")
    )
    diagnostic_ready = False
    modelport_compatible = False
    benchmark_compatible = False
    outcome = RuntimeOutcome.AUTH_FAILED if not auth_ready else RuntimeOutcome.COMPLETED
    stage_detail = (
        "codex --version succeeded; session material was not copied into Zest. "
        f"{auth_detail}. MODELPORT_COMPATIBLE requires a request-consuming exec for this model."
    )
    if live_probe and auth_ready and configuration is not None:
        diagnostic_ready, modelport_compatible, benchmark_compatible, outcome, stage_detail = (
            _probe_model_exec(
                configuration,
                runner=runner,
                executable=path,
                version=version,
            )
        )
    elif auth_ready:
        stage_detail = (
            "codex CLI is authenticated; tokens were not scraped. "
            "BENCHMARK_COMPATIBLE requires an explicit live probe with a request-consuming "
            "exec for this model."
        )
        outcome = RuntimeOutcome.COMPLETED
    readiness = readiness_from_flags(
        installed=True,
        version_known=True,
        auth_ready=auth_ready,
        dependencies_ready=auth_ready,
        diagnostic_ready=diagnostic_ready,
        modelport_compatible=modelport_compatible,
        benchmark_compatible=benchmark_compatible,
        detail=stage_detail,
        version=version,
        executable=path,
    )
    return CliRuntimeAvailability(
        available=auth_ready and (configuration is None or benchmark_compatible),
        outcome=outcome,
        executable=path,
        version=version,
        detail=readiness.detail,
        readiness=readiness,
        configuration_id=configuration_id,
        model=model,
        configuration_fingerprint=fingerprint,
    )


def probe_codex_configurations(
    *,
    env: Mapping[str, str] | None = None,
    runner: ArgvRunner | None = None,
    live_probe: bool = False,
) -> tuple[CliRuntimeAvailability, ...]:
    configs = load_codex_model_configurations(env)
    return tuple(
        probe_codex_cli(
            executable_name=item.executable,
            runner=runner,
            configuration=item,
            live_probe=live_probe,
        )
        for item in configs
    )


def _probe_model_exec(
    configuration: CodexCliRuntimeConfiguration,
    *,
    runner: ArgvRunner | None,
    executable: str,
    version: str,
) -> tuple[bool, bool, bool, RuntimeOutcome, str]:
    adapter = CodexCliSessionAdapter(
        allowed_capabilities=(CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY,),
        executable=executable,
        version=version,
        runner=runner,
        model=configuration.model,
        configuration_id=configuration.configuration_id,
    )
    request = ModelCallRequest(
        role=ModelRole.GENERATOR,
        correlation_id="zest.codex.diagnostic",
        context_fingerprint="codex-diagnostic",
        instructions=diagnostic_readiness_instructions(),
        payload={"diagnostic": True},
        timeout_ms=CODEX_DIAGNOSTIC_TIMEOUT_MS,
    )
    try:
        result = adapter.complete(request)
    except RuntimeUnavailableError as exc:
        return False, False, False, RuntimeOutcome.UNAVAILABLE, str(exc)
    except ProviderAuthError as exc:
        return False, False, False, RuntimeOutcome.AUTH_FAILED, str(exc)
    except ProviderRateLimitError as exc:
        return False, False, False, RuntimeOutcome.RATE_LIMITED, str(exc)
    except ProviderTimeoutError as exc:
        return False, False, False, RuntimeOutcome.TIMED_OUT, str(exc)
    except ContentPolicyBlockedError as exc:
        return False, False, False, RuntimeOutcome.CONTENT_POLICY_BLOCKED, str(exc)
    except StructuredOutputTransportError as exc:
        return False, False, False, RuntimeOutcome.STRUCTURED_OUTPUT_INVALID, str(exc)
    except RuntimeCancelledError as exc:
        return False, False, False, RuntimeOutcome.CANCELLED, str(exc)
    except RuntimeProcessError as exc:
        return False, False, False, RuntimeOutcome.PROCESS_FAILED, str(exc)
    except ModelPortError as exc:
        return False, False, False, RuntimeOutcome.PROCESS_FAILED, str(exc)
    if result.structured_output != DIAGNOSTIC_APPLICATION_OUTPUT:
        return (
            False,
            False,
            False,
            RuntimeOutcome.STRUCTURED_OUTPUT_INVALID,
            "diagnostic probe did not return application-level {\"diagnostic\": true}",
        )
    detail = (
        f"request-consuming Codex exec succeeded for model {configuration.model}; "
        "tokens were not scraped"
    )
    return True, True, True, RuntimeOutcome.COMPLETED, detail


class CodexCliSessionAdapter:
    """AGENT_RUNTIME ModelPort over documented Codex CLI exec. Consumes ModelCallRequest."""

    MODELPORT_COMPATIBLE = True

    def __init__(
        self,
        *,
        allowed_capabilities: tuple[str, ...],
        executable: str | None = None,
        version: str | None = None,
        runner: ArgvRunner | None = None,
        working_directory: Path | None = None,
        model: str | None = None,
        configuration_id: str | None = None,
        sandbox: str = ALLOWED_SANDBOX,
        ephemeral: bool = True,
    ) -> None:
        if not allowed_capabilities:
            raise ModelPortError("agent runtime requires an explicit capability set")
        lowered = {item.lower() for item in allowed_capabilities}
        if lowered & UNRESTRICTED_MARKERS:
            raise ModelPortError("unrestricted tool capability is rejected")
        if sandbox != ALLOWED_SANDBOX:
            raise ModelPortError("sandbox must be read-only")
        if ephemeral is not True:
            raise ModelPortError("ephemeral must be true")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ModelPortError("model must be a non-empty string when set")
        self._allowed = tuple(allowed_capabilities)
        self._executable = executable
        self._version = version
        self._working_directory = working_directory
        self._runner = runner
        self._model = model.strip() if model is not None else None
        self._skip_git_repo_check = working_directory is None
        runtime_id = configuration_id or "codex-cli"
        runtime_configuration = None
        if self._model is not None:
            runtime_configuration = {
                "sandbox": sandbox,
                "ephemeral": ephemeral,
                "ignore_user_config": True,
                "isolated_non_repository_cwd": self._skip_git_repo_check,
                "skip_git_repo_check": self._skip_git_repo_check,
                "executable": executable or DEFAULT_CODEX_EXECUTABLE,
            }
        self._identity = cli_session_runtime_identity(
            adapter_id="codex.cli.session",
            runtime_id=runtime_id,
            runtime_version=version,
            session_reference="local-authenticated-cli-session",
            model_id=self._model,
            runtime_configuration=runtime_configuration,
        )

    @property
    def adapter_identity(self) -> str:
        return self._identity.adapter_id

    @property
    def runtime_identity(self):
        return self._identity

    @property
    def capacity_domain_id(self) -> str | None:
        identity = self._identity

        if identity.session_reference is None:
            return None

        raw = "\x1f".join(
            (
                identity.runtime_kind.value,
                identity.adapter_id,
                identity.auth_mode.value,
                identity.session_reference,
            )
        )

        return (
            "capacity-domain:v1:"
            + hashlib.sha256(
                raw.encode("utf-8")
            ).hexdigest()
        )

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        if CODEX_DIAGNOSTIC_STRUCTURED_OUTPUT_CAPABILITY not in self._allowed:
            raise ModelPortError("requested agent capability is not allowlisted")
        if self._model is None:
            raise ModelPortError("Codex CLI ModelRuntime requires a configured model")
        executable = self._executable or resolve_executable(DEFAULT_CODEX_EXECUTABLE)
        if executable is None:
            raise RuntimeUnavailableError("codex CLI is UNAVAILABLE")
        schema = schema_for_request(request)
        prompt = _prompt_for_request(request, schema)
        with tempfile.TemporaryDirectory(prefix="zest-codex-") as tmp:
            schema_path = Path(tmp) / "output-schema.json"
            schema_path.write_text(
                json.dumps(schema, separators=(",", ":")),
                encoding="utf-8",
            )
            cwd = self._working_directory or Path(tmp)
            argv = _codex_exec_argv(
                executable,
                self._model,
                schema_path,
                skip_git_repo_check=self._skip_git_repo_check,
            )
            result = _run(
                self._runner,
                argv,
                stdin_bytes=prompt.encode("utf-8"),
                timeout_ms=request.timeout_ms or CODEX_DIAGNOSTIC_TIMEOUT_MS,
                working_directory=cwd,
            )
        return _result_from_process(request, result, self._identity, self._version, self._model)


class CodexDiagnosticEchoAdapter:
    """Legacy echo probe. Ignores ModelCallRequest. Not MODELPORT_COMPATIBLE."""

    MODELPORT_COMPATIBLE = False

    def __init__(self, *, executable: str | None = None, runner: ArgvRunner | None = None) -> None:
        self._executable = executable
        self._runner = runner

    def complete(self, request: ModelCallRequest) -> ModelCallResult:
        del request
        raise ModelPortError(
            "diagnostic echo adapter ignores ModelCallRequest and is not MODELPORT_COMPATIBLE"
        )


def _prompt_for_request(request: ModelCallRequest, schema: Mapping[str, object]) -> str:
    payload = json.dumps(dict(request.payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    schema_payload = json.dumps(dict(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (
        "SYSTEM:\nYou are a structured-output reasoning runtime. Do not call tools.\n\n"
        f"INSTRUCTIONS:\n{request.instructions}\n\n"
        f"USER:\ncorrelation_id={request.correlation_id}\n"
        f"role={request.role.value}\n"
        f"context_fingerprint={request.context_fingerprint}\n"
        f"payload={payload}\n\n"
        "TRANSPORT:\n"
        "Emit only the requested application-level JSON object at the top level.\n"
        "Do not wrap it in result_json or any transport envelope.\n"
        "For nullable optional fields, null means the optional application field is absent.\n"
        f"application_json_schema={schema_payload}\n"
    )


def _result_from_process(
    request: ModelCallRequest,
    result: ArgvProcessResult,
    identity,
    version: str | None,
    model: str,
) -> ModelCallResult:
    if result.status is ArgvProcessStatus.UNAVAILABLE:
        raise RuntimeUnavailableError(result.reason or "codex CLI unavailable")
    if result.status is ArgvProcessStatus.TIMED_OUT:
        raise ProviderTimeoutError(result.reason or "codex CLI timed out")
    if result.status is ArgvProcessStatus.CANCELLED:
        raise RuntimeCancelledError(result.reason or "codex CLI cancelled")
    if result.status is ArgvProcessStatus.PROCESS_FAILED:
        combined = f"{result.stdout} {result.stderr}".lower()
        if _codex_usage_quota_limited(combined):
            raise ProviderUsageLimitError(USAGE_LIMIT_DETAIL)
        if _codex_transient_rate_limited(combined):
            raise ProviderRateLimitError(USAGE_LIMIT_DETAIL)
        if "login" in combined or "unauthorized" in combined or "not authenticated" in combined:
            raise ProviderAuthError("codex CLI authentication failed")
        if _codex_content_policy_refusal(combined):
            raise ContentPolicyBlockedError("codex CLI content/safety policy blocked the request")
        if _codex_non_git_working_directory_error(combined):
            raise RuntimeProcessError(NON_GIT_WORKING_DIRECTORY_DETAIL)
        raise RuntimeProcessError(_safe_process_failure_detail(result))
    raw = result.stdout.strip()
    if not raw:
        raise StructuredOutputTransportError("codex CLI stdout was empty")
    structured = decode_transport_output_for_request(request, _parse_structured_stdout(raw))
    return ModelCallResult(
        role=request.role,
        adapter_identity=identity.adapter_id,
        provider_adapter_identity=identity.runtime_id,
        structured_output=structured,
        model_id=model,
        model_version=version,
        runtime_identity=identity,
    )


def _codex_usage_quota_limited(text: str) -> bool:
    return any(
        marker in text
        for marker in CODEX_USAGE_QUOTA_MARKERS
    )


def _codex_transient_rate_limited(text: str) -> bool:
    return any(
        marker in text
        for marker in CODEX_TRANSIENT_RATE_LIMIT_MARKERS
    )


def _codex_usage_limited(text: str) -> bool:
    return (
        _codex_usage_quota_limited(text)
        or _codex_transient_rate_limited(text)
    )


def _codex_non_git_working_directory_error(text: str) -> bool:
    return any(marker in text for marker in CODEX_NON_GIT_WORKING_DIRECTORY_MARKERS)


def _codex_content_policy_refusal(text: str) -> bool:
    return any(marker in text for marker in CODEX_CONTENT_POLICY_REFUSAL_MARKERS)


def _safe_process_failure_detail(result: ArgvProcessResult) -> str:
    """Return deterministic process provenance without provider output text."""

    stdout_bytes = result.stdout.encode("utf-8")
    stderr_bytes = result.stderr.encode("utf-8")
    failure_code = (
        "PROCESS_EXIT_NONZERO"
        if result.exit_code is not None and result.exit_code != 0
        else "PROCESS_FAILED"
    )
    metadata = {
        "cleanup_failed": result.cleanup_failed,
        "cleanup_status": "FAILED" if result.cleanup_failed else "NOT_FAILED",
        "exit_code": result.exit_code,
        "failure_code": failure_code,
        "reason": result.reason,
        "stderr_byte_length": len(stderr_bytes),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "stderr_truncated": result.stderr_truncated,
        "stdout_byte_length": len(stdout_bytes),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
    }
    return json.dumps(metadata, sort_keys=True, separators=(",", ":"))


def _load_json_object(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
        if parsed is None:
            raise StructuredOutputTransportError("codex CLI stdout was not structured JSON")
    if not isinstance(parsed, dict):
        raise StructuredOutputTransportError("codex CLI JSON was not an object")
    return parsed


def _parse_structured_stdout(raw: str) -> dict[str, object]:
    parsed = _load_json_object(raw)
    if "result_json" in parsed:
        raise StructuredOutputTransportError("codex CLI returned legacy result_json envelope")
    return parsed
