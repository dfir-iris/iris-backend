#  IRIS Source Code
#  Copyright (C) 2023 - DFIR-IRIS
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

import subprocess


class Docker:

    def __init__(self, docker_compose_path, docker_compose_file, project_name=None):
        self._docker_compose_path = docker_compose_path
        self._docker_compose_file = docker_compose_file
        self._project_name = project_name

    def _compose(self, *arguments):
        command = ['docker', 'compose', '-f', self._docker_compose_file]
        if self._project_name:
            command = command + ['-p', self._project_name]
        return command + list(arguments)

    def compose_up(self, service=None):
        command = self._compose('up', '--detach', '--wait')
        if service:
            command = command + [service]
        subprocess.check_call(command, cwd=self._docker_compose_path)

    def compose_down(self):
        subprocess.check_call(self._compose('down'), cwd=self._docker_compose_path)

    def extract_logs(self, service):
        return subprocess.check_output(self._compose('logs', '--no-color', service),
                                       cwd=self._docker_compose_path, universal_newlines=True)

    @staticmethod
    def exec(container, stdin, command):
        subprocess.check_call(['docker', 'exec', '--interactive', container] + command, stdin=stdin)

    @staticmethod
    def volume_rm(volume_name):
        subprocess.check_call(['docker', 'volume', 'rm', volume_name])
