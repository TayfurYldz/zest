from __future__ import annotations

import unittest

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

    def test_all_twelve_navigation_destinations_are_declared(self) -> None:
        app = read_static_asset('js/app.js')[0].decode('utf-8')
        for view in (
            'mission', 'surface', 'hunter', 'hypotheses', 'experiments', 'models',
            'browser', 'oast', 'evidence', 'coverage', 'audit', 'setup',
        ):
            self.assertIn(f'["{view}"', app)
        self.assertEqual(app.count('render as render'), 12)

    def test_each_destination_has_a_separate_view_module(self) -> None:
        for view in (
            'mission', 'surface', 'hunter', 'hypotheses', 'experiments', 'models',
            'browser', 'oast', 'evidence', 'coverage', 'audit', 'setup',
        ):
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

        self.assertIn('aria-label="${escapeHtml(title)}"', app)
        for label in ('Experiment Control', 'Browser Operations', 'OAST Operations'):
            self.assertIn(f'"{label}"', app)
        self.assertIn('@media (max-width: 768px)', shell)
        self.assertIn('.inspector { width: 100%; }', shell)


if __name__ == '__main__':
    unittest.main()
