#  IRIS Source Code
#  Copyright (C) 2021 - Airbus CyberSecurity (SAS)
#  ir@cyberactionlab.net
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
import logging

from app import app
from app import run_post_init
from app import socket_io

if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.addHandler(gunicorn_logger.handlers)
    app.logger.setLevel(logging.INFO)


if __name__ == "__main__":
    # Importing the app no longer bootstraps the database — in the
    # container that is a separate one-shot step run before gunicorn
    # (`python -m scripts.run_post_init`, see the entrypoint). This dev
    # runner is a single process, so it just does it inline and keeps
    # `python run.py` working against an empty database as before.
    # Under __main__ only: a WSGI server importing this module must not
    # trigger post-init once per worker.
    run_post_init()

    socket_io.run(app, host='127.0.0.1', port=8000, debug=True)
