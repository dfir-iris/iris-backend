#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for pure demo_builder functions.

Only the functions that do no DB work are tested here:
  gen_demo_admins / gen_demo_users  — deterministic RNG, yield structure
  _case_base_time                   — deterministic offset from case index
  random_filename (flask_dropzone)  — extension preserved, UUID slug

All are pure Python with no request or app context needed.
"""

import datetime
from unittest import TestCase

from app.flask_dropzone.utils import random_filename
from app.iris_engine.demo_builder import _case_base_time, gen_demo_admins, gen_demo_users


class TestGenDemoAdmins(TestCase):

    def test_yields_count_minus_one_entries(self):
        # range(1, count) → count-1 items
        entries = list(gen_demo_admins(4, seed_adm=0))
        self.assertEqual(3, len(entries))

    def test_each_entry_is_four_tuple(self):
        for entry in gen_demo_admins(3, seed_adm=1):
            self.assertEqual(4, len(entry))

    def test_login_pattern(self):
        for i, (_name, login, _pw, _key) in enumerate(gen_demo_admins(5, seed_adm=7), start=1):
            self.assertEqual(f'adm_{i}', login)

    def test_name_pattern(self):
        for i, (name, _login, _pw, _key) in enumerate(gen_demo_admins(5, seed_adm=7), start=1):
            self.assertEqual(f'Adm {i}', name)

    def test_password_is_16_chars(self):
        for _name, _login, pw, _key in gen_demo_admins(3, seed_adm=5):
            self.assertEqual(16, len(pw))

    def test_api_key_is_64_chars(self):
        for _name, _login, _pw, key in gen_demo_admins(3, seed_adm=5):
            self.assertEqual(64, len(key))

    def test_deterministic_with_same_seed(self):
        entries_a = list(gen_demo_admins(5, seed_adm=42))
        entries_b = list(gen_demo_admins(5, seed_adm=42))
        self.assertEqual(entries_a, entries_b)

    def test_different_seed_gives_different_passwords(self):
        pw_a = list(gen_demo_admins(3, seed_adm=1))[0][2]
        pw_b = list(gen_demo_admins(3, seed_adm=2))[0][2]
        self.assertNotEqual(pw_a, pw_b)

    def test_count_zero_yields_nothing(self):
        self.assertEqual([], list(gen_demo_admins(1, seed_adm=0)))

    def test_count_one_yields_nothing(self):
        self.assertEqual([], list(gen_demo_admins(1, seed_adm=0)))


class TestGenDemoUsers(TestCase):

    def test_yields_count_minus_one_entries(self):
        entries = list(gen_demo_users(4, seed_user=0))
        self.assertEqual(3, len(entries))

    def test_each_entry_is_four_tuple(self):
        for entry in gen_demo_users(3, seed_user=1):
            self.assertEqual(4, len(entry))

    def test_login_pattern(self):
        for i, (_name, login, _pw, _key) in enumerate(gen_demo_users(5, seed_user=7), start=1):
            self.assertEqual(f'user_std_{i}', login)

    def test_name_pattern(self):
        for i, (name, _login, _pw, _key) in enumerate(gen_demo_users(5, seed_user=7), start=1):
            self.assertEqual(f'User Std {i}', name)

    def test_password_is_16_chars(self):
        for _name, _login, pw, _key in gen_demo_users(3, seed_user=5):
            self.assertEqual(16, len(pw))

    def test_api_key_is_64_chars(self):
        for _name, _login, _pw, key in gen_demo_users(3, seed_user=5):
            self.assertEqual(64, len(key))

    def test_deterministic_with_same_seed(self):
        a = list(gen_demo_users(5, seed_user=99))
        b = list(gen_demo_users(5, seed_user=99))
        self.assertEqual(a, b)

    def test_different_seed_differs(self):
        pw_a = list(gen_demo_users(3, seed_user=10))[0][2]
        pw_b = list(gen_demo_users(3, seed_user=20))[0][2]
        self.assertNotEqual(pw_a, pw_b)


class TestCaseBaseTime(TestCase):

    def test_returns_datetime(self):
        result = _case_base_time(0)
        self.assertIsInstance(result, datetime.datetime)

    def test_seconds_and_microseconds_zeroed(self):
        for i in range(10):
            t = _case_base_time(i)
            self.assertEqual(0, t.second)
            self.assertEqual(0, t.microsecond)

    def test_hour_in_valid_range(self):
        # 7 + (i * 5) % 11  → [7, 18]
        for i in range(20):
            t = _case_base_time(i)
            self.assertGreaterEqual(t.hour, 7)
            self.assertLessEqual(t.hour, 18)

    def test_minute_in_valid_range(self):
        for i in range(20):
            t = _case_base_time(i)
            self.assertGreaterEqual(t.minute, 0)
            self.assertLessEqual(t.minute, 59)

    def test_result_is_in_the_past(self):
        now = datetime.datetime.utcnow()
        for i in range(5):
            self.assertLess(_case_base_time(i), now)

    def test_different_indices_give_different_times(self):
        times = [_case_base_time(i) for i in range(10)]
        self.assertGreater(len(set(times)), 1)


class TestRandomFilename(TestCase):

    def test_preserves_extension(self):
        result = random_filename('report.pdf')
        self.assertTrue(result.endswith('.pdf'))

    def test_no_extension_produces_no_dot(self):
        result = random_filename('noext')
        self.assertNotIn('.', result)

    def test_result_is_32_hex_chars_plus_extension(self):
        result = random_filename('file.zip')
        stem, ext = result.rsplit('.', 1)
        self.assertRegex(stem, r'^[0-9a-f]{32}$')
        self.assertEqual('zip', ext)

    def test_double_extension_uses_last(self):
        # os.path.splitext('archive.tar.gz') → ('archive.tar', '.gz')
        result = random_filename('archive.tar.gz')
        self.assertTrue(result.endswith('.gz'))

    def test_each_call_produces_unique_name(self):
        names = {random_filename('x.bin') for _ in range(20)}
        self.assertEqual(20, len(names))

    def test_empty_extension(self):
        result = random_filename('nodot')
        self.assertEqual(32, len(result))

    def test_result_is_string(self):
        self.assertIsInstance(random_filename('test.txt'), str)
