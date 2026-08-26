#  IRIS Source Code
#  DFIR IRIS
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

import datetime
import random
import string

from app import app
from app import bc
from app.business.customers import customers_get_by_name, customers_create
from app.datamgmt.case.case_db import case_db_save
from app.db import db
from app.blueprints.iris_user import iris_current_user
from app.datamgmt.manage.manage_groups_db import add_case_access_to_group
from app.datamgmt.manage.manage_users_db import add_user_to_customer
from app.datamgmt.manage.manage_users_db import add_user_to_group
from app.datamgmt.manage.manage_users_db import add_user_to_organisation
from app.datamgmt.manage.manage_users_db import user_exists
from app.iris_engine.access_control.utils import ac_add_users_multi_effective_access
from app.iris_engine.demo_policy import coerce_user_ids
from app.iris_engine.demo_policy import is_protected_demo_group
from app.iris_engine.demo_policy import is_protected_demo_user
from app.iris_engine.demo_policy import protected_demo_logins
from app.iris_engine.demo_populate import demo_actor
from app.iris_engine.demo_populate import populate_demo_case
from app.iris_engine.demo_scenarios import scenario_for_index
from app.models.cases import Cases
from app.models.errors import ObjectNotFoundError
from app.models.customers import Client
from app.models.authorization import CaseAccessLevel
from app.models.authorization import User

log = app.logger


# Per-file upload ceiling. Started life as a datastore-only guard and
# now applies to every upload surface (datastore, war-room datastore
# and chat attachments, avatars, report templates, asset-type icons,
# case and managed-asset imports). `MAX_CONTENT_LENGTH` in
# `app.configuration` caps the whole request on top of this.
DEMO_MODE_UPLOAD_MAX_BYTES = 10 * 1024

# The account that owns a demo instance. Everything an operator needs
# to keep the demo running (server settings, backups, SMTP probe) stays
# reachable for this user and nobody else — see
# `demo_mode_restricts_server_settings`.
DEMO_MODE_OWNER_USER_ID = 1


def is_demo_mode_enabled():
    return app.config.get('DEMO_MODE_ENABLED') == 'True'


def demo_mode_blocks_password_change():
    """True when demo mode forbids setting a password — for any account.

    The demo publishes its credentials on the landing page, so a
    visitor changing *any* password (their own, another account's, via
    the admin user editor) locks the next visitor out of that account.
    Unlike `protect_demo_mode_user` this is not scoped to the seeded
    accounts: an account created during a demo session is just as
    shared as a seeded one.
    """
    return is_demo_mode_enabled()


def demo_mode_blocks_mfa():
    """True when demo mode forbids MFA entirely.

    Demo accounts are shared, so a second factor bound to one visitor's
    authenticator app locks out everyone else. Demo mode therefore
    forces the server-wide policy off (`enforce_mfa` is ignored, see
    `app.business.auth`), refuses enrolment, and refuses admin resets —
    there is nothing to reset.
    """
    return is_demo_mode_enabled()


def demo_mode_restricts_server_settings():
    """True when the *caller* must not reach the server-settings surface.

    Server settings carry SMTP credentials, DSNs, the chatbot API key
    and the backup trigger. On a demo instance every visitor is an
    admin, so `server_administrator` alone is not a meaningful gate;
    the surface is narrowed to `DEMO_MODE_OWNER_USER_ID`.
    """
    if not is_demo_mode_enabled():
        return False
    return getattr(iris_current_user, 'id', None) != DEMO_MODE_OWNER_USER_ID


def demo_mode_over_upload_cap(file_storage):
    """Return True when demo mode is on and the pending upload exceeds
    `DEMO_MODE_UPLOAD_MAX_BYTES` (10 KB).

    The size is read from the underlying stream by seeking to end and
    then rewinding. `FileStorage.content_length` is only populated when
    the client sent a per-part Content-Length header (rare), so relying
    on it would let large uploads slip past the cap.
    """
    if not is_demo_mode_enabled():
        return False
    if file_storage is None:
        return False
    stream = file_storage.stream
    try:
        stream.seek(0, 2)
        size = stream.tell()
    finally:
        stream.seek(0)
    return size > DEMO_MODE_UPLOAD_MAX_BYTES


