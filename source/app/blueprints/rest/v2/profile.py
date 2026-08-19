#  IRIS Source Code
#  Copyright (C) 2024 - DFIR-IRIS
#  contact@dfir-iris.org
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU Lesser General Public
#  License as published by the Free Software Foundation; either
#  version 3 of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  Lesser General Public License for more details.
#
#  You should have received a copy of the GNU Lesser General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

import secrets
from flask import Blueprint
from flask import current_app
from flask import request
from flask import session
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app import bc
from app.db import db
from app.blueprints.iris_user import iris_current_user
from app.blueprints.rest.endpoints import response_api_success
from app.blueprints.rest.endpoints import response_api_created
from app.blueprints.rest.endpoints import response_api_deleted
from app.blueprints.rest.endpoints import response_api_error
from app.blueprints.rest.endpoints import response_api_not_found
from app.models.errors import BusinessProcessingError
from app.models.errors import ObjectNotFoundError
from app.blueprints.access_controls import ac_api_requires
from app.blueprints.access_controls import ac_fast_check_current_user_has_case_access
from app.blueprints.rest.api_doc import api_doc
from app.business.cases import cases_exists
from app.business.users import api_keys_create
from app.business.users import api_keys_list
from app.business.users import api_keys_revoke
from app.business.users import users_get
from app.business.users import users_update
from app.iris_engine.demo_builder import demo_mode_blocks_password_change
from app.iris_engine.access_control.utils import ac_get_effective_permissions_of_user
from app.iris_engine.access_control.utils import ac_recompute_effective_ac
from app.models.authorization import CaseAccessLevel
from app.models.authorization import Permissions
from app.models.authorization import UserFollowedCase
from app.models.cases import Cases
from app.schema.marshables import CaseSchemaForAPIV2
from app.schema.marshables import UserApiKeySchema
from app.schema.marshables import UserSchemaForAPIV2


