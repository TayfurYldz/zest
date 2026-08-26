"""Playwright Chromium BrowserEngine. The only Worker module allowed to import Playwright."""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from .browser_engine import (
    REDIRECT_STATUSES,
    BrowserActionResult,
    BrowserContextLease,
    BrowserEngineUnavailable,
    BrowserRuntimeLimits,
    ControlRef,
    NetworkEvent,
    NOT_REPRESENTABLE,
    PROCESS_GENERATION,
    REPRESENTABLE,
    cap_text,
    control_to_mapping,
    fingerprint_controls,
    lease_binding_matches,
    network_event_to_mapping,
    reauthorization_diagnostics,
    safe_href_parts,
    snapshot_raw,
)
from .browser_envelope import (
    UNSUPPORTED_SCHEME,
    BrowserNetworkEnvelope,
    envelope_allows,
    normalize_target,
    url_is_representable,
)

CONTROL_EXTRACT_SCRIPT = """(elements) => elements.map((el) => ({
  tag: (el.tagName || '').toLowerCase(),
  role: el.getAttribute('role') || '',
  input_type: (el.getAttribute('type') || '').toLowerCase(),
  disabled: !!(el.disabled || el.hasAttribute('disabled')),
  checked: !!el.checked,
  name: (el.getAttribute('name') || '').slice(0, 64),
  aria_label: (el.getAttribute('aria-label') || '').slice(0, 64),
  placeholder: (el.getAttribute('placeholder') || '').slice(0, 64),
  href: (el.getAttribute('href') || '').slice(0, 256)
}))"""
CONTROL_SELECTOR = "a, button, input, select, textarea"


@dataclass
class _Runtime:
    lease: BrowserContextLease
    context: Any
    page: Any
    envelope: BrowserNetworkEnvelope | None
    limits: BrowserRuntimeLimits
    attempted: int
    network_events: list[NetworkEvent]
    controls: list[ControlRef]
    snapshot_fingerprint: str
    pending_status: str | None
    pending_channel: str | None
    pending_raw_location: str | None
    pending_response_url: str | None
    pending_error: str | None
    committed: bool
    ready_state: str
    frame_count: int