def demo_mode_upload_cap_message():
    """Single wording for the over-cap rejection, used by every upload route."""
    return (f'Uploads are limited to {DEMO_MODE_UPLOAD_MAX_BYTES // 1024} KB '
            f'in demo mode')


# Wording for the two refusals below. Every route that turns a
# `protect_demo_mode_*` verdict into a response uses these so a visitor
# poking at the Access Control page gets the same explanation wherever
# they hit the wall.
DEMO_MODE_PROTECTED_USER_MESSAGE = (
    'This account is part of the demo dataset and cannot be modified in demo mode'
)
DEMO_MODE_PROTECTED_GROUP_MESSAGE = (
    'This group is part of the demo dataset and cannot be modified in demo mode'
)


def demo_mode_protected_logins():
    """Logins demo mode freezes — the seeded accounts and the admin."""
    return protected_demo_logins(app.config.get('DEMO_USERS_COUNT', 10),
                                 app.config.get('DEMO_ADM_COUNT', 4),
                                 app.config.get('IRIS_ADM_USERNAME'))


def protect_demo_mode_user(user):
    """True when `user` is off-limits to the caller in demo mode.

    Config and current-user plumbing around `is_protected_demo_user`,
    where the rule itself lives and is unit tested.
    """
    if not is_demo_mode_enabled():
        return False

    # `getattr` rather than a plain attribute read: the serialisers call
    # this outside a request context too, where `iris_current_user`
    # proxies to None.
    caller_id = getattr(iris_current_user, 'id', None)

    return is_protected_demo_user(user.id, user.user, caller_id,
                                  DEMO_MODE_OWNER_USER_ID,
                                  demo_mode_protected_logins())


def protect_demo_mode_user_id(user_id):
    """`protect_demo_mode_user` for routes that only hold an id.

    An unknown id is *not* protected — the caller gets the route's own
    404 rather than a misleading 403.
    """
    if not is_demo_mode_enabled():
        return False

    user = User.query.filter(User.id == user_id).first()
    if user is None:
        return False

    return protect_demo_mode_user(user)


def protect_demo_mode_users(user_ids):
    """True when any id in `user_ids` names a protected demo account.

    Used by the group-membership routes: those edit a group but the
    thing they actually change is the *members'* effective permissions,
    so a demo account must not be added to or removed from any group —
    including a brand-new group the visitor just created and granted
    `server_administrator`.
    """
    if not is_demo_mode_enabled():
        return False

    ids = coerce_user_ids(user_ids)
    if not ids:
        return False

    return any(protect_demo_mode_user(user)
               for user in User.query.filter(User.id.in_(ids)).all())


def protect_demo_mode_group(group):
    """True when `group` is off-limits to the caller in demo mode."""
    if not is_demo_mode_enabled():
        return False

    return is_protected_demo_group(group.group_id,
                                   getattr(iris_current_user, 'id', None),
                                   DEMO_MODE_OWNER_USER_ID)


def gen_demo_admins(count, seed_adm):
    random.seed(seed_adm, version=2)
    for i in range(1, count):
        yield f'Adm {i}',\
              f'adm_{i}', \
              ''.join(random.choices(string.printable[:-6], k=16)), \
              ''.join(random.choices(string.ascii_letters, k=64))


def gen_demo_users(count, seed_user):
    random.seed(seed_user, version=2)
    for i in range(1, count):
        yield f'User Std {i}',\
              f'user_std_{i}', \
              ''.join(random.choices(string.printable[:-6], k=16)), \
              ''.join(random.choices(string.ascii_letters, k=64))


