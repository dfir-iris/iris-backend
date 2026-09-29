#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the worker-side shape of app.configuration.Config.

`Config` trims a few web-only settings when IRIS_WORKER is set, but the
celery worker still needs SECRET_KEY: it verifies the HMAC the app put on
module hook payloads and derives the Fernet key for mail credentials from
it. Dropping it left Flask's `SECRET_KEY = None` default in place and
every hook task died on `TypeError: encoding without a string argument`.
"""

import importlib.util
import os
import sys
from unittest import TestCase
from unittest.mock import patch

_SECRET = 'test-secret-key-for-unit-tests'


def _load_config(environ):
    """Evaluate the `Config` class body under a given environment.

    The IRIS_WORKER guard lives in the class body, so it is only
    evaluated at import time. Loading a private copy of the module under
    a throwaway name — rather than reloading `app.configuration` — keeps
    the canonical module (and the exception classes other modules have
    already bound) untouched for the rest of the suite.
    """
    spec = importlib.util.find_spec('app.configuration')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, environ, clear=False):
        with patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)

    return module.Config


class TestWorkerConfiguration(TestCase):

    def test_secret_key_is_set_on_the_worker(self):
        config = _load_config({'IRIS_SECRET_KEY': _SECRET, 'IRIS_WORKER': 'iris-worker'})
        self.assertEqual(_SECRET, config.SECRET_KEY)

    def test_secret_key_is_set_on_the_app(self):
        environ = {'IRIS_SECRET_KEY': _SECRET}
        with patch.dict(os.environ, environ, clear=False):
            os.environ.pop('IRIS_WORKER', None)
            config = _load_config(environ)
        self.assertEqual(_SECRET, config.SECRET_KEY)

    def test_web_only_settings_are_still_trimmed_on_the_worker(self):
        config = _load_config({'IRIS_SECRET_KEY': _SECRET, 'IRIS_WORKER': 'iris-worker'})
        self.assertFalse(hasattr(config, 'CSRF_ENABLED'))
        self.assertFalse(hasattr(config, 'SECURITY_LOGIN_USER_TEMPLATE'))
