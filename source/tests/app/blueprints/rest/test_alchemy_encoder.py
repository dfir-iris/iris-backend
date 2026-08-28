#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the AlchemyEncoder JSON encoder in blueprints/responses.py.

The non-ORM paths (Decimal, datetime, date, UUID, unknown types) are pure
and need no Flask or DB context.
"""

import datetime
import decimal
import json
import uuid
from unittest import TestCase

from app.blueprints.responses import AlchemyEncoder


def _encode(obj):
    return json.dumps(obj, cls=AlchemyEncoder)


class TestAlchemyEncoderDecimal(TestCase):

    def test_decimal_serialized_as_string(self):
        result = json.loads(_encode(decimal.Decimal('3.14')))
        self.assertEqual('3.14', result)

    def test_decimal_zero_serialized(self):
        result = json.loads(_encode(decimal.Decimal('0')))
        self.assertEqual('0', result)

    def test_decimal_negative(self):
        result = json.loads(_encode(decimal.Decimal('-99.99')))
        self.assertEqual('-99.99', result)


class TestAlchemyEncoderDatetime(TestCase):

    def test_datetime_serialized_as_isoformat(self):
        dt = datetime.datetime(2026, 1, 15, 10, 30, 0)
        result = json.loads(_encode(dt))
        self.assertEqual(dt.isoformat(), result)

    def test_date_serialized_as_isoformat(self):
        d = datetime.date(2026, 3, 20)
        result = json.loads(_encode(d))
        self.assertEqual(d.isoformat(), result)


class TestAlchemyEncoderUUID(TestCase):

    def test_uuid_serialized_as_string(self):
        uid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        result = json.loads(_encode(uid))
        self.assertEqual(str(uid), result)

    def test_uuid4_serialized_as_string(self):
        uid = uuid.uuid4()
        result = json.loads(_encode(uid))
        self.assertEqual(str(uid), result)


class TestAlchemyEncoderUnknown(TestCase):

    def test_unknown_type_raises_type_error(self):
        # Objects that are not Decimal/datetime/UUID/bytes/ORM model
        # fall through to the parent JSONEncoder which raises TypeError
        with self.assertRaises(TypeError):
            _encode(object())
