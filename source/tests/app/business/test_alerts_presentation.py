#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
#  contact@dfir-iris.org

"""Unit tests for the pure presentation helpers in business/alerts.py.

_get_font, _get_icon_alert, _get_icon_ioc, _get_icon_case are all
pure dict/string return functions — no DB, no Flask context.
"""

from unittest import TestCase

from app.business.alerts import (
    _TokenUserView,
    _create_asset_node,
    _create_case_node,
    _create_ioc_node,
    _get_font,
    _get_icon_alert,
    _get_icon_case,
    _get_icon_ioc,
)


class TestGetFont(TestCase):

    def test_dark_mode_returns_white_string(self):
        result = _get_font(True)
        self.assertIn('white', result)

    def test_light_mode_returns_empty_string(self):
        self.assertEqual('', _get_font(False))

    def test_returns_string(self):
        self.assertIsInstance(_get_font(True), str)
        self.assertIsInstance(_get_font(False), str)


class TestGetIconAlert(TestCase):

    def test_returns_dict(self):
        self.assertIsInstance(_get_icon_alert('#ff0000'), dict)

    def test_color_is_set(self):
        result = _get_icon_alert('#ff0000')
        self.assertEqual('#ff0000', result['color'])

    def test_face_is_font_awesome(self):
        result = _get_icon_alert('red')
        self.assertEqual('FontAwesome', result['face'])

    def test_has_code_key(self):
        result = _get_icon_alert('red')
        self.assertIn('code', result)

    def test_different_colors_produce_different_results(self):
        r1 = _get_icon_alert('#aaa')
        r2 = _get_icon_alert('#bbb')
        self.assertNotEqual(r1['color'], r2['color'])


class TestGetIconIoc(TestCase):

    def test_returns_dict(self):
        self.assertIsInstance(_get_icon_ioc(True), dict)

    def test_dark_mode_color_is_white(self):
        result = _get_icon_ioc(True)
        self.assertEqual('white', result['color'])

    def test_light_mode_color_is_empty(self):
        result = _get_icon_ioc(False)
        self.assertEqual('', result['color'])

    def test_face_is_font_awesome(self):
        self.assertEqual('FontAwesome', _get_icon_ioc(True)['face'])

    def test_has_code_key(self):
        self.assertIn('code', _get_icon_ioc(False))


class TestGetIconCase(TestCase):

    def test_returns_dict(self):
        self.assertIsInstance(_get_icon_case(True), dict)

    def test_closed_case_has_closed_color(self):
        result = _get_icon_case(True)
        self.assertIn('#c95029', result['color'])

    def test_open_case_has_open_color(self):
        result = _get_icon_case(False)
        self.assertIn('#4cba4f', result['color'])

    def test_face_is_font_awesome(self):
        self.assertEqual('FontAwesome', _get_icon_case(True)['face'])

    def test_closed_and_open_colors_differ(self):
        closed = _get_icon_case(True)['color']
        opened = _get_icon_case(False)['color']
        self.assertNotEqual(closed, opened)

    def test_has_code_key(self):
        self.assertIn('code', _get_icon_case(False))


class TestTokenUserView(TestCase):

    def _make(self, **kwargs):
        defaults = {
            'user_id': 7,
            'user_login': 'alice',
            'user_name': 'Alice Smith',
            'user_email': 'alice@example.com',
        }
        defaults.update(kwargs)
        return _TokenUserView(defaults)

    def test_id_set(self):
        self.assertEqual(7, self._make().id)

    def test_user_login_set(self):
        self.assertEqual('alice', self._make().user)

    def test_name_set(self):
        self.assertEqual('Alice Smith', self._make().name)

    def test_email_set(self):
        self.assertEqual('alice@example.com', self._make().email)

    def test_is_authenticated_true(self):
        self.assertTrue(self._make().is_authenticated)

    def test_is_active_true(self):
        self.assertTrue(self._make().is_active)

    def test_is_anonymous_false(self):
        self.assertFalse(self._make().is_anonymous)


class TestCreateIocNode(TestCase):

    def test_id_format(self):
        result = _create_ioc_node(False, '1.2.3.4')
        self.assertEqual('ioc_1.2.3.4', result['id'])

    def test_label_is_ioc_value(self):
        result = _create_ioc_node(False, 'evil.com')
        self.assertEqual('evil.com', result['label'])

    def test_group_is_ioc(self):
        result = _create_ioc_node(False, 'x')
        self.assertEqual('ioc', result['group'])

    def test_shape_is_icon(self):
        result = _create_ioc_node(False, 'x')
        self.assertEqual('icon', result['shape'])

    def test_has_icon_and_font_keys(self):
        result = _create_ioc_node(True, 'x')
        self.assertIn('icon', result)
        self.assertIn('font', result)


class TestCreateCaseNode(TestCase):

    def test_id_format(self):
        result = _create_case_node(7, 'desc', None, False)
        self.assertEqual('case_7', result['id'])

    def test_open_case_label(self):
        result = _create_case_node(7, 'desc', None, False)
        self.assertIn('Case #7', result['label'])
        self.assertNotIn('Closed', result['label'])

    def test_closed_case_label(self):
        result = _create_case_node(7, 'desc', '2026-01-01', False)
        self.assertIn('Closed', result['label'])

    def test_description_in_title(self):
        result = _create_case_node(7, 'My incident', None, False)
        self.assertEqual('My incident', result['title'])

    def test_group_is_case(self):
        result = _create_case_node(7, 'desc', None, False)
        self.assertEqual('case', result['group'])


class TestCreateAssetNode(TestCase):

    def test_id_format(self):
        result = _create_asset_node('svr01', {'icon': 'server.png'}, False)
        self.assertEqual('asset_svr01', result['id'])

    def test_label_is_asset_id(self):
        result = _create_asset_node('myhost', {'icon': 'pc.png'}, False)
        self.assertEqual('myhost', result['label'])

    def test_group_is_asset(self):
        result = _create_asset_node('x', {'icon': 'generic.png'}, False)
        self.assertEqual('asset', result['group'])

    def test_image_path_contains_icon(self):
        result = _create_asset_node('x', {'icon': 'server.png'}, False)
        self.assertIn('server.png', result['image'])
