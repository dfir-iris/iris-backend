#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the _safe decorator in iris_engine/notifications/hook_listeners.py.

_safe wraps a listener so exceptions never bubble up. Tested without
any Flask / DB context.
"""

from unittest import TestCase

from app.iris_engine.notifications.hook_listeners import _safe


class TestSafeDecorator(TestCase):

    def test_normal_function_result_returned(self):
        @_safe
        def fn():
            return 42
        self.assertEqual(42, fn())

    def test_exception_suppressed_and_returns_none(self):
        @_safe
        def fn():
            raise ValueError('boom')
        result = fn()
        self.assertIsNone(result)

    def test_wrapped_name_preserved(self):
        @_safe
        def my_listener():
            pass
        self.assertEqual('my_listener', my_listener.__name__)

    def test_args_passed_through(self):
        @_safe
        def fn(x, y):
            return x + y
        self.assertEqual(5, fn(2, 3))

    def test_kwargs_passed_through(self):
        @_safe
        def fn(x=0, y=0):
            return x * y
        self.assertEqual(6, fn(x=2, y=3))

    def test_runtime_error_suppressed(self):
        @_safe
        def fn():
            raise RuntimeError('critical failure')
        self.assertIsNone(fn())

    def test_multiple_exceptions_suppressed(self):
        call_count = []

        @_safe
        def fn():
            call_count.append(1)
            raise TypeError('oops')

        fn()
        fn()
        self.assertEqual(2, len(call_count))