class PlaywrightChromiumEngine:
    """Headless Chromium engine with envelope interception. Not a scanner."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._timeout_error: type[BaseException] = TimeoutError
        self._runtimes: dict[str, _Runtime] = {}
        self._started = False

    def start(self) -> None:
        try:
            from playwright.sync_api import Route, TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserEngineUnavailable("playwright is not installed") from exc
        if "max_redirects" not in inspect.signature(Route.fetch).parameters:
            raise BrowserEngineUnavailable("Playwright Route.fetch(max_redirects=0) is required")
        self._timeout_error = PlaywrightTimeout
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            self.stop()
            raise BrowserEngineUnavailable("chromium launch failed") from exc
        if "service_workers" not in inspect.signature(self._browser.new_context).parameters:
            self.stop()
            raise BrowserEngineUnavailable("Playwright context service_workers=block is required")
        self._started = True

    def stop(self) -> None:
        self.close_all()
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        self._started = False
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    def close_all(self) -> None:
        for runtime in list(self._runtimes.values()):
            self._close_runtime(runtime)
        self._runtimes.clear()

    def get_lease(self, context_ref: str) -> BrowserContextLease | None:
        runtime = self._runtimes.get(context_ref)
        if runtime is None:
            return None
        return runtime.lease

    def snapshot_fingerprint_for(self, context_ref: str) -> str | None:
        runtime = self._runtimes.get(context_ref)
        if runtime is None:
            return None
        return runtime.snapshot_fingerprint

    def freeze(self, lease: BrowserContextLease) -> None:
        runtime = self._runtimes.get(lease.context_ref)
        if runtime is None:
            return
        runtime.lease = replace(runtime.lease, frozen=True)

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
            existing = self._runtimes.get(lease_key) if lease_key else None
            if existing is not None:
                self.freeze(existing.lease)
            return self._denied(url, reason, attempted=0, freeze=True, runtime=existing)
        runtime, error = self._acquire(lease_key, url, cookie, binding, envelope, limits)
        if error is not None:
            return error
        assert runtime is not None
        if runtime.lease.frozen:
            return self._failed(runtime, "BLOCKED", "browser context is frozen")
        return self._goto(runtime, url, limits)

    def observe(
        self,
        lease: BrowserContextLease,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> BrowserActionResult:
        runtime = self._runtimes.get(lease.context_ref)
        if runtime is None or runtime.lease.generation != PROCESS_GENERATION:
            return BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "unknown browser_context_reference"},
                attempted_network_requests=0,
                freeze=False,
            )
        if runtime.lease.frozen:
            return self._failed(runtime, "BLOCKED", "browser context is frozen")
        self._begin_action(runtime, envelope, limits)
        pending = self._pending(runtime)
        if pending is not None:
            return pending
        spa = self._check_main_url(runtime, channel="SPA")
        if spa is not None:
            return spa
        self._take_snapshot(runtime)
        return self._succeeded(runtime)

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
        runtime = self._runtimes.get(lease.context_ref)
        if runtime is None or runtime.lease.generation != PROCESS_GENERATION:
            return BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "unknown browser_context_reference"},
                attempted_network_requests=0,
                freeze=False,
            )
        if runtime.lease.frozen:
            return self._failed(runtime, "BLOCKED", "browser context is frozen")
        if snapshot_fp != runtime.snapshot_fingerprint:
            return self._failed(runtime, "BLOCKED", "stale snapshot")
        control = next((item for item in runtime.controls if item.element_reference == element_ref), None)
        if control is None:
            return self._failed(runtime, "BLOCKED", "unknown element_reference")
        if control.disabled:
            return self._failed(runtime, "BLOCKED", "control is disabled")
        if kind == "fill" and control.input_type == "password":
            return self._failed(runtime, "BLOCKED", "password fields cannot be filled")
        self._begin_action(runtime, envelope, limits)
        try:
            locator = runtime.page.locator(CONTROL_SELECTOR)
            index = int(element_ref.split("-", 1)[1])
            target = locator.nth(index)
            timeout = limits.max_action_runtime_ms
            if kind == "click":
                target.click(timeout=timeout)
            elif kind == "fill":
                target.fill(value or "", timeout=timeout)
            elif kind == "select":
                target.select_option(value or "", timeout=timeout)
            elif kind == "submit":
                target.press("Enter", timeout=timeout)
            else:
                return self._failed(runtime, "BLOCKED", "interact kind is not allowed")
        except self._timeout_error:
            return self._failed(runtime, "TIMED_OUT", "action timed out")
        except (ValueError, IndexError):
            return self._failed(runtime, "BLOCKED", "unknown element_reference")
        except Exception as exc:
            self._settle(runtime)
            pending = self._pending(runtime)
            if pending is not None:
                return pending
            return self._failed(runtime, "EXECUTION_FAILED", type(exc).__name__)
        self._settle(runtime)
        pending = self._pending(runtime)
        if pending is not None:
            return pending
        spa = self._check_main_url(runtime, channel="SPA")
        if spa is not None:
            return spa
        self._take_snapshot(runtime)
        return self._succeeded(runtime)

    def _acquire(
        self,
        lease_key: str | None,
        url: str,
        cookie: str | None,
        binding: dict[str, Any],
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> tuple[_Runtime | None, BrowserActionResult | None]:
        if lease_key:
            runtime = self._runtimes.get(lease_key)
            if runtime is None or runtime.lease.generation != PROCESS_GENERATION:
                return None, BrowserActionResult(
                    status="EXECUTION_FAILED",
                    raw={},
                    diagnostics={"error": "unknown browser_context_reference"},
                    attempted_network_requests=0,
                    freeze=False,
                )
            if not lease_binding_matches(runtime.lease, binding):
                return None, BrowserActionResult(
                    status="BLOCKED",
                    raw={},
                    diagnostics={"error": "browser context binding mismatch"},
                    attempted_network_requests=0,
                    freeze=False,
                )
            if cookie:
                self._seed_cookie(runtime.context, cookie, str(binding.get("origin") or url))
            self._begin_action(runtime, envelope, limits)
            return runtime, None
        if self._browser is None:
            return None, BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "browser engine is not started", "reason_code": "IMPLEMENTATION_NOT_AVAILABLE"},
                attempted_network_requests=0,
                freeze=False,
            )
        if len(self._runtimes) >= limits.max_active_contexts:
            self._evict_oldest_runtime()
        if len(self._runtimes) >= limits.max_active_contexts:
            return None, BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "max_active_contexts exceeded"},
                attempted_network_requests=0,
                freeze=False,
            )
        context_ref = f"ctx-{uuid.uuid4().hex}"
        page_ref = f"page-{uuid.uuid4().hex}"
        context_options = {"service_workers": "block", "accept_downloads": False}
        required_user_agent = binding.get("required_user_agent")
        if isinstance(required_user_agent, str) and required_user_agent:
            context_options["user_agent"] = required_user_agent

        required_headers = binding.get("required_headers")
        if isinstance(required_headers, dict) and required_headers:
            context_options["extra_http_headers"] = dict(required_headers)

        try:
            context = self._browser.new_context(**context_options)
        except TypeError:
            try:
                context_options.pop("accept_downloads", None)
                context = self._browser.new_context(**context_options)
            except TypeError:
                return None, BrowserActionResult(
                    status="EXECUTION_FAILED",
                    raw={},
                    diagnostics={
                        "error": "service_workers=block is required",
                        "reason_code": "IMPLEMENTATION_NOT_AVAILABLE",
                    },
                    attempted_network_requests=0,
                    freeze=False,
                )
        if limits.max_pages_per_context < 1:
            context.close()
            return None, BrowserActionResult(
                status="EXECUTION_FAILED",
                raw={},
                diagnostics={"error": "max_pages_per_context exceeded"},
                attempted_network_requests=0,
                freeze=False,
            )
        page = context.new_page()
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
        runtime = _Runtime(
            lease=lease,
            context=context,
            page=page,
            envelope=envelope,
            limits=limits,
            attempted=0,
            network_events=[],
            controls=[],
            snapshot_fingerprint="",
            pending_status=None,
            pending_channel=None,
            pending_raw_location=None,
            pending_response_url=None,
            pending_error=None,
            committed=False,
            ready_state="loading",
            frame_count=1,
        )
        self._runtimes[context_ref] = runtime
        self._install_guards(runtime)
        self._seed_cookie(context, cookie, str(binding.get("origin") or url))
        self._begin_action(runtime, envelope, limits)
        return runtime, None

    def _install_guards(self, runtime: _Runtime) -> None:
        page = runtime.page
        context = runtime.context
        context_ref = runtime.lease.context_ref
        context.route("**/*", lambda route, _request=None, key=context_ref: self._handle_route(key, route))
        page.on("popup", lambda popup, key=context_ref: self._on_popup(key, popup))
        page.on("websocket", lambda ws, key=context_ref: self._on_websocket(key, ws))
        page.on("download", lambda download, key=context_ref: self._on_download(key, download))
        page.on("framenavigated", lambda frame, key=context_ref: self._on_frame_navigated(key, frame))
        context.on("page", lambda new_page, key=context_ref: self._on_extra_page(key, new_page))

    def _begin_action(
        self,
        runtime: _Runtime,
        envelope: BrowserNetworkEnvelope,
        limits: BrowserRuntimeLimits,
    ) -> None:
        runtime.envelope = envelope
        runtime.limits = limits
        runtime.attempted = 0
        runtime.network_events = []
        runtime.pending_status = None
        runtime.pending_channel = None
        runtime.pending_raw_location = None
        runtime.pending_response_url = None
        runtime.pending_error = None

    def _handle_route(self, context_ref: str, route: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        if runtime is None or runtime.envelope is None:
            route.abort()
            return
        request = route.request
        url = request.url
        if runtime.attempted >= runtime.limits.max_attempted_network_requests_per_action:
            runtime.pending_status = "BUDGET_EXHAUSTED"
            runtime.pending_error = "attempted network requests would exceed max"
            route.abort()
            return
        runtime.attempted += 1
        allowed, reason = envelope_allows(runtime.envelope, url)
        if not allowed:
            self._record_event(runtime, request, status_code=None, redirect=False, url=url)
            if reason == UNSUPPORTED_SCHEME or not url_is_representable(url):
                runtime.pending_status = "BLOCKED"
                runtime.pending_error = reason
            else:
                frame = getattr(request, "frame", None)
                channel = "IFRAME" if frame is not None and frame != runtime.page.main_frame else "REDIRECT"
                self._mark_reauth(runtime, channel, url, runtime.page.url or url)
            route.abort()
            return
        try:
            response = route.fetch(max_redirects=0)
        except Exception:
            route.abort()
            return
        location = _header(response, "location")
        disposition = _header(response, "content-disposition").lower()
        if "attachment" in disposition:
            runtime.pending_status = "BLOCKED"
            runtime.pending_error = "download is not allowed"
            route.abort()
            return
        if int(response.status) in REDIRECT_STATUSES:
            self._record_event(runtime, request, status_code=int(response.status), redirect=True, url=url)
            self._mark_reauth(runtime, "REDIRECT", location or url, url)
            route.abort()
            return
        self._record_event(runtime, request, status_code=int(response.status), redirect=False, url=url)
        try:
            route.fulfill(response=response)
        except Exception:
            route.abort()

    def _on_popup(self, context_ref: str, popup: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        popup_url = ""
        try:
            popup_url = popup.url
            popup.close()
        except Exception:
            pass
        if runtime is None:
            return
        self._mark_reauth(runtime, "POPUP", popup_url, runtime.page.url)
        self.freeze(runtime.lease)

    def _on_extra_page(self, context_ref: str, new_page: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        if runtime is None:
            return
        if new_page is runtime.page:
            return
        extra_url = ""
        try:
            extra_url = new_page.url
            new_page.close()
        except Exception:
            pass
        self._mark_reauth(runtime, "POPUP", extra_url, runtime.page.url)
        self.freeze(runtime.lease)

    def _on_websocket(self, context_ref: str, websocket: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        try:
            websocket.close()
        except Exception:
            pass
        if runtime is None:
            return
        runtime.pending_status = "BLOCKED"
        runtime.pending_error = "websocket is not allowed"

    def _on_download(self, context_ref: str, download: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        try:
            download.cancel()
        except Exception:
            pass
        if runtime is None:
            return
        runtime.pending_status = "BLOCKED"
        runtime.pending_error = "download is not allowed"

    def _on_frame_navigated(self, context_ref: str, frame: Any) -> None:
        runtime = self._runtimes.get(context_ref)
        if runtime is None or runtime.envelope is None:
            return
        url = frame.url or ""
        if not url or url.startswith("about:"):
            return
        allowed, reason = envelope_allows(runtime.envelope, url)
        if allowed:
            if frame == runtime.page.main_frame:
                runtime.frame_count = max(runtime.frame_count, len(runtime.page.frames))
            else:
                runtime.frame_count = max(runtime.frame_count, len(runtime.page.frames))
            return
        if frame == runtime.page.main_frame:
            if runtime.committed:
                self._mark_reauth(runtime, "SPA", url, runtime.page.url)
                self.freeze(runtime.lease)
            return
        channel = "IFRAME"
        if reason == UNSUPPORTED_SCHEME or not url_is_representable(url):
            runtime.pending_status = "BLOCKED"
            runtime.pending_error = "iframe target is outside envelope"
            self.freeze(runtime.lease)
            return
        self._mark_reauth(runtime, channel, url, runtime.page.url)
        self.freeze(runtime.lease)

    def _goto(self, runtime: _Runtime, url: str, limits: BrowserRuntimeLimits) -> BrowserActionResult:
        try:
            runtime.page.goto(url, wait_until="domcontentloaded", timeout=limits.max_navigation_runtime_ms)
            runtime.committed = True
        except self._timeout_error:
            pending = self._pending(runtime)
            if pending is not None:
                return pending
            return self._failed(runtime, "TIMED_OUT", "navigation timed out")
        except Exception:
            self._settle(runtime)
            pending = self._pending(runtime)
            if pending is not None:
                return pending
            return self._failed(runtime, "EXECUTION_FAILED", "navigation failed")
        self._settle(runtime)
        pending = self._pending(runtime)
        if pending is not None:
            return pending
        spa = self._check_main_url(runtime, channel="SPA")
        if spa is not None:
            return spa
        self._take_snapshot(runtime)
        return self._succeeded(runtime)

    def _check_main_url(self, runtime: _Runtime, *, channel: str) -> BrowserActionResult | None:
        if runtime.envelope is None:
            return None
        url = runtime.page.url
        allowed, reason = envelope_allows(runtime.envelope, url)
        if allowed:
            return None
        self.freeze(runtime.lease)
        return self._denied(
            url,
            reason,
            attempted=runtime.attempted,
            freeze=True,
            runtime=runtime,
            channel=channel,
            response_url=url,
        )

    def _take_snapshot(self, runtime: _Runtime) -> None:
        locator = runtime.page.locator(CONTROL_SELECTOR)
        items = locator.evaluate_all(CONTROL_EXTRACT_SCRIPT)
        if not isinstance(items, list):
            items = []
        items = items[: runtime.limits.max_control_refs]
        signatures = []
        controls: list[ControlRef] = []
        url = runtime.page.url
        for item in items:
            if not isinstance(item, dict):
                continue
            href_scheme, href_origin, href_path = safe_href_parts(item.get("href"), url)
            signatures.append(
                {
                    "tag": str(item.get("tag") or ""),
                    "role": cap_text(str(item.get("role") or "")),
                    "input_type": str(item.get("input_type") or "").lower(),
                    "name": cap_text(str(item.get("name") or "")),
                    "aria_label": cap_text(str(item.get("aria_label") or "")),
                    "placeholder": cap_text(str(item.get("placeholder") or "")),
                    "href_scheme": href_scheme,
                    "href_origin": href_origin,
                    "href_path": href_path,
                    "disabled": bool(item.get("disabled")),
                    "checked": bool(item.get("checked")),
                }
            )
        fingerprint = fingerprint_controls(url, signatures)
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            href_scheme, href_origin, href_path = safe_href_parts(item.get("href"), url)
            controls.append(
                ControlRef(
                    element_reference=f"el-{index}",
                    snapshot_fingerprint=fingerprint,
                    tag=str(item.get("tag") or "").lower(),
                    role=cap_text(str(item.get("role") or "")),
                    input_type=str(item.get("input_type") or "").lower(),
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
        runtime.controls = controls
        runtime.snapshot_fingerprint = fingerprint
        runtime.ready_state = "complete"
        runtime.frame_count = len(runtime.page.frames)

    def _seed_cookie(self, context: Any, cookie: str | None, origin: str) -> None:
        if not cookie:
            return
        parsed = urlsplit(origin if "://" in origin else f"http://{origin}")
        cookie_url = f"{parsed.scheme or 'http'}://{parsed.hostname or '127.0.0.1'}"
        if parsed.port:
            cookie_url = f"{cookie_url}:{parsed.port}"
        cookies: list[dict[str, str]] = []
        for part in cookie.split(";"):
            piece = part.strip()
            if "=" not in piece:
                continue
            name, value = piece.split("=", 1)
            name = name.strip()
            if not name:
                continue
            cookies.append({"name": name, "value": value, "url": cookie_url + "/"})
        if not cookies:
            return
        context.add_cookies(cookies)

    def _record_event(
        self,
        runtime: _Runtime,
        request: Any,
        *,
        status_code: int | None,
        redirect: bool,
        url: str,
    ) -> None:
        if len(runtime.network_events) >= runtime.limits.max_network_events:
            return
        method = str(getattr(request, "method", "GET") or "GET")
        resource_type = str(getattr(request, "resource_type", "other") or "other")
        normalized = normalize_target(url) or ""
        try:
            path = urlsplit(url).path or "/"
        except ValueError:
            path = "/"
        request_bytes = 0
        post = getattr(request, "post_data", None)
        if isinstance(post, str):
            request_bytes = len(post.encode("utf-8"))
        elif isinstance(post, (bytes, bytearray)):
            request_bytes = len(post)
        runtime.network_events.append(
            NetworkEvent(
                event_id=f"ne-{len(runtime.network_events) + 1}",
                method=method,
                resource_type=resource_type,
                normalized_target=normalized,
                path=path,
                status_code=status_code,
                request_bytes=request_bytes,
                response_bytes=0,
                redirect=redirect,
                representability=REPRESENTABLE if url_is_representable(url) else NOT_REPRESENTABLE,
            )
        )

    def _mark_reauth(self, runtime: _Runtime, channel: str, raw_location: str, response_url: str) -> None:
        runtime.pending_status = "REAUTHORIZATION_REQUIRED"
        runtime.pending_channel = channel
        runtime.pending_raw_location = raw_location
        runtime.pending_response_url = response_url
        runtime.pending_error = None

    def _settle(self, runtime: _Runtime) -> None:
        try:
            runtime.page.wait_for_timeout(200)
        except Exception:
            pass

    def _pending(self, runtime: _Runtime) -> BrowserActionResult | None:
        if runtime.pending_status is None:
            return None
        if runtime.pending_status == "REAUTHORIZATION_REQUIRED":
            raw_location = runtime.pending_raw_location or ""
            response_url = runtime.pending_response_url or runtime.page.url
            resolved = normalize_target(raw_location) or raw_location
            raw = {
                "attempted_network_requests": runtime.attempted,
                "browser_context_reference": runtime.lease.context_ref,
                "page_reference": runtime.lease.page_ref,
                "followed": False,
            }
            return BrowserActionResult(
                status="REAUTHORIZATION_REQUIRED",
                raw=raw,
                diagnostics=reauthorization_diagnostics(
                    channel=runtime.pending_channel or "REDIRECT",
                    raw_location=raw_location,
                    response_url=response_url,
                    location=resolved,
                ),
                attempted_network_requests=runtime.attempted,
                freeze=runtime.lease.frozen,
            )
        if runtime.pending_status == "BUDGET_EXHAUSTED":
            return BrowserActionResult(
                status="BUDGET_EXHAUSTED",
                raw={
                    "attempted_network_requests": runtime.attempted,
                    "browser_context_reference": runtime.lease.context_ref,
                    "page_reference": runtime.lease.page_ref,
                },
                diagnostics={
                    "error": runtime.pending_error or "attempted network requests would exceed max",
                    "self_authorized": False,
                },
                attempted_network_requests=runtime.attempted,
                freeze=False,
            )
        return self._failed(runtime, runtime.pending_status, runtime.pending_error or "blocked")

    def _succeeded(self, runtime: _Runtime) -> BrowserActionResult:
        normalized = normalize_target(runtime.page.url) or runtime.page.url
        raw = snapshot_raw(
            attempted_network_requests=runtime.attempted,
            browser_context_reference=runtime.lease.context_ref,
            page_reference=runtime.lease.page_ref,
            snapshot_fingerprint=runtime.snapshot_fingerprint,
            normalized_url=normalized,
            ready_state=runtime.ready_state,
            frame_count=runtime.frame_count,
            controls=[control_to_mapping(item) for item in runtime.controls],
            network_events=[network_event_to_mapping(item) for item in runtime.network_events],
        )
        return BrowserActionResult(
            status="SUCCEEDED",
            raw=raw,
            diagnostics=None,
            attempted_network_requests=runtime.attempted,
            freeze=runtime.lease.frozen,
        )

    def _failed(self, runtime: _Runtime, status: str, error: str) -> BrowserActionResult:
        return BrowserActionResult(
            status=status,
            raw={
                "attempted_network_requests": runtime.attempted,
                "browser_context_reference": runtime.lease.context_ref,
                "page_reference": runtime.lease.page_ref,
            },
            diagnostics={"error": error, "self_authorized": False},
            attempted_network_requests=runtime.attempted,
            freeze=runtime.lease.frozen,
        )

    def _denied(
        self,
        url: str,
        reason: str,
        *,
        attempted: int,
        freeze: bool,
        runtime: _Runtime | None,
        channel: str | None = None,
        response_url: str | None = None,
    ) -> BrowserActionResult:
        if reason == UNSUPPORTED_SCHEME or not url_is_representable(url):
            raw: dict[str, Any] = {"attempted_network_requests": attempted, "followed": False}
            if runtime is not None:
                raw["browser_context_reference"] = runtime.lease.context_ref
                raw["page_reference"] = runtime.lease.page_ref
            return BrowserActionResult(
                status="BLOCKED",
                raw=raw,
                diagnostics={"error": reason, "self_authorized": False, "followed": False},
                attempted_network_requests=attempted,
                freeze=freeze,
            )
        resolved = normalize_target(url) or url
        raw = {"attempted_network_requests": attempted, "followed": False}
        if runtime is not None:
            raw["browser_context_reference"] = runtime.lease.context_ref
            raw["page_reference"] = runtime.lease.page_ref
        return BrowserActionResult(
            status="REAUTHORIZATION_REQUIRED",
            raw=raw,
            diagnostics=reauthorization_diagnostics(
                channel=channel or "REDIRECT",
                raw_location=url,
                response_url=response_url or url,
                location=resolved,
            ),
            attempted_network_requests=attempted,
            freeze=freeze,
        )

    def _evict_oldest_runtime(self) -> None:
        if not self._runtimes:
            return
        oldest_ref = next(iter(self._runtimes))
        runtime = self._runtimes.pop(oldest_ref)
        self._close_runtime(runtime)

    def _close_runtime(self, runtime: _Runtime) -> None:
        try:
            runtime.page.close()
        except Exception:
            pass
        try:
            runtime.context.close()
        except Exception:
            pass


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value)
    return ""