def create_demo_users(def_org, gadm, ganalystes, users_count, seed_user, adm_count, seed_adm):
    users = {
        'admins': [],
        'users': [],
        'gadm': gadm,
        'ganalystes': ganalystes
    }

    for name, username, pwd, api_key in gen_demo_users(users_count, seed_user):

        # Create default users
        user = user_exists(username, f'{username}@iris.local')
        if not user:
            password = bc.generate_password_hash(pwd.encode('utf-8')).decode('utf-8')
            user = User(
                user=username,
                password=password,
                email=f'{username}@iris.local',
                name=name,
                active=True)

            user.api_key = api_key
            db.session.add(user)
            db.session.commit()
            add_user_to_group(user_id=user.id, group_id=ganalystes.group_id)
            add_user_to_organisation(user_id=user.id, org_id=def_org.org_id)
            db.session.commit()
            log.info(f'Created demo user: {user.user} -  {pwd}')

        users['users'].append(user)

    for name, username, pwd, api_key in gen_demo_admins(adm_count, seed_adm):
        user = user_exists(username, f'{username}@iris.local')
        if not user:
            password = bc.generate_password_hash(pwd.encode('utf-8')).decode('utf-8')
            user = User(
                user=username,
                password=password,
                email=f'{username}@iris.local',
                name=name,
                active=True)

            user.api_key = api_key
            db.session.add(user)
            db.session.commit()
            add_user_to_group(user_id=user.id, group_id=gadm.group_id)
            add_user_to_organisation(user_id=user.id, org_id=def_org.org_id)
            db.session.commit()
            log.info(f'Created demo admin: {user.user} - {pwd}')

        users['admins'].append(user)

    return users


def safe_create_customer(name, description):
    try:
        return customers_get_by_name(name)
    except ObjectNotFoundError:
        customer = Client(name=name, description=description)
        customers_create(customer)
        return customer


def demo_case_exists(name, soc_id):
    return db.session.query(Cases).filter(Cases.name.like(f'%{name}'),
                                          Cases.soc_id == soc_id).first()


def _normalise_case(case, name, soc_id, client_id, owner):
    """Undo `Cases.__init__` assigning 1-tuples to scalar columns.

    `Cases.__init__` has trailing commas on `name`, `soc_id`, `client_id`
    and `state_id`, so those attributes are tuples until the first flush
    coerces them. It also overrides `user_id` with `iris_current_user`,
    which under demo seeding is the seeding admin rather than the intended
    case owner. Set all of them explicitly rather than relying on either
    behaviour.
    """
    case.name = name
    case.soc_id = soc_id
    case.client_id = client_id
    case.state_id = None
    case.user_id = owner.id
    case.owner_id = owner.id


def _case_base_time(case_index):
    """Deterministic incident start for a case, spread over recent weeks.

    Demo data has to look recent — a fixed 2024 timestamp makes every case
    read as stale the moment the demo is a few months old. Deriving the
    offset from the case index keeps it stable across restarts so the
    idempotent seeding path doesn't shuffle timestamps on every boot.
    """
    days_ago = 2 + (case_index * 3) % 40
    hour = 7 + (case_index * 5) % 11
    start = datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)
    return start.replace(hour=hour, minute=(case_index * 13) % 60,
                         second=0, microsecond=0)


def _ensure_demo_case(name, soc_id, description, owner, client_id):
    """Fetch or create a demo case. Returns ``(case, created)``."""
    case = demo_case_exists(name, soc_id)
    if case is not None:
        return case, False

    case = Cases(name=name, description=description, soc_id=soc_id,
                 user=owner, client_id=client_id)
    _normalise_case(case, name, soc_id, client_id, owner)

    case_db_save(case)
    db.session.commit()

    return case, True


def _link_users_to_customers(users_data, clients):
    """Give every demo account access to every demo customer.

    Alert visibility is gated on `UserClient` rows, not on case ACLs. Without
    them `get_user_clients_id` returns an empty list and the alerts page is
    blank for every non-admin demo account — the alerts exist, but nobody
    except the administrator can see any of them.
    """
    for user in list(users_data['users']) + list(users_data['admins']):
        for client_id in clients:
            add_user_to_customer(user.id, client_id)


