#  IRIS Source Code
#  Copyright (C) ${current_year} - DFIR-IRIS
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

from sqlalchemy import and_

from app.iris_engine.utils.tracker import track_activity
from app.models.comments import Comments
from app.models.cases import Cases
from app.models.models import Client
from app.models.iocs import Ioc
from app.models.models import IocType
from app.models.models import Notes
from app.models.iocs import Tlp


def search(search_type, search_value):
    track_activity(f'started a global search for {search_value} on {search_type}')

    files = []
    if search_type == 'ioc':
        files = search_iocs(search_value)

    if search_type == 'notes' and search_value:
        files = search_notes(search_value)

    if search_type == 'comments':
        files = search_comments(search_value)
    return files


def search_comments(search_value):
    search_condition = and_()
    comments = Comments.query.filter(
        Comments.comment_text.like(f'%{search_value}%'),
        Cases.client_id == Client.client_id,
        search_condition
    ).with_entities(
        Comments.comment_id,
        Comments.comment_text,
        Cases.name.label('case_name'),
        Client.name.label('customer_name'),
        Cases.case_id
    ).join(
        Comments.case
    ).join(
        Cases.client
    ).order_by(
        Client.name
    ).all()

    return [row._asdict() for row in comments]


def search_notes(search_value):
    search_condition = and_()
    notes = Notes.query.filter(
        Notes.note_content.like(f'%{search_value}%'),
        Cases.client_id == Client.client_id,
        search_condition
    ).with_entities(
        Notes.note_id,
        Notes.note_title,
        Cases.name.label('case_name'),
        Client.name.label('client_name'),
        Cases.case_id
    ).join(
        Notes.case
    ).order_by(
        Client.name
    ).all()

    return [row._asdict() for row in notes]


def search_iocs(search_value):
    search_condition = and_()
    res = Ioc.query.with_entities(
        Ioc.ioc_value.label('ioc_name'),
        Ioc.ioc_description.label('ioc_description'),
        Ioc.ioc_misp,
        IocType.type_name,
        Tlp.tlp_name,
        Tlp.tlp_bscolor,
        Cases.name.label('case_name'),
        Cases.case_id,
        Client.name.label('customer_name')
    ).filter(
        and_(
            Ioc.ioc_value.like(search_value),
            Ioc.case_id == Cases.case_id,
            Client.client_id == Cases.client_id,
            Ioc.ioc_tlp_id == Tlp.tlp_id,
            search_condition
        )
    ).join(Ioc.ioc_type).all()

    return [row._asdict() for row in res]
