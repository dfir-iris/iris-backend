#  IRIS Source Code
#  Copyright (C) 2026 - DFIR-IRIS
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

"""CVSS vector parsing, base score computation and severity bands.

Base scores are computed for v2 and v3.x vectors. CVSS v4 scoring relies
on the MacroVector lookup tables of the specification, so a v4 vector is
only validated: its score has to be supplied with it.
"""

import math
import re

from app.models.errors import BusinessProcessingError

_V2_RE = re.compile(
    r'^(?:\(?)AV:[LAN]/AC:[HML]/Au:[MSN]/C:[NPC]/I:[NPC]/A:[NPC](?:/[A-Za-z]+:[A-Z]{1,3}(?:\.[0-9])?)*(?:\)?)$'
)
_V3_RE = re.compile(
    r'^CVSS:3\.[01]/AV:[NALP]/AC:[LH]/PR:[NLH]/UI:[NR]/S:[UC]/C:[NLH]/I:[NLH]/A:[NLH]'
    r'(?:/[A-Z]{1,3}:[A-Z])*$'
)
_V4_RE = re.compile(
    r'^CVSS:4\.0/AV:[NALP]/AC:[LH]/AT:[NP]/PR:[NLH]/UI:[NPA]/VC:[HLN]/VI:[HLN]/VA:[HLN]'
    r'/SC:[HLN]/SI:[HLN]/SA:[HLN](?:/[A-Z]{1,3}:[A-Z])*$'
)

_V3_WEIGHTS = {
    'AV': {'N': 0.85, 'A': 0.62, 'L': 0.55, 'P': 0.2},
    'AC': {'L': 0.77, 'H': 0.44},
    'UI': {'N': 0.85, 'R': 0.62},
    'CIA': {'H': 0.56, 'L': 0.22, 'N': 0.0},
}
_V3_PR = {
    'U': {'N': 0.85, 'L': 0.62, 'H': 0.27},
    'C': {'N': 0.85, 'L': 0.68, 'H': 0.5},
}
_V2_WEIGHTS = {
    'AV': {'L': 0.395, 'A': 0.646, 'N': 1.0},
    'AC': {'H': 0.35, 'M': 0.61, 'L': 0.71},
    'Au': {'M': 0.45, 'S': 0.56, 'N': 0.704},
    'CIA': {'N': 0.0, 'P': 0.275, 'C': 0.660},
}


def _metrics(vector):
    metrics = {}
    for part in vector.split('/'):
        if ':' not in part:
            continue
        key, value = part.split(':', 1)
        metrics.setdefault(key, value)
    return metrics


def vulnerabilities_cvss_parse(vector):
    """`(version, normalised vector)` of a CVSS vector, or raise."""
    if not isinstance(vector, str):
        raise BusinessProcessingError('cvss_vector must be a string')
    vector = vector.strip()
    if _V4_RE.fullmatch(vector):
        return '4.0', vector
    if _V3_RE.fullmatch(vector):
        return vector[5:8], vector
    if _V2_RE.fullmatch(vector):
        return '2.0', vector.strip('()')
    raise BusinessProcessingError('cvss_vector is not a valid CVSS v2, v3.0, v3.1 or v4.0 base vector')


def _roundup_v31(value):
    integer = int(round(value * 100000))
    if integer % 10000 == 0:
        return integer / 100000.0
    return (math.floor(integer / 10000) + 1) / 10.0


def _roundup_v30(value):
    return math.ceil(value * 10) / 10.0


def _score_v3(version, metrics):
    scope_changed = metrics['S'] == 'C'
    iss = 1 - ((1 - _V3_WEIGHTS['CIA'][metrics['C']])
               * (1 - _V3_WEIGHTS['CIA'][metrics['I']])
               * (1 - _V3_WEIGHTS['CIA'][metrics['A']]))
    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss
    exploitability = (8.22 * _V3_WEIGHTS['AV'][metrics['AV']] * _V3_WEIGHTS['AC'][metrics['AC']]
                      * _V3_PR[metrics['S']][metrics['PR']] * _V3_WEIGHTS['UI'][metrics['UI']])
    if impact <= 0:
        return 0.0
    roundup = _roundup_v31 if version == '3.1' else _roundup_v30
    if scope_changed:
        return roundup(min(1.08 * (impact + exploitability), 10))
    return roundup(min(impact + exploitability, 10))


def _score_v2(metrics):
    impact = 10.41 * (1 - ((1 - _V2_WEIGHTS['CIA'][metrics['C']])
                           * (1 - _V2_WEIGHTS['CIA'][metrics['I']])
                           * (1 - _V2_WEIGHTS['CIA'][metrics['A']])))
    exploitability = (20 * _V2_WEIGHTS['AV'][metrics['AV']] * _V2_WEIGHTS['AC'][metrics['AC']]
                      * _V2_WEIGHTS['Au'][metrics['Au']])
    factor = 0 if impact == 0 else 1.176
    score = ((0.6 * impact) + (0.4 * exploitability) - 1.5) * factor
    return round(max(score, 0.0), 1)


def vulnerabilities_cvss_base_score(version, vector):
    """Base score of a parsed vector, or None when it cannot be computed (v4)."""
    metrics = _metrics(vector)
    if version in ('3.0', '3.1'):
        return _score_v3(version, metrics)
    if version == '2.0':
        return _score_v2(metrics)
    return None


def vulnerabilities_cvss_severity(score, version=None):
    """Qualitative severity band of a score (NVD bands; v2 has no critical)."""
    if score is None:
        return 'unknown'
    if version == '2.0':
        if score >= 7.0:
            return 'high'
        return 'medium' if score >= 4.0 else 'low'
    if score >= 9.0:
        return 'critical'
    if score >= 7.0:
        return 'high'
    if score >= 4.0:
        return 'medium'
    if score > 0:
        return 'low'
    return 'none'