class ProfileOperations:

    def __init__(self):
        self._schema = UserSchemaForAPIV2()
        self._update_request_schema = UserSchemaForAPIV2(exclude=['user_is_service_account', 'user_active', 'uuid'])

    def get(self):
        user = users_get(iris_current_user.id)
        result = self._schema.dump(user)
        return response_api_success(result)

    def update(self):
        try:
            user = users_get(iris_current_user.id)
            # Self-service profile updates expose only a password change in
            # the GUI. Restricting the payload to that one field stops any
            # client from sneaking attributes the schema would otherwise
            # accept (user_login, user_email, user_isadmin, user_name, ...)
            # and overwriting the user's own row — the mass-assignment
            # vector reported as GHSA-w78h-mx7h-qm3h / SBA-ADV-20260128-01 /
            # CWE-915. `user_current_password` is not a model field and is
            # popped off before the schema sees the payload.
            raw = request.get_json()
            if not isinstance(raw, dict):
                raw = {}
            new_password = raw.get('user_password')
            current_password = raw.get('user_current_password')

            if new_password and demo_mode_blocks_password_change():
                return response_api_error('Password changes are disabled in demo mode')

            if new_password:
                if not current_password:
                    return response_api_error(
                        'Current password is required to change password',
                        data={'user_current_password': ['Required field']}
                    )
                if not bc.check_password_hash(user.password, current_password):
                    return response_api_error(
                        'Current password is incorrect',
                        data={'user_current_password': ['Incorrect password']}
                    )

            request_data = {
                'user_password': new_password,
                'user_id': iris_current_user.id,
            }

            user = self._update_request_schema.load(request_data, instance=user, partial=True)
            user = users_update(user, new_password)
            result = self._schema.dump(user)
            return response_api_success(result)
        except ValidationError as e:
            return response_api_error('Data error', data=e.messages)

    def renew_api_key(self):
        user = users_get(iris_current_user.id)
        user.api_key = secrets.token_urlsafe(nbytes=64)
        db.session.commit()
        result = self._schema.dump(user)
        return response_api_success(result)

    # ---- Per-user API keys (UserApiKey) --------------------------------

    def list_api_keys(self):
        """List every API key belonging to the caller."""
        user = users_get(iris_current_user.id)
        rows = api_keys_list(user)
        return response_api_success({
            'api_keys': UserApiKeySchema(many=True).dump(rows),
        })

    def create_api_key(self):
        """Mint a new named, scope-restricted API key for the caller.

        Body: `{name: str, scope_mask?: int}`. `scope_mask` is an
        integer bitmask of `Permissions` values that will be AND-ed
        into the user's effective permissions on every request
        authenticated with this key. Omit for a full-permissions key
        (matches legacy behaviour).

        The plaintext `api_key` is returned exactly once in the
        response body — subsequent list/get calls only expose the
        metadata. The caller should copy it now and store it in their
        MCP client / CI configuration.
        """
        body = request.get_json(silent=True) or {}
        name = (body.get('name') or '').strip()
        scope_mask = body.get('scope_mask')
        if not name:
            return response_api_error('name is required')
        if scope_mask is not None and not isinstance(scope_mask, int):
            return response_api_error('scope_mask must be an integer bitmask')
        user = users_get(iris_current_user.id)
        try:
            row, plaintext = api_keys_create(user, name, scope_mask)
        except BusinessProcessingError as exc:
            return response_api_error(exc.get_message())
        payload = UserApiKeySchema().dump(row)
        # Plaintext key is out-of-band relative to the schema on purpose
        # — the schema never carries the raw key so subsequent GETs
        # can't accidentally re-emit it.
        payload['api_key'] = plaintext
        return response_api_created(payload)

    def revoke_api_key(self, key_id: int):
        """Revoke one of the caller's API keys (idempotent)."""
        user = users_get(iris_current_user.id)
        try:
            row = api_keys_revoke(user, key_id)
        except ObjectNotFoundError:
            return response_api_not_found()
        return response_api_success(UserApiKeySchema().dump(row))

    def refresh_permissions(self):
        user = users_get(iris_current_user.id)
        ac_recompute_effective_ac(iris_current_user.id)
        session['permissions'] = ac_get_effective_permissions_of_user(user)
        result = self._schema.dump(user)
        return response_api_success(result)

    def get_context(self):
        """Compact bootstrap payload the SPA needs on every load.

        Returns the running IRIS version, the demo-mode flag, and the
        effective permission mask + matching enum names for the current
        user. The SPA uses this to render the version strip in the side
        bar and to gate menu entries the user isn't allowed to reach.

        Kept lightweight on purpose: no DB writes, no joins beyond what
        `ac_get_effective_permissions_of_user` already does. Any
        authenticated user can call it — this is *their own* context.
        """
        user = users_get(iris_current_user.id)
        mask = ac_get_effective_permissions_of_user(user)
        # `standard_user` is implicit for every authenticated user, even
        # if the group bitmask doesn't include it (admins, service
        # accounts). Include it so the SPA can treat it as a baseline.
        if user is not None:
            mask |= Permissions.standard_user.value
        names = [p.name for p in Permissions if (mask & p.value) == p.value]

        demo_mode = current_app.config.get('DEMO_MODE_ENABLED') == 'True'

        return response_api_success({
            'iris_version': current_app.config.get('IRIS_VERSION'),
            'demo_mode': demo_mode,
            # The SPA pairs this with `demo_mode` to hide the server
            # settings section from everyone but the instance owner —
            # the mirror of `demo_mode_restricts_server_settings` on the
            # API side. Purely cosmetic: the routes enforce it too.
            'user_id': iris_current_user.id,
            'permissions': {
                'mask': mask,
                'names': names,
            },
            # Per-user UI preferences that the SPA shell needs on the
            # very first render so it doesn't flash one layout and then
            # snap into the persisted one. Currently a single boolean
            # for the collapsed side bar; keep the dict shape so we can
            # add more (theme, density, …) without breaking the SPA.
            'preferences': {
                'has_mini_sidebar': bool(getattr(user, 'has_mini_sidebar', False)) if user else False,
            },
        })

    def list_followed_cases(self):
        """Return cases the current user follows.

        Followed cases are surfaced on the dashboard's "Following" tile
        regardless of ownership. Rows the user has lost access to
        (revoked group membership, customer reassignment, etc.) are
        filtered out so the dashboard never renders a tile the user
        cannot open.
        """
        followed = (
            Cases.query
            .join(UserFollowedCase, UserFollowedCase.case_id == Cases.case_id)
            .filter(UserFollowedCase.user_id == iris_current_user.id)
            .order_by(UserFollowedCase.created_at.desc())
            .all()
        )

        # Drop entries the user is no longer allowed to see. We use the
        # fast check (read_only or full_access) because the dashboard
        # only renders a title + link — no protected fields are shipped.
        visible = [
            c for c in followed
            if ac_fast_check_current_user_has_case_access(
                c.case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]
            )
        ]

        # Use the v2 API schema so the response matches what the SPA's
        # `Case` type expects (case_name / case_customer / severity /
        # state). `CaseDetailsSchema` here would ship the raw model
        # field names (`name`, `client`, ...) and the dashboard
        # "Following" tile would render rows with no title.
        return response_api_success(data=CaseSchemaForAPIV2(many=True).dump(visible))

    def follow_case(self):
        """Add a case to the current user's followed-cases list.

        Idempotent: re-following an already-followed case returns 201
        rather than 400 to keep the SPA's "Follow" button safe to retry
        on flaky connections.
        """
        raw = request.get_json()
        if not isinstance(raw, dict):
            return response_api_error('Invalid request')
        case_id = raw.get('case_id')
        if not isinstance(case_id, int):
            return response_api_error('case_id must be an integer')

        if not cases_exists(case_id):
            return response_api_not_found()
        # Users can only follow cases they can actually open. Without
        # this gate the dashboard would silently start linking to a
        # case the user has no read access to.
        if not ac_fast_check_current_user_has_case_access(
                case_id, [CaseAccessLevel.read_only, CaseAccessLevel.full_access]):
            return response_api_error('No access to this case')

        existing = (
            UserFollowedCase.query
            .filter_by(user_id=iris_current_user.id, case_id=case_id)
            .first()
        )
        if existing is not None:
            return response_api_created({'case_id': case_id, 'followed': True})

        follow = UserFollowedCase(user_id=iris_current_user.id, case_id=case_id)
        db.session.add(follow)
        try:
            db.session.commit()
        except IntegrityError:
            # Race with a concurrent POST from the same user. Same
            # outcome as the existing-row branch above.
            db.session.rollback()
        return response_api_created({'case_id': case_id, 'followed': True})

    def unfollow_case(self, case_id):
        """Remove a case from the current user's followed-cases list.

        Idempotent: unfollowing a case that isn't followed returns 204
        rather than 404 so the SPA can fire-and-forget on toggle clicks
        without first round-tripping the current state.
        """
        UserFollowedCase.query.filter_by(
            user_id=iris_current_user.id, case_id=case_id
        ).delete(synchronize_session=False)
        db.session.commit()
        return response_api_deleted()

    def update_preferences(self):
        """Persist a small dict of UI preferences on the current user.

        Today only `has_mini_sidebar` is accepted — the SPA toggles
        this when the user folds the side bar so the next session
        opens with the same layout. Returns the updated preference
        block so the client can re-seed its in-memory copy without a
        second round-trip.
        """
        user = users_get(iris_current_user.id)
        if user is None:
            return response_api_error('Unknown user')

        raw = request.get_json()
        if not isinstance(raw, dict):
            return response_api_error('Invalid request')

        if 'has_mini_sidebar' in raw:
            if not isinstance(raw['has_mini_sidebar'], bool):
                return response_api_error(
                    'has_mini_sidebar must be a boolean'
                )
            user.has_mini_sidebar = raw['has_mini_sidebar']

        db.session.commit()
        return response_api_success({
            'has_mini_sidebar': bool(user.has_mini_sidebar),
        })


