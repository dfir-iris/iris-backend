#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for poll_is_closed in business/war_room_chat.py.

The function only inspects `poll.closed_at` and `poll.closes_at`
against utcnow — no DB, no Flask context needed.
"""

import datetime
from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.business.war_room_chat import poll_is_closed


def _poll(closed_at=None, closes_at=None):
    poll = MagicMock()
    poll.closed_at = closed_at
    poll.closes_at = closes_at
    return poll


_PAST = datetime.datetime(2000, 1, 1)
_FUTURE = datetime.datetime(9999, 12, 31)
_NOW = datetime.datetime(2026, 6, 1, 12, 0, 0)


class TestPollIsClosed(TestCase):

    def test_closed_at_set_returns_true(self):
        self.assertTrue(poll_is_closed(_poll(closed_at=_NOW)))

    def test_closed_at_none_closes_at_none_returns_false(self):
        self.assertFalse(poll_is_closed(_poll()))

    def test_closes_at_in_past_returns_true(self):
        with patch('app.business.war_room_chat.datetime') as mock_dt:
            mock_dt.datetime.utcnow.return_value = _NOW
            self.assertTrue(poll_is_closed(_poll(closes_at=_PAST)))

    def test_closes_at_in_future_returns_false(self):
        with patch('app.business.war_room_chat.datetime') as mock_dt:
            mock_dt.datetime.utcnow.return_value = _NOW
            self.assertFalse(poll_is_closed(_poll(closes_at=_FUTURE)))

    def test_closes_at_equal_to_now_returns_true(self):
        with patch('app.business.war_room_chat.datetime') as mock_dt:
            mock_dt.datetime.utcnow.return_value = _NOW
            self.assertTrue(poll_is_closed(_poll(closes_at=_NOW)))

    def test_closed_at_takes_precedence_over_closes_at(self):
        with patch('app.business.war_room_chat.datetime') as mock_dt:
            mock_dt.datetime.utcnow.return_value = _NOW
            # Both set; closed_at wins
            self.assertTrue(poll_is_closed(_poll(closed_at=_NOW, closes_at=_FUTURE)))
