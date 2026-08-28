#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for enum and plain-class models that carry no DB logic."""

import enum
from unittest import TestCase

from app.models.cases import CaseStatus, ReviewStatusList
from app.models.war_rooms import WarRoomMemberRole, WarRoomState


class TestCaseStatus(TestCase):

    def test_is_enum(self):
        self.assertTrue(issubclass(CaseStatus, enum.Enum))

    def test_unknown_value(self):
        self.assertEqual(0x0, CaseStatus.unknown.value)

    def test_false_positive_value(self):
        self.assertEqual(0x1, CaseStatus.false_positive.value)

    def test_true_positive_with_impact_value(self):
        self.assertEqual(0x2, CaseStatus.true_positive_with_impact.value)

    def test_not_applicable_value(self):
        self.assertEqual(0x3, CaseStatus.not_applicable.value)

    def test_true_positive_without_impact_value(self):
        self.assertEqual(0x4, CaseStatus.true_positive_without_impact.value)

    def test_legitimate_value(self):
        self.assertEqual(0x5, CaseStatus.legitimate.value)

    def test_five_named_members(self):
        members = [m for m in CaseStatus if m != CaseStatus.unknown]
        self.assertEqual(5, len(members))


class TestReviewStatusList(TestCase):

    def test_no_review_required_label(self):
        self.assertEqual('No review required', ReviewStatusList.no_review_required)

    def test_not_reviewed_label(self):
        self.assertEqual('Not reviewed', ReviewStatusList.not_reviewed)

    def test_pending_review_label(self):
        self.assertEqual('Pending review', ReviewStatusList.pending_review)

    def test_review_in_progress_label(self):
        self.assertEqual('Review in progress', ReviewStatusList.review_in_progress)

    def test_reviewed_label(self):
        self.assertEqual('Reviewed', ReviewStatusList.reviewed)

    def test_all_values_are_strings(self):
        for attr in ('no_review_required', 'not_reviewed', 'pending_review',
                     'review_in_progress', 'reviewed'):
            with self.subTest(attr=attr):
                self.assertIsInstance(getattr(ReviewStatusList, attr), str)


class TestWarRoomState(TestCase):

    def test_is_enum(self):
        self.assertTrue(issubclass(WarRoomState, enum.Enum))

    def test_open_value(self):
        self.assertEqual('open', WarRoomState.open.value)

    def test_active_value(self):
        self.assertEqual('active', WarRoomState.active.value)

    def test_standby_value(self):
        self.assertEqual('standby', WarRoomState.standby.value)

    def test_closed_value(self):
        self.assertEqual('closed', WarRoomState.closed.value)

    def test_four_states(self):
        self.assertEqual(4, len(list(WarRoomState)))

    def test_values_are_strings(self):
        for state in WarRoomState:
            with self.subTest(state=state):
                self.assertIsInstance(state.value, str)


class TestWarRoomMemberRole(TestCase):

    def test_is_enum(self):
        self.assertTrue(issubclass(WarRoomMemberRole, enum.Enum))

    def test_lead_value(self):
        self.assertEqual('lead', WarRoomMemberRole.lead.value)

    def test_responder_value(self):
        self.assertEqual('responder', WarRoomMemberRole.responder.value)

    def test_observer_value(self):
        self.assertEqual('observer', WarRoomMemberRole.observer.value)

    def test_three_roles(self):
        self.assertEqual(3, len(list(WarRoomMemberRole)))
