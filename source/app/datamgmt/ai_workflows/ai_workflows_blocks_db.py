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


"""Persistence of saved AI workflow blocks."""

from sqlalchemy import or_

from app.models.ai_workflows import AiWorkflowBlock


def ai_workflows_blocks_db_list(user_id=None):
    """Every block, or (with `user_id`) the user's blocks and the shared ones."""
    query = AiWorkflowBlock.query
    if user_id is not None:
        query = query.filter(or_(AiWorkflowBlock.owner_id == user_id, AiWorkflowBlock.is_shared.is_(True)))
    return query.order_by(AiWorkflowBlock.name.asc(), AiWorkflowBlock.id.asc()).all()


def ai_workflows_blocks_db_get(block_id):
    return AiWorkflowBlock.query.filter(AiWorkflowBlock.id == block_id).first()
