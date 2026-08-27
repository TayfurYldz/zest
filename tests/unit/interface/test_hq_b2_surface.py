from __future__ import annotations

import unittest
from pathlib import Path

import pathsetup  # noqa: F401

from zest.hq import read_index, read_static_asset
from zest.interface.dashboard import HTML


class HqB2SurfaceTests(unittest.TestCase):
    def test_index_uses_modular_local_assets_without_inline_or_remote_assets(self) -> None:
        index = read_index()
        self.assertIn('/static/js/app.js', index)
        self.assertIn('/static/css/tokens.css', index)
        self.assertNotIn('<style', index.lower())
        self.assertNotIn('src="http', index.lower())
        self.assertNotIn('href="http', index.lower())

    def test_compact_operational_navigation_destinations_are_declared(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        for view in (
            'mission', 'research', 'execution', 'surface', 'evidence', 'authority', 'setup',
        ):
            self.assertIn(f'["{view}"', app)
        self.assertEqual(app.count('render as render'), 7)

    def test_each_destination_has_a_separate_view_module(self) -> None:
        for view in ('mission', 'research', 'hypotheses', 'experiments', 'surface', 'evidence', 'audit', 'setup'):
            content = read_static_asset(f'js/views/{view}.js')[0].decode('utf-8')
            self.assertIn('export function render', content)

    def test_static_path_containment_rejects_traversal_and_unknown_files(self) -> None:
        for path in ('../index.html', 'css/../index.html', 'js/../../pyproject.toml', 'missing.js'):
            with self.assertRaises(FileNotFoundError):
                read_static_asset(path)

    def test_dashboard_html_remains_a_local_read_only_boundary(self) -> None:
        self.assertIn('/api/runs/', read_static_asset('js/api.js')[0].decode('utf-8'))
        self.assertIn('/analysis', read_static_asset('js/api.js')[0].decode('utf-8'))
        self.assertIn('type="module"', HTML)
        self.assertIn('Projection only', HTML)
        self.assertNotIn('<script>', HTML)

    def test_accessibility_and_reduced_motion_contracts_exist(self) -> None:
        base = read_static_asset('css/base.css')[0].decode('utf-8')
        index = read_index()
        self.assertIn(':focus-visible', base)
        self.assertIn('prefers-reduced-motion', base)
        self.assertIn('aria-current', read_static_asset('js/app.js')[0].decode('utf-8'))
        self.assertIn('aria-label="Record inspector"', index)
        self.assertIn('Escape', read_static_asset('js/inspector.js')[0].decode('utf-8'))

    def test_navigation_names_and_narrow_inspector_contract(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        shell = read_static_asset('css/shell.css')[0].decode('utf-8')
        index = read_index()

        self.assertIn('aria-label="${escapeHtml(labels[id] || title)}"', app)
        for label in ('Canlı Operasyon', 'Araştırma', 'Programlar', 'Kanıtlar', 'Bulgular', 'Politika', 'Ayarlar'):
            self.assertIn(f'"{label}"', app)
        self.assertIn('@media (max-width: 768px)', shell)
        self.assertIn('.inspector { position: fixed;', shell)
        self.assertNotIn('minmax(280px, 340px)', shell)
        self.assertIn('id="truthStrip"', index)
        self.assertIn('role="dialog"', index)
        self.assertIn('hidden', index)

    def test_stage3_cockpit_contract_is_deterministic_and_bounded(self) -> None:
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        inspector = read_static_asset('js/inspector.js')[0].decode('utf-8')

        for marker in (
            'current_research_lineage', 'active_pipeline',
            'semantic_activity_timeline', 'failure_inspector', 'counters',
            'motorOrder', 'Observer slot · deterministic', 'No LLM inference',
            'WorkerResult', 'Observation', 'Evidence',
        ):
            self.assertIn(marker, mission)
        self.assertIn('element.hidden = true', inspector)
        self.assertIn('element.hidden = false', inspector)
        self.assertIn('event.key === "Escape"', inspector)

    def test_stage4_transport_and_failure_contract_is_declared(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        api = read_static_asset('js/api.js')[0].decode('utf-8')
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        operator_api = (Path(__file__).resolve().parents[3] / 'src/zest/interface/operator_api.py').read_text(encoding='utf-8')

        for marker in ('EventSource', 'LIVE', 'RECONNECTING', 'POLLING', 'OFFLINE', 'lastEventId', 'preserve: true'):
            self.assertIn(marker, app)
        self.assertIn('/events', api)
        self.assertIn('semantic_activity', operator_api)
        self.assertIn('Last-Event-ID', operator_api)
        self.assertIn('state_consistency_warnings', mission)
        self.assertIn('Failure inspector', mission)

    def test_stage5_observer_intent_and_verification_contract_is_declared(self) -> None:
        observer = (Path(__file__).resolve().parents[3] / 'src/zest/application/observer.py').read_text(encoding='utf-8')
        provider = (Path(__file__).resolve().parents[3] / 'src/zest/integrations/observer/nvidia.py').read_text(encoding='utf-8')
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        research = read_static_asset('js/views/research.js')[0].decode('utf-8')
        for marker in ('ObserverContext', 'ObserverProvider', 'validate_observer_brief', 'source_event_ids', 'ZEST_OBSERVER_ENABLED'):
            self.assertIn(marker, observer)
        self.assertIn('NvidiaCompatibleObserverProvider', provider)
        for marker in ('AI Observer', 'Non-authoritative narrator', 'confirmed_facts', 'unknowns', 'source_event_ids'):
            self.assertIn(marker, mission)
        for marker in ('research_intent', 'verification_chain', 'FALSE_POSITIVE', 'observer_can_finalize'):
            self.assertIn(marker, research)

    def test_glass_console_r3_makes_zest_the_primary_live_surface(self) -> None:
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        tokens = read_static_asset('css/tokens.css')[0].decode('utf-8')
        views = read_static_asset('css/views.css')[0].decode('utf-8')
        for marker in ('zest-live-panel', 'signal-strip', 'process-row', 'data-narration', 'prefers-reduced-motion'):
            self.assertIn(marker, mission + views)
        for marker in ('--z-canvas: #0B0B0C', '--z-accent: #E5484D', '--z-text-primary: #F4F4F5', '--z-glass: rgba(255,255,255,.05)'):
            self.assertIn(marker, tokens)
        self.assertIn('min-height: 536px', views)
        self.assertIn('LiveZestPanel', mission)
        self.assertIn('Context', mission)

    def test_final_console_uses_red_accent_and_incremental_transcript_contract(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        state = read_static_asset('js/state.js')[0].decode('utf-8')
        tokens = read_static_asset('css/tokens.css')[0].decode('utf-8')
        views = read_static_asset('css/views.css')[0].decode('utf-8')
        for marker in ('appendLiveEvent', 'background: true', 'notify: !background', 'lastEventId'):
            self.assertIn(marker, app)
        for marker in ('MAX_TRANSCRIPT_ROWS', 'data-live-follow', 'Yeni olayları göster', 'activity-item-new', 'localizedChip'):
            self.assertIn(marker, mission)
        self.assertIn('notify = true', state)
        self.assertIn('--z-accent: #E5484D', tokens)
        self.assertIn('background: #000000', views)
        self.assertIn('Canlı Operasyon', app)
        self.assertIn('ZEST', read_index())

    def test_r6_transcript_workspace_and_mobile_navigation_contract(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        index = read_index()
        mission = read_static_asset('js/views/mission.js')[0].decode('utf-8')
        shell = read_static_asset('css/shell.css')[0].decode('utf-8')
        views = read_static_asset('css/views.css')[0].decode('utf-8')
        self.assertIn('id="mobileNavToggle"', index)
        self.assertIn('mobile-nav-open', app)
        self.assertIn('transcript-shell', mission)
        self.assertNotIn('<dl class="runtime-context"', mission)
        self.assertNotIn('Mevcut araştırma</div>', mission)
        self.assertNotIn('class="recent-activity"', mission)
        self.assertIn('#truthStrip[hidden]', views)
        self.assertIn('body.mobile-nav-open .primary-rail', shell)


if __name__ == '__main__':
    unittest.main()
