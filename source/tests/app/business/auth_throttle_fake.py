#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""In-memory stand-in for `datamgmt.manage.manage_auth_throttle_db`.

The throttles keep their counters in the `auth_throttle` table now, and
the unit suite has no database. Patching the five persistence helpers out
with something that behaves like the table keeps the tests on the part
that has the security opinions — ceilings, buckets, what a success
clears — which is where they were pointed all along.

Faithful in the two ways that matter to those tests: the failure count
rolls over when the window expires, and locking clears the count (so a
bucket does not re-lock on its first attempt after the lockout ends).
What it cannot stand in for is the atomicity of the real
`INSERT ... ON CONFLICT`, which is the whole reason the counter moved
out of process — that needs a live stack with more than one worker.
"""

from datetime import datetime
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch


class AuthThrottleFake:
    """The five helpers, backed by a dict. `install` patches a module."""

    def __init__(self):
        # key -> {'failures': int, 'window_start': datetime,
        #         'locked_until': datetime | None}
        self.rows = {}

    def install(self, module_path):
        """Patch this fake over the helpers `module_path` imported.

        Only over the ones it actually imported — `mfa_throttle` has no
        clear-all, and patching a name a module never bound would fail on
        a detail of the module rather than on anything the caller cares
        about.
        """
        module = import_module(module_path)
        for name in ('auth_throttle_register_failure',
                     'auth_throttle_lockout_seconds',
                     'auth_throttle_lock',
                     'auth_throttle_clear',
                     'auth_throttle_clear_all'):
            if not hasattr(module, name):
                continue
            patch(f'{module_path}.{name}', getattr(self, name)).start()

    def auth_throttle_register_failure(self, key, window_seconds):
        now = datetime.utcnow()
        row = self.rows.get(key)
        if row is None or row['window_start'] < now - timedelta(seconds=window_seconds):
            row = {'failures': 0, 'window_start': now,
                   'locked_until': None if row is None else row['locked_until']}
            self.rows[key] = row

        row['failures'] += 1

        return row['failures']

    def auth_throttle_lockout_seconds(self, key):
        row = self.rows.get(key)
        if row is None or row['locked_until'] is None:
            return 0

        remaining = (row['locked_until'] - datetime.utcnow()).total_seconds()

        return int(remaining) if remaining > 0 else 0

    def auth_throttle_lock(self, key, lockout_seconds):
        row = self.rows.setdefault(
            key, {'failures': 0, 'window_start': datetime.utcnow(),
                  'locked_until': None})
        row['failures'] = 0
        row['locked_until'] = datetime.utcnow() + timedelta(seconds=lockout_seconds)

    def auth_throttle_clear(self, key):
        self.rows.pop(key, None)

    def auth_throttle_clear_all(self):
        self.rows.clear()
