"""Browser engine protocol and in-memory test double. No Playwright import."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin, urlsplit

from .browser_envelope import (
    OUTSIDE_ENVELOPE,
    UNSUPPORTED_SCHEME,
    BrowserNetworkEnvelope,
    envelope_allows,
    normalize_target,
    url_is_representable,
)

PROCESS_GENERATION = uuid.uuid4().hex
SNAPSHOT_SCHEMA_VERSION = "browser.page.snapshot.v1"
REPRESENTABLE = "REPRESENTABLE"
NOT_REPRESENTABLE = "NOT_REPRESENTABLE"
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
CONTROL_NAME_CAP = 64
POPUP_ATTR_RE = re.compile(r'data-popup-url=["\']([^"\']+)["\']', re.I)
IFRAME_SRC_RE = re.compile(r'<iframe\b[^>]*\bsrc=["\']([^"\']+)["\']', re.I)
CONTROL_TAG_RE = re.compile(
    r"<(a|button|input|select|textarea)\b([^>]*)>",
    re.I,
)
ATTR_RE = re.compile(r"""([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""", re.I)
HREF_RE = re.compile(r"""\bhref=["']([^"']+)["']""", re.I)


class BrowserEngineUnavailable(Exception):
    """Playwright/Chromium runtime is not available. Not a silent fallback signal."""


@dataclass(frozen=True)
class BrowserRuntimeLimits:
    """Bounds for one browser action.

    ``max_memory_bytes`` is the maximum aggregate memory available to this Worker
    and its Chromium descendants combined; it is not a sampled RSS figure.
    ``max_descendant_processes`` is the process ceiling. ``max_descendant_tasks``
    is the task/thread ceiling. The Worker does not enforce these itself; the
    supervising parent does, in the kernel, and states the enforced values in the
    containment acknowledgement before the engine may be created. Linux cgroup v2
    enforces the task ceiling with ``pids.max``. Windows Job Objects enforce the
    process ceiling with ``ActiveProcessLimit``. Both are hard kernel boundaries.
    Neither is an RSS or process-sampling approximation.
    """

    max_active_contexts: int = 2
    max_pages_per_context: int = 1
    max_attempted_network_requests_per_action: int = 16
    max_network_events: int = 32
    max_control_refs: int = 32
    max_action_runtime_ms: int = 8000
    max_navigation_runtime_ms: int = 5000
    max_stdout_bytes: int = 65536
    max_descendant_processes: int = 32
    max_descendant_tasks: int = 256
    max_memory_bytes: int = 2_147_483_648


@dataclass(frozen=True)
class BrowserContextLease:
    context_ref: str
    page_ref: str
    research_run_id: str
    identity_id: str | None
    session_context_reference: str | None
    origin: str
    target_reference: str
    capability_version: str
    fingerprint: str
    generation: str
    frozen: bool


@dataclass(frozen=True)
class BrowserActionResult:
    status: str
    raw: dict[str, Any]
    diagnostics: dict[str, Any] | None
    attempted_network_requests: int
    freeze: bool


@dataclass(frozen=True)
class NetworkEvent:
    event_id: str
    method: str
    resource_type: str
    normalized_target: str
    path: str
    status_code: int | None
    request_bytes: int
    response_bytes: int
    redirect: bool
    representability: str
    body_digest: str | None = None


@dataclass(frozen=True)
class ControlRef:
    element_reference: str
    snapshot_fingerprint: str
    tag: str
    role: str
    input_type: str
    disabled: bool
    checked: bool
    name: str
    aria_label: str
    placeholder: str
    href_scheme: str = ""
    href_origin: str = ""
    href_path: str = ""


class BrowserEngine(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def navigate(
        self,
        lease_key: str | None,
        url: str,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
        cookie: str | None = None,
        binding: dict[str, Any] | None = None,
    ) -> BrowserActionResult: ...

    def observe(
        self,
        lease: BrowserContextLease,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> BrowserActionResult: ...

    def interact(
        self,
        lease: BrowserContextLease,
        kind: str,
        element_ref: str,
        snapshot_fp: str,
        value: str | None,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> BrowserActionResult: ...

    def freeze(self, lease: BrowserContextLease) -> None: ...

    def close_all(self) -> None: ...

    def get_lease(self, context_ref: str) -> BrowserContextLease | None: ...

    def snapshot_fingerprint_for(self, context_ref: str) -> str | None: ...


def cap_text(value: str, limit: int = CONTROL_NAME_CAP) -> str:
    return value[:limit]


def snapshot_raw(
    *,
    attempted_network_requests: int,
    browser_context_reference: str,
    page_reference: str,
    snapshot_fingerprint: str,
    normalized_url: str,
    ready_state: str,
    frame_count: int,
    controls: list[dict[str, Any]],
    network_events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "attempted_network_requests": attempted_network_requests,
        "browser_context_reference": browser_context_reference,
        "page_reference": page_reference,
        "snapshot_fingerprint": snapshot_fingerprint,
        "normalized_url": normalized_url,
        "ready_state": ready_state,
        "frame_count": frame_count,
        "controls": controls,
        "network_events": network_events,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }


def reauthorization_diagnostics(
    *,
    channel: str,
    raw_location: str,
    response_url: str,
    location: str,
) -> dict[str, Any]:
    return {
        "followed": False,
        "requires_core_re_evaluation": True,
        "channel": channel,
        "raw_location": raw_location,
        "response_url": response_url,
        "location": location,
        "self_authorized": False,
    }


def control_to_mapping(control: ControlRef) -> dict[str, Any]:
    payload = {
        "element_reference": control.element_reference,
        "snapshot_fingerprint": control.snapshot_fingerprint,
        "tag": control.tag,
        "role": control.role,
        "input_type": control.input_type,
        "disabled": control.disabled,
        "checked": control.checked,
        "name": control.name,
        "aria_label": control.aria_label,
        "placeholder": control.placeholder,
    }
    if control.href_scheme:
        payload["href_scheme"] = control.href_scheme
    if control.href_origin:
        payload["href_origin"] = control.href_origin
    if control.href_path:
        payload["href_path"] = control.href_path
    return payload


def network_event_to_mapping(event: NetworkEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": event.event_id,
        "method": event.method,
        "resource_type": event.resource_type,
        "normalized_target": event.normalized_target,
        "path": event.path,
        "status_code": event.status_code,
        "request_bytes": event.request_bytes,
        "response_bytes": event.response_bytes,
        "redirect": event.redirect,
        "representability": event.representability,
    }
    if event.body_digest is not None:
        payload["body_digest"] = event.body_digest
    return payload


def fingerprint_controls(url: str, signatures: list[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        {"url": url, "controls": signatures},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lease_binding_matches(lease: BrowserContextLease, binding: Mapping[str, Any]) -> bool:
    if lease.generation != PROCESS_GENERATION:
        return False
    if lease.research_run_id != binding.get("research_run_id"):
        return False
    if lease.identity_id != binding.get("identity_id"):
        return False
    if lease.session_context_reference != binding.get("session_context_reference"):
        return False
    if lease.origin != binding.get("origin"):
        return False
    if lease.target_reference != binding.get("target_reference"):
        return False
    return True


@dataclass
class _SeededPage:
    html: str = ""
    controls: list[dict[str, Any]] = field(default_factory=list)
    location: str | None = None
    status_code: int = 200
    iframe_src: str | None = None
    websocket: bool = False
    download: bool = False
    service_worker: bool = False
    resources: list[dict[str, Any]] = field(default_factory=list)
    spa_url: str | None = None


@dataclass
class _PageState:
    lease: BrowserContextLease
    url: str
    html: str
    cookie: str | None
    controls: list[ControlRef]
    network_events: list[NetworkEvent]
    attempted: int
    ready_state: str
    frame_count: int
    snapshot_fingerprint: str


class InMemoryBrowserEngine:
    """Deterministic BrowserEngine for unit tests. Does not use Playwright."""

    def __init__(self) -> None:
        self._pages: dict[str, _SeededPage] = {}
        self._states: dict[str, _PageState] = {}
        self._started = False

    def seed_page(
        self,
        url: str,
        html_controls: object = None,
        resources: object = None,
    ) -> None:
        html = ""
        explicit_controls: list[dict[str, Any]] = []
        if isinstance(html_controls, str):
            html = html_controls
        elif isinstance(html_controls, Mapping):
            raw_html = html_controls.get("html")
            html = raw_html if isinstance(raw_html, str) else ""
            raw_controls = html_controls.get("controls")
            if isinstance(raw_controls, list):
                explicit_controls = [item for item in raw_controls if isinstance(item, Mapping)]
        location = None
        iframe_src = None
        websocket = False
        download = False
        service_worker = False
        status_code = 200
        extra_resources: list[dict[str, Any]] = []
        spa_url = None
        if isinstance(resources, Mapping):
            loc = resources.get("location") or resources.get("redirect")
            location = loc if isinstance(loc, str) else None
            iframe = resources.get("iframe_src") or resources.get("iframe")
            iframe_src = iframe if isinstance(iframe, str) else None
            websocket = bool(resources.get("websocket"))
            download = bool(resources.get("download"))
            service_worker = bool(resources.get("service_worker"))
            status = resources.get("status_code")
            status_code = status if isinstance(status, int) else 200
            seeded = resources.get("network_events") or resources.get("resources") or []
            if isinstance(seeded, list):
                extra_resources = [item for item in seeded if isinstance(item, Mapping)]
            spa = resources.get("spa_url")
            spa_url = spa if isinstance(spa, str) else None
        if iframe_src is None:
            match = IFRAME_SRC_RE.search(html)
            iframe_src = match.group(1) if match else None
        self._pages[url] = _SeededPage(
            html=html,
            controls=explicit_controls,
            location=location,
            status_code=status_code,
            iframe_src=iframe_src,
            websocket=websocket,
            download=download,
            service_worker=service_worker,
            resources=extra_resources,
            spa_url=spa_url,
        )

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self.close_all()
        self._started = False

    def get_lease(self, context_ref: str) -> BrowserContextLease | None:
        state = self._states.get(context_ref)
        if state is None:
            return None
        return state.lease

    def snapshot_fingerprint_for(self, context_ref: str) -> str | None:
        state = self._states.get(context_ref)
        if state is None:
            return None
        return state.snapshot_fingerprint

    def freeze(self, lease: BrowserContextLease) -> None:
        state = self._states.get(lease.context_ref)
        if state is None:
            return
        state.lease = replace(state.lease, frozen=True)

    def close_all(self) -> None:
        self._states.clear()

    def _evict_oldest_state(self) -> None:
        if not self._states:
            return
        oldest_ref = next(iter(self._states))
        del self._states[oldest_ref]

    def navigate(
        self,
        lease_key: str | None,
        url: str,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
        cookie: str | None = None,
        binding: dict[str, Any] | None = None,
    ) -> BrowserActionResult:
        binding = dict(binding or {})
        allowed, reason = envelope_allows(envelope, url)
        if not allowed:
            return self._deny_url(
                url,
                reason,
                attempted=0,
                freeze=True,
                lease=self._states.get(lease_key).lease if lease_key and lease_key in self._states else None,
                limits=limits,
            )
        state, created, error = self._acquire_state(
            lease_key, url, cookie, binding, limits, require_existing=False
        )
        if error is not None:
            return error
        assert state is not None
        if state.lease.frozen:
            return self._failed(state, "BLOCKED", "browser context is frozen")
        return self._load(state, url, envelope, limits, created=created)

    def observe(
        self,
        lease: BrowserContextLease,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> BrowserActionResult:
        state = self._states.get(lease.context_ref)
        if state is None or state.lease.generation != PROCESS_GENERATION:
            return BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "unknown browser_context_reference"},
                attempted_network_requests=0,
                freeze=False,
            )
        if state.lease.frozen:
            return self._failed(state, "BLOCKED", "browser context is frozen")
        allowed, reason = envelope_allows(envelope, state.url)
        if not allowed:
            self.freeze(state.lease)
            return self._deny_url(
                state.url, reason, attempted=state.attempted, freeze=True, lease=state.lease, limits=limits
            )
        self._refresh_snapshot(state, envelope, limits, record_document=False)
        blocked = self._post_load_checks(state, envelope, limits, state.url)
        if blocked is not None:
            return blocked
        return self._succeeded(state, limits)

    def interact(
        self,
        lease: BrowserContextLease,
        kind: str,
        element_ref: str,
        snapshot_fp: str,
        value: str | None,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> BrowserActionResult:
        state = self._states.get(lease.context_ref)
        if state is None or state.lease.generation != PROCESS_GENERATION:
            return BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "unknown browser_context_reference"},
                attempted_network_requests=0,
                freeze=False,
            )
        if state.lease.frozen:
            return self._failed(state, "BLOCKED", "browser context is frozen")
        if snapshot_fp != state.snapshot_fingerprint:
            return self._failed(state, "BLOCKED", "stale snapshot")
        control = next((item for item in state.controls if item.element_reference == element_ref), None)
        if control is None:
            return self._failed(state, "BLOCKED", "unknown element_reference")
        if control.disabled:
            return self._failed(state, "BLOCKED", "control is disabled")
        if kind not in {"click", "fill", "select", "submit"}:
            return self._failed(state, "BLOCKED", "interact kind is not allowed")
        if kind == "fill" and control.input_type == "password":
            return self._failed(state, "BLOCKED", "password fields cannot be filled")
        seeded = self._pages.get(state.url)
        next_url = None
        if kind == "click":
            next_url = self._click_target(state, element_ref, seeded)
        elif kind in {"submit", "select", "fill"} and seeded is not None:
            next_url = seeded.spa_url
        if next_url:
            allowed, reason = envelope_allows(envelope, next_url)
            if not allowed:
                self.freeze(state.lease)
                return self._deny_url(
                    next_url,
                    reason,
                    attempted=state.attempted,
                    freeze=True,
                    lease=state.lease,
                    limits=limits,
                    channel="SPA",
                    response_url=state.url,
                )
            loaded = self._load(state, next_url, envelope, limits, created=False)
            return loaded
        self._refresh_snapshot(state, envelope, limits, record_document=False)
        blocked = self._post_load_checks(state, envelope, limits, state.url)
        if blocked is not None:
            return blocked
        return self._succeeded(state, limits)

    def _click_target(
        self, state: _PageState, element_ref: str, seeded: _SeededPage | None
    ) -> str | None:
        if seeded is None:
            return None
        match = HREF_RE.search(seeded.html)
        if match:
            return urljoin(state.url, match.group(1))
        return seeded.spa_url

    def _acquire_state(
        self,
        lease_key: str | None,
        url: str,
        cookie: str | None,
        binding: dict[str, Any],
        limits: BrowserRuntimeLimits,
        *,
        require_existing: bool,
    ) -> tuple[_PageState | None, bool, BrowserActionResult | None]:
        if lease_key:
            state = self._states.get(lease_key)
            if state is None or state.lease.generation != PROCESS_GENERATION:
                return None, False, BrowserActionResult(
                    status="EXECUTION_FAILED",
                    raw={},
                    diagnostics={"error": "unknown browser_context_reference"},
                    attempted_network_requests=0,
                    freeze=False,
                )
            if not lease_binding_matches(state.lease, binding):
                return None, False, BrowserActionResult(
                    status="BLOCKED",
                    raw={},
                    diagnostics={"error": "browser context binding mismatch"},
                    attempted_network_requests=0,
                    freeze=False,
                )
            if cookie is not None:
                state.cookie = cookie
            return state, False, None
        if require_existing:
            return None, False, BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "browser_context_reference is required"},
                attempted_network_requests=0,
                freeze=False,
            )
        if len(self._states) >= limits.max_active_contexts:
            self._evict_oldest_state()
        if len(self._states) >= limits.max_active_contexts:
            return None, False, BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "max_active_contexts exceeded"},
                attempted_network_requests=0,
                freeze=False,
            )
        context_ref = f"ctx-{uuid.uuid4().hex}"
        page_ref = f"page-{uuid.uuid4().hex}"
        lease = BrowserContextLease(
            context_ref=context_ref,
            page_ref=page_ref,
            research_run_id=str(binding.get("research_run_id") or ""),
            identity_id=binding.get("identity_id") if isinstance(binding.get("identity_id"), str) else None,
            session_context_reference=(
                binding.get("session_context_reference")
                if isinstance(binding.get("session_context_reference"), str)
                else None
            ),
            origin=str(binding.get("origin") or ""),
            target_reference=str(binding.get("target_reference") or ""),
            capability_version=str(binding.get("capability_version") or ""),
            fingerprint=str(binding.get("fingerprint") or ""),
            generation=PROCESS_GENERATION,
            frozen=False,
        )
        state = _PageState(
            lease=lease,
            url=url,
            html="",
            cookie=cookie,
            controls=[],
            network_events=[],
            attempted=0,
            ready_state="loading",
            frame_count=1,
            snapshot_fingerprint="",
        )
        self._states[context_ref] = state
        return state, True, None

    def _load(
        self,
        state: _PageState,
        url: str,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
        *,
        created: bool,
    ) -> BrowserActionResult:
        if state.attempted >= limits.max_attempted_network_requests_per_action:
            return self._exhausted(state)
        state.attempted += 1
        seeded = self._pages.get(url) or _SeededPage()
        self._record_document_event(state, url, seeded.status_code, limits)
        if seeded.location:
            location = urljoin(url, seeded.location)
            self.freeze(state.lease)
            return self._deny_url(
                location,
                OUTSIDE_ENVELOPE,
                attempted=state.attempted,
                freeze=True,
                lease=state.lease,
                limits=limits,
                channel="REDIRECT",
                response_url=url,
                raw_location=seeded.location,
                force=True,
            )
        for resource in seeded.resources:
            if state.attempted >= limits.max_attempted_network_requests_per_action:
                return self._exhausted(state)
            resource_url = str(resource.get("url") or url)
            allowed, reason = envelope_allows(envelope, resource_url)
            state.attempted += 1
            self._record_event(
                state,
                method=str(resource.get("method") or "GET"),
                resource_type=str(resource.get("resource_type") or "xhr"),
                url=resource_url,
                status_code=resource.get("status_code") if isinstance(resource.get("status_code"), int) else None,
                redirect=False,
                limits=limits,
            )
            if not allowed:
                self.freeze(state.lease)
                return self._deny_url(
                    resource_url, reason, attempted=state.attempted, freeze=True, lease=state.lease, limits=limits
                )
        state.url = url
        state.html = seeded.html
        state.ready_state = "complete"
        self._refresh_snapshot(state, envelope, limits, record_document=False)
        blocked = self._post_load_checks(state, envelope, limits, url)
        if blocked is not None:
            return blocked
        return self._succeeded(state, limits)

    def _post_load_checks(
        self,
        state: _PageState,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
        response_url: str,
    ) -> BrowserActionResult | None:
        seeded = self._pages.get(state.url)
        html = state.html
        popup = POPUP_ATTR_RE.search(html)
        if popup:
            popup_url = urljoin(state.url, popup.group(1))
            self.freeze(state.lease)
            return self._deny_url(
                popup_url,
                OUTSIDE_ENVELOPE,
                attempted=state.attempted,
                freeze=True,
                lease=state.lease,
                limits=limits,
                channel="POPUP",
                response_url=response_url,
                force=True,
            )
        iframe_src = seeded.iframe_src if seeded is not None else None
        if iframe_src:
            iframe_url = urljoin(state.url, iframe_src)
            allowed, reason = envelope_allows(envelope, iframe_url)
            if not allowed:
                self.freeze(state.lease)
                channel = "IFRAME"
                status = "BLOCKED" if reason == UNSUPPORTED_SCHEME else "REAUTHORIZATION_REQUIRED"
                if status == "REAUTHORIZATION_REQUIRED":
                    return self._deny_url(
                        iframe_url,
                        reason,
                        attempted=state.attempted,
                        freeze=True,
                        lease=state.lease,
                        limits=limits,
                        channel=channel,
                        response_url=response_url,
                    )
                return self._failed(state, "BLOCKED", "iframe target is outside envelope")
            state.frame_count = 2
        if seeded is not None and seeded.websocket:
            return self._failed(state, "BLOCKED", "websocket is not allowed")
        if seeded is not None and seeded.download:
            return self._failed(state, "BLOCKED", "download is not allowed")
        if seeded is not None and seeded.service_worker:
            return self._failed(state, "BLOCKED", "service worker is not allowed")
        return None

    def _refresh_snapshot(
        self,
        state: _PageState,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
        *,
        record_document: bool,
    ) -> None:
        seeded = self._pages.get(state.url)
        raw_controls = list(seeded.controls) if seeded is not None and seeded.controls else _extract_controls(state.html)
        capped = raw_controls[: limits.max_control_refs]
        signatures = []
        controls: list[ControlRef] = []
        for index, item in enumerate(capped):
            tag = str(item.get("tag") or "div").lower()
            role = cap_text(str(item.get("role") or ""))
            input_type = str(item.get("input_type") or item.get("type") or "").lower()
            name = cap_text(str(item.get("name") or ""))
            aria_label = cap_text(str(item.get("aria_label") or ""))
            placeholder = cap_text(str(item.get("placeholder") or ""))
            href_scheme, href_origin, href_path = safe_href_parts(item.get("href"), state.url)
            signature = {
                "tag": tag,
                "role": role,
                "input_type": input_type,
                "name": name,
                "aria_label": aria_label,
                "placeholder": placeholder,
                "href_scheme": href_scheme,
                "href_origin": href_origin,
                "href_path": href_path,
                "disabled": bool(item.get("disabled")),
                "checked": bool(item.get("checked")),
            }
            signatures.append(signature)
        fingerprint = fingerprint_controls(state.url, signatures)
        for index, item in enumerate(capped):
            href_scheme, href_origin, href_path = safe_href_parts(item.get("href"), state.url)
            controls.append(
                ControlRef(
                    element_reference=f"el-{index}",
                    snapshot_fingerprint=fingerprint,
                    tag=str(item.get("tag") or "div").lower(),
                    role=cap_text(str(item.get("role") or "")),
                    input_type=str(item.get("input_type") or item.get("type") or "").lower(),
                    disabled=bool(item.get("disabled")),
                    checked=bool(item.get("checked")),
                    name=cap_text(str(item.get("name") or "")),
                    aria_label=cap_text(str(item.get("aria_label") or "")),
                    placeholder=cap_text(str(item.get("placeholder") or "")),
                    href_scheme=href_scheme,
                    href_origin=href_origin,
                    href_path=href_path,
                )
            )
        state.controls = controls
        state.snapshot_fingerprint = fingerprint
        if record_document:
            self._record_document_event(state, state.url, 200, limits)

    def _record_document_event(
        self, state: _PageState, url: str, status_code: int, limits: BrowserRuntimeLimits
    ) -> None:
        self._record_event(
            state,
            method="GET",
            resource_type="document",
            url=url,
            status_code=status_code,
            redirect=status_code in REDIRECT_STATUSES,
            limits=limits,
        )

    def _record_event(
        self,
        state: _PageState,
        *,
        method: str,
        resource_type: str,
        url: str,
        status_code: int | None,
        redirect: bool,
        limits: BrowserRuntimeLimits,
    ) -> None:
        if len(state.network_events) >= limits.max_network_events:
            return
        normalized = normalize_target(url) or ""
        representability = REPRESENTABLE if url_is_representable(url) else NOT_REPRESENTABLE
        try:
            path = urlsplit(url).path or "/"
        except ValueError:
            path = "/"
        state.network_events.append(
            NetworkEvent(
                event_id=f"ne-{len(state.network_events) + 1}",
                method=method,
                resource_type=resource_type,
                normalized_target=normalized,
                path=path,
                status_code=status_code,
                request_bytes=0,
                response_bytes=0,
                redirect=redirect,
                representability=representability,
            )
        )

    def _succeeded(self, state: _PageState, limits: BrowserRuntimeLimits) -> BrowserActionResult:
        normalized = normalize_target(state.url) or state.url
        raw = snapshot_raw(
            attempted_network_requests=state.attempted,
            browser_context_reference=state.lease.context_ref,
            page_reference=state.lease.page_ref,
            snapshot_fingerprint=state.snapshot_fingerprint,
            normalized_url=normalized,
            ready_state=state.ready_state,
            frame_count=state.frame_count,
            controls=[control_to_mapping(item) for item in state.controls[: limits.max_control_refs]],
            network_events=[
                network_event_to_mapping(item) for item in state.network_events[: limits.max_network_events]
            ],
        )
        return BrowserActionResult(
            status="SUCCEEDED",
            raw=raw,
            diagnostics=None,
            attempted_network_requests=state.attempted,
            freeze=state.lease.frozen,
        )

    def _failed(self, state: _PageState, status: str, error: str) -> BrowserActionResult:
        return BrowserActionResult(
            status=status,
            raw={
                "attempted_network_requests": state.attempted,
                "browser_context_reference": state.lease.context_ref,
                "page_reference": state.lease.page_ref,
            },
            diagnostics={"error": error, "self_authorized": False},
            attempted_network_requests=state.attempted,
            freeze=state.lease.frozen,
        )

    def _exhausted(self, state: _PageState) -> BrowserActionResult:
        return BrowserActionResult(
            status="BUDGET_EXHAUSTED",
            raw={
                "attempted_network_requests": state.attempted,
                "browser_context_reference": state.lease.context_ref,
                "page_reference": state.lease.page_ref,
            },
            diagnostics={"error": "attempted network requests would exceed max", "self_authorized": False},
            attempted_network_requests=state.attempted,
            freeze=False,
        )

    def _deny_url(
        self,
        url: str,
        reason: str,
        *,
        attempted: int,
        freeze: bool,
        lease: BrowserContextLease | None,
        limits: BrowserRuntimeLimits,
        channel: str | None = None,
        response_url: str | None = None,
        raw_location: str | None = None,
        force: bool = False,
    ) -> BrowserActionResult:
        if lease is not None and freeze:
            self.freeze(lease)
        representable = url_is_representable(url) or force
        if reason == UNSUPPORTED_SCHEME and not force:
            status = "BLOCKED"
            diagnostics: dict[str, Any] = {
                "error": reason,
                "self_authorized": False,
                "followed": False,
            }
        elif representable:
            status = "REAUTHORIZATION_REQUIRED"
            resolved = normalize_target(url) or url
            diagnostics = reauthorization_diagnostics(
                channel=channel or "REDIRECT",
                raw_location=raw_location if raw_location is not None else url,
                response_url=response_url or url,
                location=resolved,
            )
        else:
            status = "BLOCKED"
            diagnostics = {"error": reason, "self_authorized": False, "followed": False}
        raw: dict[str, Any] = {"attempted_network_requests": attempted, "followed": False}
        if lease is not None:
            raw["browser_context_reference"] = lease.context_ref
            raw["page_reference"] = lease.page_ref
        return BrowserActionResult(
            status=status,
            raw=raw,
            diagnostics=diagnostics,
            attempted_network_requests=attempted,
            freeze=freeze,
        )


def _extract_controls(html: str) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    for match in CONTROL_TAG_RE.finditer(html or ""):
        tag = match.group(1).lower()
        attrs = _parse_attrs(match.group(2) or "")
        input_type = (attrs.get("type") or "").lower()
        controls.append(
            {
                "tag": tag,
                "role": attrs.get("role") or "",
                "input_type": input_type,
                "disabled": "disabled" in attrs,
                "checked": "checked" in attrs,
                "name": attrs.get("name") or "",
                "aria_label": attrs.get("aria-label") or "",
                "placeholder": attrs.get("placeholder") or "",
                "href": attrs.get("href") or "",
            }
        )
    return controls


def safe_href_parts(href: object, base_url: str) -> tuple[str, str, str]:
    raw = href if isinstance(href, str) else ""
    raw = raw.strip()
    if not raw:
        return "", "", ""
    parsed_raw = urlsplit(raw)
    if parsed_raw.scheme and parsed_raw.scheme not in {"http", "https"}:
        return parsed_raw.scheme, "", f"{parsed_raw.scheme}:"
    parsed = urlsplit(urljoin(base_url, raw))
    if parsed.scheme not in {"http", "https"}:
        return parsed.scheme or "", "", ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    origin = f"{parsed.scheme}://{parsed.hostname}{port}" if parsed.hostname else ""
    return parsed.scheme, origin, (parsed.path or "/")[:256]


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(raw):
        name = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ""
        attrs[name] = value
    return attrs
