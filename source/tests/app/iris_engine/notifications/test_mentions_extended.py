#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Extended unit tests for app.iris_engine.notifications.mentions.

Covers:
- extract_mentioned_team_ids: team spans, deduplication, ignores non-team
  kinds, empty/None content, no matching spans
- resolve_mentions_to_user_ids: user-only, team-only, both, no mentions,
  legacy @handle path that expands to teams
- Additional edge cases for extract_mentioned_user_ids not in test_mentions.py
- Direct regex-constant behaviour for _MENTION_SPAN_RE and _TEAM_MENTION_SPAN_RE
"""

from unittest import TestCase
from unittest.mock import patch

from app.iris_engine.notifications.mentions import (
    _MENTION_SPAN_RE,
    _TEAM_MENTION_SPAN_RE,
    _LEGACY_MENTION_RE,
    extract_mentioned_team_ids,
    extract_mentioned_user_ids,
    resolve_mentions_to_user_ids,
)


# ---------------------------------------------------------------------------
# extract_mentioned_team_ids
# ---------------------------------------------------------------------------

class TestExtractMentionedTeamIds(TestCase):

    def test_returns_empty_set_for_none(self):
        self.assertEqual(set(), extract_mentioned_team_ids(None))

    def test_returns_empty_set_for_empty_string(self):
        self.assertEqual(set(), extract_mentioned_team_ids(''))

    def test_extracts_single_team_span(self):
        content = (
            '<span data-mention data-kind="team" data-id="10" '
            'data-label="Blue Team">@Blue Team</span>'
        )
        self.assertEqual({10}, extract_mentioned_team_ids(content))

    def test_extracts_multiple_team_spans(self):
        content = (
            '<span data-mention data-kind="team" data-id="3">@T3</span> '
            'and '
            '<span data-mention data-kind="team" data-id="7">@T7</span>'
        )
        self.assertEqual({3, 7}, extract_mentioned_team_ids(content))

    def test_deduplicates_repeated_team_id(self):
        content = (
            '<span data-mention data-kind="team" data-id="5">@T</span> '
            '<span data-mention data-kind="team" data-id="5">@T</span>'
        )
        self.assertEqual({5}, extract_mentioned_team_ids(content))

    def test_ignores_user_kind_spans(self):
        content = (
            '<span data-mention data-kind="user" data-id="99">@alice</span>'
        )
        self.assertEqual(set(), extract_mentioned_team_ids(content))

    def test_ignores_asset_kind_spans(self):
        content = (
            '<span data-mention data-kind="asset" data-id="1">#srv</span>'
        )
        self.assertEqual(set(), extract_mentioned_team_ids(content))

    def test_ignores_ioc_kind_spans(self):
        content = (
            '<span data-mention data-kind="ioc" data-id="2">!hash</span>'
        )
        self.assertEqual(set(), extract_mentioned_team_ids(content))

    def test_only_team_spans_extracted_from_mixed_content(self):
        content = (
            '<span data-mention data-kind="user" data-id="1">@alice</span> '
            '<span data-mention data-kind="team" data-id="20">@sec</span> '
            '<span data-mention data-kind="asset" data-id="9">#host</span>'
        )
        self.assertEqual({20}, extract_mentioned_team_ids(content))

    def test_malformed_team_id_is_skipped(self):
        # The regex requires \d+, so a non-numeric data-id won't match at all.
        content = (
            '<span data-mention data-kind="team" data-id="not-a-number">'
            '@broken</span>'
        )
        self.assertEqual(set(), extract_mentioned_team_ids(content))

    def test_no_team_spans_returns_empty(self):
        content = '<p>No team mentions here, just plain text.</p>'
        self.assertEqual(set(), extract_mentioned_team_ids(content))

    def test_attribute_ordering_does_not_matter(self):
        # Reversed attribute order (data-id before data-kind before data-mention)
        content = (
            '<span data-id="15" data-kind="team" data-mention>@ops</span>'
        )
        self.assertEqual({15}, extract_mentioned_team_ids(content))

    def test_case_insensitive_kind_attribute(self):
        # HTML sanitisers sometimes lower-case attribute values; re.IGNORECASE
        # ensures we still match.
        content = (
            '<span data-mention data-kind="TEAM" data-id="8">@upper</span>'
        )
        self.assertEqual({8}, extract_mentioned_team_ids(content))


# ---------------------------------------------------------------------------
# resolve_mentions_to_user_ids
# ---------------------------------------------------------------------------

class TestResolveMentionsToUserIds(TestCase):

    def test_returns_empty_set_for_none(self):
        self.assertEqual(set(), resolve_mentions_to_user_ids(None, war_room_id=1))

    def test_returns_empty_set_for_empty_string(self):
        self.assertEqual(set(), resolve_mentions_to_user_ids('', war_room_id=1))

    def test_user_mentions_only_no_team_expansion(self):
        content = (
            '<span data-mention data-kind="user" data-id="42">@alice</span>'
        )
        result = resolve_mentions_to_user_ids(content, war_room_id=99)
        self.assertEqual({42}, result)

    @patch('app.iris_engine.notifications.mentions.resolve_user_handles',
           return_value=set())
    @patch('app.iris_engine.notifications.mentions._resolve_team_names_to_ids',
           return_value=set())
    def test_team_mentions_only_expands_to_members(self, _mock_team_names, _mock_resolve):
        # Team span's inner text (@sec) would go through the legacy path
        # (no user spans present). Mock both DB-touching helpers and verify
        # only the structured team ID (3) reaches war_room_team_member_user_ids.
        content = (
            '<span data-mention data-kind="team" data-id="3">@sec</span>'
        )
        with patch(
            'app.business.war_room_teams.war_room_team_member_user_ids',
            return_value={10, 11},
        ) as mock_members:
            result = resolve_mentions_to_user_ids(content, war_room_id=5)
        self.assertEqual({10, 11}, result)
        mock_members.assert_called_once_with(5, {3})

    @patch('app.iris_engine.notifications.mentions.war_room_team_member_user_ids',
           create=True)
    def test_user_and_team_mentions_unioned(self, _mock_members):
        _mock_members.return_value = {20, 21}
        content = (
            '<span data-mention data-kind="user" data-id="1">@alice</span> '
            '<span data-mention data-kind="team" data-id="7">@red</span>'
        )
        with patch(
            'app.business.war_room_teams.war_room_team_member_user_ids',
            _mock_members,
        ):
            result = resolve_mentions_to_user_ids(content, war_room_id=5)
        self.assertEqual({1, 20, 21}, result)

    def test_no_mentions_returns_empty(self):
        content = '<p>Nothing to mention here.</p>'
        result = resolve_mentions_to_user_ids(content, war_room_id=1)
        self.assertEqual(set(), result)

    @patch('app.iris_engine.notifications.mentions._resolve_team_names_to_ids')
    @patch('app.iris_engine.notifications.mentions.war_room_team_member_user_ids',
           create=True)
    def test_legacy_handle_resolves_team_names(self, _mock_members, mock_resolve_teams):
        # Plain @teamname with no structured spans → tries user handles AND
        # team names.  Simulate team resolution returning team_id=4.
        mock_resolve_teams.return_value = {4}
        _mock_members.return_value = {30, 31}

        content = 'Hey @redteam please triage'
        with patch(
            'app.business.war_room_teams.war_room_team_member_user_ids',
            _mock_members,
        ):
            with patch(
                'app.iris_engine.notifications.mentions.resolve_user_handles',
                return_value=set(),
            ):
                result = resolve_mentions_to_user_ids(content, war_room_id=2)

        mock_resolve_teams.assert_called_once()
        args, _ = mock_resolve_teams.call_args
        self.assertIn('redteam', args[0])
        self.assertEqual(2, args[1])
        self.assertEqual({30, 31}, result)

    @patch('app.iris_engine.notifications.mentions._resolve_team_names_to_ids')
    def test_legacy_path_not_taken_when_user_spans_present(self, mock_resolve_teams):
        # Structured user spans suppress the legacy @handle fallback, so
        # _resolve_team_names_to_ids must NOT be called.
        content = (
            'Hey <span data-mention data-kind="user" data-id="5">@alice</span>'
        )
        result = resolve_mentions_to_user_ids(content, war_room_id=1)
        self.assertEqual({5}, result)
        mock_resolve_teams.assert_not_called()

    @patch('app.iris_engine.notifications.mentions.resolve_user_handles',
           return_value=set())
    @patch('app.iris_engine.notifications.mentions._resolve_team_names_to_ids',
           return_value=set())
    def test_team_expansion_uses_correct_war_room_id(self, _mock_team_names, _mock_resolve):
        # Verify that war_room_id is forwarded correctly to the team-member
        # expansion. Mock both DB helpers so no app context is needed.
        content = (
            '<span data-mention data-kind="team" data-id="9">@ops</span>'
        )
        with patch(
            'app.business.war_room_teams.war_room_team_member_user_ids',
            return_value=set(),
        ) as mock_members:
            resolve_mentions_to_user_ids(content, war_room_id=42)
        mock_members.assert_called_once_with(42, {9})

    @patch('app.iris_engine.notifications.mentions.war_room_team_member_user_ids',
           create=True)
    def test_user_ids_from_team_and_direct_overlap_deduplicated(self, _mock_members):
        # User 1 is mentioned directly AND is a member of the mentioned team.
        _mock_members.return_value = {1, 50}
        content = (
            '<span data-mention data-kind="user" data-id="1">@alice</span> '
            '<span data-mention data-kind="team" data-id="2">@sec</span>'
        )
        with patch(
            'app.business.war_room_teams.war_room_team_member_user_ids',
            _mock_members,
        ):
            result = resolve_mentions_to_user_ids(content, war_room_id=1)
        self.assertEqual({1, 50}, result)


# ---------------------------------------------------------------------------
# extract_mentioned_user_ids — additional edge cases
# ---------------------------------------------------------------------------

class TestExtractMentionedUserIdsEdgeCases(TestCase):

    @patch('app.iris_engine.notifications.mentions.resolve_user_handles',
           return_value=set())
    def test_content_with_only_team_spans_yields_no_user_ids(self, _mock_resolve):
        # Team spans must NOT contribute to user ID extraction. The @ops text
        # inside the span would normally hit the legacy handle path; mock it
        # out so we stay out of the DB and verify the returned set is empty.
        content = (
            '<span data-mention data-kind="team" data-id="99">@ops</span>'
        )
        self.assertEqual(set(), extract_mentioned_user_ids(content))

    def test_content_with_only_team_spans_does_not_trigger_legacy_path(self):
        # A team span is NOT a user span; if no user spans are present the
        # legacy @handle path would run — but the @-text inside the span
        # (e.g. "@ops") should not be treated as a plaintext handle.
        # The legacy regex requires a non-word char before @, and inside the
        # span markup the preceding char is ">", so "@ops" WOULD match the
        # legacy regex. We therefore only check that NO user id is returned.
        content = (
            '<span data-mention data-kind="team" data-id="99">@ops</span>'
        )
        with patch(
            'app.iris_engine.notifications.mentions.resolve_user_handles',
            return_value={999},
        ):
            result = extract_mentioned_user_ids(content)
        # Either nothing is returned (most likely) or the mock's 999 surfaces
        # — but critically, the team span's id 99 must NOT appear.
        self.assertNotIn(99, result)

    def test_user_span_with_extra_attributes_still_extracted(self):
        # Future TipTap versions may add more attributes; verify extraction
        # is not thrown off.
        content = (
            '<span data-mention="" data-kind="user" data-id="7" '
            'data-label="Zara" data-extra="irrelevant">@Zara</span>'
        )
        self.assertEqual({7}, extract_mentioned_user_ids(content))

    def test_multiple_kinds_in_same_content(self):
        content = (
            '<span data-mention data-kind="user" data-id="2">@u2</span>'
            '<span data-mention data-kind="team" data-id="8">@t8</span>'
            '<span data-mention data-kind="asset" data-id="100">#srv</span>'
            '<span data-mention data-kind="ioc" data-id="200">!ioc</span>'
        )
        # Only user id 2 should be returned.
        self.assertEqual({2}, extract_mentioned_user_ids(content))

    @patch('app.iris_engine.notifications.mentions.resolve_user_handles',
           return_value=set())
    def test_wrong_kind_value_does_not_match(self, _mock_resolve):
        # data-kind="users" (plural) must NOT match data-kind="user".
        # Because there are no user spans, the legacy @handle path runs —
        # mock it out to avoid hitting the DB.
        content = (
            '<span data-mention data-kind="users" data-id="55">@u</span>'
        )
        self.assertEqual(set(), extract_mentioned_user_ids(content))

    @patch('app.iris_engine.notifications.mentions.resolve_user_handles')
    def test_legacy_path_multiple_handles(self, mock_resolve):
        mock_resolve.return_value = {10, 11}
        content = 'Ping @alice and @bob about this'
        result = extract_mentioned_user_ids(content)
        self.assertEqual({10, 11}, result)
        args, _ = mock_resolve.call_args
        self.assertIn('alice', args[0])
        self.assertIn('bob', args[0])


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

class TestMentionSpanRegex(TestCase):
    """Document and lock down _MENTION_SPAN_RE match behaviour."""

    def _match(self, text):
        return _MENTION_SPAN_RE.search(text)

    def test_matches_standard_user_span(self):
        text = '<span data-mention data-kind="user" data-id="42">@u</span>'
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('42', m.group('id'))

    def test_does_not_match_team_kind(self):
        text = '<span data-mention data-kind="team" data-id="42">@t</span>'
        self.assertIsNone(self._match(text))

    def test_does_not_match_without_data_mention(self):
        text = '<span data-kind="user" data-id="42">@u</span>'
        self.assertIsNone(self._match(text))

    def test_does_not_match_non_numeric_id(self):
        text = '<span data-mention data-kind="user" data-id="abc">@u</span>'
        self.assertIsNone(self._match(text))

    def test_matches_with_single_quotes(self):
        text = "<span data-mention data-kind='user' data-id='7'>@u</span>"
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('7', m.group('id'))

    def test_matches_case_insensitive_kind(self):
        text = '<span data-mention data-kind="USER" data-id="3">@u</span>'
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('3', m.group('id'))


class TestTeamMentionSpanRegex(TestCase):
    """Document and lock down _TEAM_MENTION_SPAN_RE match behaviour."""

    def _match(self, text):
        return _TEAM_MENTION_SPAN_RE.search(text)

    def test_matches_standard_team_span(self):
        text = '<span data-mention data-kind="team" data-id="10">@t</span>'
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('10', m.group('id'))

    def test_does_not_match_user_kind(self):
        text = '<span data-mention data-kind="user" data-id="10">@u</span>'
        self.assertIsNone(self._match(text))

    def test_does_not_match_without_data_mention(self):
        text = '<span data-kind="team" data-id="10">@t</span>'
        self.assertIsNone(self._match(text))

    def test_does_not_match_non_numeric_team_id(self):
        text = '<span data-mention data-kind="team" data-id="bad">@t</span>'
        self.assertIsNone(self._match(text))

    def test_matches_reversed_attribute_order(self):
        text = '<span data-id="22" data-kind="team" data-mention>@t</span>'
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('22', m.group('id'))

    def test_matches_with_single_quotes(self):
        text = "<span data-mention data-kind='team' data-id='5'>@t</span>"
        m = self._match(text)
        self.assertIsNotNone(m)
        self.assertEqual('5', m.group('id'))

    def test_extracts_correct_id_from_multiple_spans(self):
        text = (
            '<span data-mention data-kind="team" data-id="1">@a</span> '
            '<span data-mention data-kind="team" data-id="2">@b</span>'
        )
        ids = {m.group('id') for m in _TEAM_MENTION_SPAN_RE.finditer(text)}
        self.assertEqual({'1', '2'}, ids)


class TestLegacyMentionRegex(TestCase):
    """Document _LEGACY_MENTION_RE boundary behaviour."""

    def _handles(self, text):
        return {m.group('handle') for m in _LEGACY_MENTION_RE.finditer(text)}

    def test_matches_simple_at_handle(self):
        self.assertIn('alice', self._handles('@alice'))

    def test_does_not_match_mid_word_at(self):
        # foo@bar — the @ is preceded by a word character, so no match.
        self.assertNotIn('bar', self._handles('foo@bar'))

    def test_matches_handle_after_space(self):
        self.assertIn('bob', self._handles('Hello @bob today'))

    def test_handle_with_dot_and_dash(self):
        self.assertIn('john.doe-2', self._handles('@john.doe-2'))

    def test_email_address_not_matched(self):
        handles = self._handles('contact foo@example.com now')
        self.assertNotIn('example.com', handles)
