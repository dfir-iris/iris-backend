#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure helpers in the observability reporter module.

_coerce_sample_rate is a pure function — no DB, no Flask context.
"""

from decimal import Decimal
from unittest import TestCase

from app.iris_engine.observability.reporter import _coerce_sample_rate


class TestCoerceSampleRate(TestCase):

    def test_none_returns_one(self):
        self.assertEqual(1.0, _coerce_sample_rate(None))

    def test_decimal_converts_to_float(self):
        result = _coerce_sample_rate(Decimal('0.5'))
        self.assertIsInstance(result, float)
        self.assertAlmostEqual(0.5, result)

    def test_int_converts_to_float(self):
        result = _coerce_sample_rate(1)
        self.assertIsInstance(result, float)
        self.assertEqual(1.0, result)

    def test_float_passes_through(self):
        self.assertEqual(0.25, _coerce_sample_rate(0.25))

    def test_string_float_converts(self):
        self.assertAlmostEqual(0.75, _coerce_sample_rate('0.75'))

    def test_invalid_string_returns_one(self):
        self.assertEqual(1.0, _coerce_sample_rate('not-a-float'))

    def test_empty_string_returns_one(self):
        self.assertEqual(1.0, _coerce_sample_rate(''))

    def test_list_returns_one(self):
        self.assertEqual(1.0, _coerce_sample_rate([0.5]))

    def test_zero_is_valid(self):
        self.assertEqual(0.0, _coerce_sample_rate(0))

    def test_greater_than_one_is_valid(self):
        self.assertEqual(2.0, _coerce_sample_rate(2))

    def test_decimal_zero_converts(self):
        self.assertEqual(0.0, _coerce_sample_rate(Decimal('0')))

    def test_return_type_is_always_float(self):
        for val in (None, 1, 0.5, Decimal('0.1'), 'bad', ''):
            with self.subTest(val=val):
                self.assertIsInstance(_coerce_sample_rate(val), float)