def _populate_demo_cases(cases, users_data):
    """Populate every demo case with its scenario's data.

    Called for pre-existing cases as well as freshly created ones, so a demo
    instance whose seeding died partway through a previous boot — historically
    the common outcome, since the seeder crashed on the first IOC — gets
    filled in on the next restart instead of staying permanently empty.
    """
    admins = list(users_data['admins'])
    analysts = list(users_data['users'])
    if not admins:
        log.warning('No demo admins available, skipping demo case population')
        return

    with demo_actor(app, admins[0]):
        for case_index, case in cases:
            scenario = scenario_for_index(case_index)
            owner = admins[case_index % len(admins)]
            try:
                populate_demo_case(case, scenario, owner, analysts,
                                   _case_base_time(case_index))
            except Exception as exc:
                # One bad case must not take the whole instance down: post_init
                # re-raises, and a failure here would leave IRIS refusing to
                # boot with "Post init failed".
                db.session.rollback()
                log.error(f'Could not populate demo case {case.case_id}: {exc}',
                          exc_info=True)


def create_demo_cases(users_data: dict = None, cases_count: int = 0, clients_count: int = 0):

    clients = []
    for client_index in range(0, clients_count):
        client = safe_create_customer(f'Client {client_index}', f'Description for client {client_index}')
        clients.append(client.client_id)

    if not clients or not users_data.get('users') or not users_data.get('admins'):
        log.warning('No demo customers or users available, skipping demo case creation')
        return

    _link_users_to_customers(users_data, clients)

    unrestricted = []
    for case_index in range(0, cases_count):
        case, created = _ensure_demo_case(
            name=f'Unrestricted Case {case_index}',
            soc_id=f'SOC-{case_index}',
            description='This is a demonstration of an unrestricted case',
            owner=users_data['users'][case_index % len(users_data['users'])],
            client_id=clients[case_index % len(clients)]
        )
        unrestricted.append((case_index, case))
        log.info(f'{"Added" if created else "Found"} unrestricted case {case.name}')

    cases_list = [case.case_id for _, case in unrestricted]

    log.info('Setting permissions for unrestricted cases')
    add_case_access_to_group(group=users_data['ganalystes'],
                             cases_list=cases_list,
                             access_level=CaseAccessLevel.full_access.value)

    add_case_access_to_group(group=users_data['gadm'],
                             cases_list=cases_list,
                             access_level=CaseAccessLevel.full_access.value)

    ac_add_users_multi_effective_access(users_list=[u.id for u in users_data['users']],
                                        cases_list=cases_list,
                                        access_level=CaseAccessLevel.full_access.value)

    ac_add_users_multi_effective_access(users_list=[u.id for u in users_data['admins']],
                                        cases_list=cases_list,
                                        access_level=CaseAccessLevel.full_access.value)

    restricted = []
    for case_index in range(0, int(cases_count / 2)):
        case, created = _ensure_demo_case(
            name=f'Restricted Case {case_index}',
            soc_id=f'SOC-RSTRCT-{case_index}',
            description="This is a demonstration of a restricted case that shouldn't be visible to analyst",
            owner=users_data['admins'][case_index % len(users_data['admins'])],
            client_id=clients[case_index % len(clients)]
        )
        # Offset the scenario so restricted cases don't mirror the unrestricted
        # ones one-for-one.
        restricted.append((case_index + 2, case))
        log.info(f'{"Added" if created else "Found"} restricted case {case.name}')

    cases_list = [case.case_id for _, case in restricted]

    add_case_access_to_group(group=users_data['ganalystes'],
                             cases_list=cases_list,
                             access_level=CaseAccessLevel.deny_all.value)

    ac_add_users_multi_effective_access(users_list=[u.id for u in users_data['users']],
                                        cases_list=cases_list,
                                        access_level=CaseAccessLevel.deny_all.value)

    add_case_access_to_group(group=users_data['gadm'],
                             cases_list=cases_list,
                             access_level=CaseAccessLevel.full_access.value)

    ac_add_users_multi_effective_access(users_list=[u.id for u in users_data['admins']],
                                        cases_list=cases_list,
                                        access_level=CaseAccessLevel.full_access.value)

    _populate_demo_cases(unrestricted + restricted, users_data)

    log.info('Demo data created successfully')