profile_operations = ProfileOperations()
profile_blueprint = Blueprint('profile_rest_v2', __name__, url_prefix='/me')


@profile_blueprint.get('')
@ac_api_requires()
@api_doc(response=UserSchemaForAPIV2, tags=['Profile'],
         summary="Get the caller's profile")
def get_profile():
    return profile_operations.get()


@profile_blueprint.put('')
@ac_api_requires()
@api_doc(response=UserSchemaForAPIV2, tags=['Profile'],
         summary="Update the caller's profile")
def update_profile():
    return profile_operations.update()


@profile_blueprint.post('/api-key/renew')
@ac_api_requires()
@api_doc(response=UserSchemaForAPIV2, tags=['Profile'],
         summary='Renew the API key')
def renew_api_key():
    return profile_operations.renew_api_key()


@profile_blueprint.post('/permissions/refresh')
@ac_api_requires()
@api_doc(response=UserSchemaForAPIV2, tags=['Profile'],
         summary="Refresh the caller's effective permissions")
def refresh_permissions():
    return profile_operations.refresh_permissions()


@profile_blueprint.get('/context')
@ac_api_requires()
@api_doc(tags=['Profile'], summary="Get the caller's SPA context")
def get_context():
    return profile_operations.get_context()


@profile_blueprint.put('/preferences')
@ac_api_requires()
@api_doc(tags=['Profile'], summary="Update the caller's UI preferences")
def update_preferences():
    return profile_operations.update_preferences()


@profile_blueprint.get('/followed-cases')
@ac_api_requires()
@api_doc(response=CaseSchemaForAPIV2, tags=['Profile'],
         summary='List followed cases')
def list_followed_cases():
    return profile_operations.list_followed_cases()


@profile_blueprint.post('/followed-cases')
@ac_api_requires()
@api_doc(response_shape='created', tags=['Profile'], summary='Follow a case')
def follow_case():
    return profile_operations.follow_case()


@profile_blueprint.delete('/followed-cases/<int:case_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['Profile'],
         summary='Unfollow a case')
def unfollow_case(case_id):
    return profile_operations.unfollow_case(case_id)


@profile_blueprint.get('/api-keys')
@ac_api_requires()
@api_doc(response=UserApiKeySchema, tags=['Profile'],
         summary='List the caller\'s named API keys')
def list_api_keys():
    return profile_operations.list_api_keys()


@profile_blueprint.post('/api-keys')
@ac_api_requires()
@api_doc(response=UserApiKeySchema, response_shape='created', tags=['Profile'],
         summary='Mint a new named, scope-restricted API key')
def create_api_key():
    return profile_operations.create_api_key()


@profile_blueprint.delete('/api-keys/<int:key_id>')
@ac_api_requires()
@api_doc(response_shape='deleted', tags=['Profile'],
         summary='Revoke a named API key (idempotent)')
def revoke_api_key(key_id: int):
    return profile_operations.revoke_api_key(key_id)
