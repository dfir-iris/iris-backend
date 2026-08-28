#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for app.iris_engine.llm.redaction.

Pure-Python — no DB, no Flask context required.
"""

import unittest

from app.iris_engine.llm.redaction import redact_content_blocks, redact_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MD5 = "d41d8cd98f00b204e9800998ecf8427e"   # 32 hex chars
_SHA1 = "da39a3ee5e6b4b0d3255bfef95601890afd80709"  # 40 hex chars
_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # 64 hex chars


# ===========================================================================
# redact_text
# ===========================================================================


class TestRedactTextEmptyAndIdentity(unittest.TestCase):

    def test_empty_string_returns_empty(self):
        self.assertEqual("", redact_text("", redact_ips=True, redact_emails=True, redact_hashes=True))

    def test_all_flags_off_is_identity(self):
        text = "user@example.com 8.8.8.8 " + _MD5
        result = redact_text(text, redact_ips=False, redact_emails=False, redact_hashes=False)
        self.assertEqual(text, result)

    def test_all_flags_off_no_change_to_clean_text(self):
        text = "no pii here at all"
        self.assertEqual(text, redact_text(text, redact_ips=False, redact_emails=False, redact_hashes=False))


class TestRedactTextIPv4(unittest.TestCase):

    def test_ipv4_redacted(self):
        result = redact_text("Contact 8.8.8.8 now", redact_ips=True, redact_emails=False, redact_hashes=False)
        self.assertIn("<redacted-ip>", result)
        self.assertNotIn("8.8.8.8", result)

    def test_ipv4_boundary_match(self):
        result = redact_text("1.2.3.4", redact_ips=True, redact_emails=False, redact_hashes=False)
        self.assertEqual("<redacted-ip>", result)

    def test_invalid_ipv4_256_not_matched(self):
        text = "256.0.0.1"
        result = redact_text(text, redact_ips=True, redact_emails=False, redact_hashes=False)
        self.assertEqual(text, result)

    def test_ipv4_flag_off_preserves_ip(self):
        text = "IP is 10.0.0.1"
        result = redact_text(text, redact_ips=False, redact_emails=False, redact_hashes=False)
        self.assertIn("10.0.0.1", result)


class TestRedactTextEmail(unittest.TestCase):

    def test_email_redacted(self):
        result = redact_text("user@example.com", redact_ips=False, redact_emails=True, redact_hashes=False)
        self.assertEqual("<redacted-email>", result)

    def test_email_flag_off_preserves_email(self):
        text = "user@example.com"
        result = redact_text(text, redact_ips=False, redact_emails=False, redact_hashes=False)
        self.assertEqual(text, result)

    def test_email_in_sentence(self):
        result = redact_text("Send to user@example.com please", redact_ips=False, redact_emails=True, redact_hashes=False)
        self.assertIn("<redacted-email>", result)
        self.assertNotIn("user@example.com", result)


class TestRedactTextHashes(unittest.TestCase):

    def test_md5_redacted(self):
        result = redact_text(_MD5, redact_ips=False, redact_emails=False, redact_hashes=True)
        self.assertEqual("<redacted-hash>", result)

    def test_sha1_redacted(self):
        result = redact_text(_SHA1, redact_ips=False, redact_emails=False, redact_hashes=True)
        self.assertEqual("<redacted-hash>", result)

    def test_sha256_redacted(self):
        result = redact_text(_SHA256, redact_ips=False, redact_emails=False, redact_hashes=True)
        self.assertEqual("<redacted-hash>", result)

    def test_31_char_hex_not_matched(self):
        short = "a" * 31
        result = redact_text(short, redact_ips=False, redact_emails=False, redact_hashes=True)
        self.assertEqual(short, result)

    def test_33_char_hex_not_matched(self):
        long_hex = "a" * 33
        result = redact_text(long_hex, redact_ips=False, redact_emails=False, redact_hashes=True)
        self.assertEqual(long_hex, result)

    def test_hash_flag_off_preserves_md5(self):
        result = redact_text(_MD5, redact_ips=False, redact_emails=False, redact_hashes=False)
        self.assertEqual(_MD5, result)


class TestRedactTextMultipleAndPartial(unittest.TestCase):

    def test_multiple_replacements_in_one_string(self):
        text = f"ip=8.8.8.8 email=user@example.com hash={_MD5}"
        result = redact_text(text, redact_ips=True, redact_emails=True, redact_hashes=True)
        self.assertIn("<redacted-ip>", result)
        self.assertIn("<redacted-email>", result)
        self.assertIn("<redacted-hash>", result)
        self.assertNotIn("8.8.8.8", result)
        self.assertNotIn("user@example.com", result)
        self.assertNotIn(_MD5, result)

    def test_partial_flags_only_ip(self):
        text = f"ip=8.8.8.8 email=user@example.com hash={_MD5}"
        result = redact_text(text, redact_ips=True, redact_emails=False, redact_hashes=False)
        self.assertIn("<redacted-ip>", result)
        self.assertIn("user@example.com", result)
        self.assertIn(_MD5, result)

    def test_partial_flags_only_email(self):
        text = "ip=8.8.8.8 email=user@example.com"
        result = redact_text(text, redact_ips=False, redact_emails=True, redact_hashes=False)
        self.assertIn("8.8.8.8", result)
        self.assertIn("<redacted-email>", result)


# ===========================================================================
# redact_content_blocks
# ===========================================================================


class TestRedactContentBlocksAllFlagsOff(unittest.TestCase):

    def test_all_flags_off_returns_blocks_unchanged(self):
        blocks = [{"type": "text", "text": "8.8.8.8 user@example.com " + _MD5}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=False, redact_emails=False, redact_hashes=False
        )
        self.assertIs(blocks, result)
        self.assertFalse(applied)

    def test_all_flags_off_applied_is_false(self):
        _, applied = redact_content_blocks(
            [], redact_ips=False, redact_emails=False, redact_hashes=False
        )
        self.assertFalse(applied)


class TestRedactContentBlocksTextBlock(unittest.TestCase):

    def test_text_block_ip_redacted(self):
        blocks = [{"type": "text", "text": "ip=8.8.8.8"}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=False, redact_hashes=False
        )
        self.assertEqual("<redacted-ip>", result[0]["text"].split("ip=")[1])
        self.assertTrue(applied)

    def test_text_block_email_redacted(self):
        blocks = [{"type": "text", "text": "user@example.com"}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=False, redact_emails=True, redact_hashes=False
        )
        self.assertEqual("<redacted-email>", result[0]["text"])
        self.assertTrue(applied)

    def test_text_block_hash_redacted(self):
        blocks = [{"type": "text", "text": _MD5}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=False, redact_emails=False, redact_hashes=True
        )
        self.assertEqual("<redacted-hash>", result[0]["text"])
        self.assertTrue(applied)

    def test_no_match_returns_false(self):
        blocks = [{"type": "text", "text": "nothing sensitive here"}]
        _, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertFalse(applied)


class TestRedactContentBlocksNestedList(unittest.TestCase):

    def test_nested_list_in_content(self):
        blocks = [
            {
                "type": "tool_result",
                "content": [
                    {"type": "text", "text": "ip=8.8.8.8"},
                    {"type": "text", "text": "clean"},
                ],
            }
        ]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=False, redact_hashes=False
        )
        self.assertIn("<redacted-ip>", result[0]["content"][0]["text"])
        self.assertEqual("clean", result[0]["content"][1]["text"])
        self.assertTrue(applied)

    def test_nested_list_no_match_is_false(self):
        blocks = [{"type": "tool_result", "content": [{"type": "text", "text": "safe"}]}]
        _, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertFalse(applied)


class TestRedactContentBlocksNestedDict(unittest.TestCase):

    def test_tool_input_dict_values_redacted(self):
        blocks = [
            {
                "type": "tool_use",
                "input": {"query": "lookup 8.8.8.8", "email": "admin@corp.com"},
            }
        ]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=True, redact_hashes=False
        )
        self.assertIn("<redacted-ip>", result[0]["input"]["query"])
        self.assertEqual("<redacted-email>", result[0]["input"]["email"])
        self.assertTrue(applied)

    def test_arguments_dict_values_redacted(self):
        blocks = [{"type": "tool_use", "arguments": {"hash": _SHA256}}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=False, redact_emails=False, redact_hashes=True
        )
        self.assertEqual("<redacted-hash>", result[0]["arguments"]["hash"])
        self.assertTrue(applied)


class TestRedactContentBlocksMixed(unittest.TestCase):

    def test_mixed_block_types(self):
        blocks = [
            {"type": "text", "text": "normal text"},
            {"type": "text", "text": "8.8.8.8"},
            {"type": "tool_use", "input": {"x": _MD5}},
        ]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=False, redact_hashes=True
        )
        self.assertEqual("normal text", result[0]["text"])
        self.assertEqual("<redacted-ip>", result[1]["text"])
        self.assertEqual("<redacted-hash>", result[2]["input"]["x"])
        self.assertTrue(applied)

    def test_non_string_values_pass_through(self):
        blocks = [{"type": "tool_use", "input": {"count": 42, "flag": True}}]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertEqual(42, result[0]["input"]["count"])
        self.assertTrue(result[0]["input"]["flag"])
        self.assertFalse(applied)

    def test_stacking_multiple_block_types(self):
        blocks = [
            {"type": "text", "text": "a@b.com"},
            {"type": "text", "text": "1.2.3.4"},
            {"type": "text", "text": _SHA1},
        ]
        result, applied = redact_content_blocks(
            blocks, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertEqual("<redacted-email>", result[0]["text"])
        self.assertEqual("<redacted-ip>", result[1]["text"])
        self.assertEqual("<redacted-hash>", result[2]["text"])
        self.assertTrue(applied)

    def test_changed_flag_true_only_when_something_replaced(self):
        blocks_clean = [{"type": "text", "text": "clean"}]
        _, applied_clean = redact_content_blocks(
            blocks_clean, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertFalse(applied_clean)

        blocks_dirty = [{"type": "text", "text": "8.8.8.8"}]
        _, applied_dirty = redact_content_blocks(
            blocks_dirty, redact_ips=True, redact_emails=True, redact_hashes=True
        )
        self.assertTrue(applied_dirty)


if __name__ == "__main__":
    unittest.main()
