#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure message-builder helper in mail/outbound.py.

_build_message assembles an EmailMessage from a SmtpConfig and arguments.
No SMTP connection, no DB, no Flask context.
"""

from email.message import EmailMessage
from unittest import TestCase

from app.iris_engine.mail.config import SmtpConfig
from app.iris_engine.mail.outbound import _build_message


def _smtp(*, from_name=None, from_address='noreply@example.com'):
    return SmtpConfig(
        host='smtp.example.com',
        port=587,
        user='user',
        password='pass',
        use_tls=True,
        use_ssl=False,
        from_address=from_address,
        from_name=from_name,
    )


class TestBuildMessage(TestCase):

    def test_returns_email_message(self):
        result = _build_message(_smtp(), ['to@test.com'], 'Subject', 'Body')
        self.assertIsInstance(result, EmailMessage)

    def test_subject_set(self):
        result = _build_message(_smtp(), ['to@test.com'], 'My Subject', 'body')
        self.assertEqual('My Subject', result['Subject'])

    def test_from_without_name_is_plain_address(self):
        cfg = _smtp(from_address='noreply@iris.example')
        result = _build_message(cfg, ['to@test.com'], 'S', 'b')
        self.assertEqual('noreply@iris.example', result['From'])

    def test_from_with_name_includes_display_name(self):
        cfg = _smtp(from_name='IRIS Bot', from_address='noreply@iris.example')
        result = _build_message(cfg, ['to@test.com'], 'S', 'b')
        self.assertIn('IRIS Bot', result['From'])
        self.assertIn('noreply@iris.example', result['From'])

    def test_single_recipient_in_to(self):
        result = _build_message(_smtp(), ['alice@test.com'], 'S', 'b')
        self.assertIn('alice@test.com', result['To'])

    def test_multiple_recipients_joined(self):
        result = _build_message(_smtp(), ['a@t.com', 'b@t.com'], 'S', 'b')
        self.assertIn('a@t.com', result['To'])
        self.assertIn('b@t.com', result['To'])

    def test_plain_text_body_set(self):
        result = _build_message(_smtp(), ['to@test.com'], 'S', 'Plain text here')
        payload = result.get_payload()
        # get_payload returns the string directly for non-multipart messages
        if isinstance(payload, str):
            self.assertIn('Plain text here', payload)
        else:
            # multipart — find the text/plain part
            parts = [p for p in payload if p.get_content_type() == 'text/plain']
            self.assertTrue(any('Plain text here' in (p.get_payload() or '') for p in parts))

    def test_html_body_adds_alternative(self):
        result = _build_message(
            _smtp(), ['to@test.com'], 'S', 'text', body_html='<b>html</b>'
        )
        # With HTML, the message should be multipart/alternative
        self.assertTrue(result.is_multipart())

    def test_no_html_body_is_not_multipart(self):
        result = _build_message(_smtp(), ['to@test.com'], 'S', 'text only')
        self.assertFalse(result.is_multipart())

    def test_empty_body_text_does_not_raise(self):
        result = _build_message(_smtp(), ['to@test.com'], 'S', '')
        self.assertIsNotNone(result)

    def test_none_body_text_does_not_raise(self):
        result = _build_message(_smtp(), ['to@test.com'], 'S', None)
        self.assertIsNotNone(result)
