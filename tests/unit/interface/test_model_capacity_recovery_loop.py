from __future__ import annotations

import ast
from pathlib import Path
import unittest

import pathsetup  # noqa: F401

import zest.interface.zestd as zestd_interface

from zest.interface.zestd import (
    _model_capacity_reconcile_loop,
)


class _OneIterationStop:
    def __init__(self):
        self.calls = 0

    def wait(self, _seconds):
        self.calls += 1
        return self.calls > 1


class ModelCapacityRecoveryLoopTests(
    unittest.TestCase
):
    def test_startup_live_reconcile_proof_is_forwarded(
        self,
    ) -> None:
        stop = _OneIterationStop()
        recover_args = []

        _model_capacity_reconcile_loop(
            stop,
            lambda: True,
            lambda **kwargs:
                recover_args.append(kwargs),
            reconcile_success_proves_live=True,
            interval_seconds=0,
        )

        self.assertEqual(
            recover_args,
            [
                {
                    "live_already_confirmed":
                        True,
                }
            ],
        )

    def test_metadata_only_success_is_not_live_proof(
        self,
    ) -> None:
        stop = _OneIterationStop()
        recover_args = []

        _model_capacity_reconcile_loop(
            stop,
            lambda: True,
            lambda **kwargs:
                recover_args.append(kwargs),
            reconcile_success_proves_live=False,
            interval_seconds=0,
        )

        self.assertEqual(
            recover_args,
            [
                {
                    "live_already_confirmed":
                        False,
                }
            ],
        )

    def test_reconcile_failure_still_runs_fail_closed_scan(
        self,
    ) -> None:
        stop = _OneIterationStop()
        recover_args = []

        def fail():
            raise RuntimeError(
                "metadata unavailable"
            )

        _model_capacity_reconcile_loop(
            stop,
            fail,
            lambda **kwargs:
                recover_args.append(kwargs),
            reconcile_success_proves_live=True,
            interval_seconds=0,
        )

        self.assertEqual(
            recover_args,
            [
                {
                    "live_already_confirmed":
                        False,
                }
            ],
        )

    def test_main_wires_explicit_live_callback_and_periodic_scan(
        self,
    ) -> None:
        path = Path(
            zestd_interface.__file__
        )

        tree = ast.parse(
            path.read_text(
                encoding="utf-8"
            ),
            filename=str(path),
        )

        mains = [
            node
            for node in tree.body
            if (
                isinstance(
                    node,
                    ast.FunctionDef,
                )
                and node.name == "main"
            )
        ]

        self.assertEqual(
            len(mains),
            1,
        )

        main = mains[0]

        runtime_calls = [
            node
            for node in ast.walk(main)
            if (
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Name,
                )
                and node.func.id
                == "ZestdRuntime"
            )
        ]

        self.assertEqual(
            len(runtime_calls),
            1,
        )

        runtime_call = runtime_calls[0]

        recovery_keywords = [
            kw
            for kw in runtime_call.keywords
            if kw.arg
            == "probe_model_recovery"
        ]

        self.assertEqual(
            len(recovery_keywords),
            1,
        )

        recovery_value = (
            recovery_keywords[0].value
        )

        self.assertIsInstance(
            recovery_value,
            ast.Lambda,
        )

        lambda_call = recovery_value.body

        self.assertIsInstance(
            lambda_call,
            ast.Call,
        )

        self.assertIsInstance(
            lambda_call.func,
            ast.Name,
        )

        self.assertEqual(
            lambda_call.func.id,
            "reconcile_model_health",
        )

        require_live = [
            kw
            for kw in lambda_call.keywords
            if kw.arg == "require_live"
        ]

        self.assertEqual(
            len(require_live),
            1,
        )

        self.assertIsInstance(
            require_live[0].value,
            ast.Constant,
        )

        self.assertIs(
            require_live[0].value.value,
            True,
        )

        thread_calls = [
            node
            for node in ast.walk(main)
            if (
                isinstance(node, ast.Call)
                and isinstance(
                    node.func,
                    ast.Attribute,
                )
                and node.func.attr
                == "Thread"
            )
        ]

        reconcile_threads = []

        for call in thread_calls:
            target = next(
                (
                    kw.value
                    for kw in call.keywords
                    if kw.arg == "target"
                ),
                None,
            )

            if (
                isinstance(
                    target,
                    ast.Name,
                )
                and target.id
                == "_model_capacity_reconcile_loop"
            ):
                reconcile_threads.append(
                    call
                )

        self.assertEqual(
            len(reconcile_threads),
            1,
        )

        thread = reconcile_threads[0]

        args_kw = next(
            kw.value
            for kw in thread.keywords
            if kw.arg == "args"
        )

        self.assertIsInstance(
            args_kw,
            ast.Tuple,
        )

        self.assertEqual(
            len(args_kw.elts),
            3,
        )

        third = args_kw.elts[2]

        self.assertIsInstance(
            third,
            ast.Attribute,
        )

        self.assertEqual(
            third.attr,
            "recover_model_usage_limited_runs",
        )


if __name__ == "__main__":
    unittest.main()
