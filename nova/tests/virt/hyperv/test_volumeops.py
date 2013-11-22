# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Cloudbase Solutions Srl
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock
import os

from nova import test

from nova.virt.hyperv import volumeops
from nova.virt.hyperv import utilsfactory


class HyperVSMBFSVolumeDriverTestCase(test.NoDBTestCase):
    """Unit tests for the Hyper-V HyperVSMBFSVolumeDriver class."""

    def setUp(self):
        self._vmutils = mock.MagicMock()

        utilsfactory.get_hostutils = mock.MagicMock()
        utilsfactory.get_vmutils = mock.MagicMock()
        utilsfactory.get_vmutils.return_value = self._vmutils
        utilsfactory.get_volumeutils = mock.MagicMock()
        volumeops.VolumeOps = mock.MagicMock()

        self._volumeops = volumeops.HyperVSMBFSVolumeDriver()
        super(HyperVSMBFSVolumeDriverTestCase, self).setUp()

    def _test_attach_volume(self, volume_exists):
        connection_info = mock.MagicMock()
        fake_dict = mock.Mock()
        fake_opts = ([], fake_dict)

        self._volumeops.parse_options = mock.MagicMock()
        self._volumeops.parse_options.return_value = fake_opts
        self._volumeops._ensure_mounted = mock.MagicMock()
        self._volumeops.get_local_disk_path = mock.MagicMock()
        self._volumeops.get_local_disk_path.return_value = 'fake/disk/path'
        os.path.isfile = mock.MagicMock()
        os.path.isfile.return_value = volume_exists

        self._vmutils.get_vm_scsi_controller = mock.MagicMock()
        self._vmutils.attach_ide_drive = mock.MagicMock()

        self._volumeops._get_free_controller_slot = mock.MagicMock()
        self._volumeops._get_free_controller_slot.return_value = 9999

        if not volume_exists:
            self.assertRaises(Exception, self._volumeops.attach_volume,
                              connection_info, 'fake instance')
        else:
            self._volumeops.attach_volume(connection_info, 'fake instance')

            self._volumeops.parse_options.assert_called_with(
                connection_info['data'].get('options'))
            self._volumeops._ensure_mounted.assert_called_with(
                connection_info['data']['export'], fake_opts[1])
            self._volumeops.get_local_disk_path.assert_called_with(
                connection_info)
            os.path.isfile.assert_called_with('fake/disk/path')
            self._vmutils.get_vm_scsi_controller.assert_called_with(
                'fake instance')
            self._volumeops._get_free_controller_slot.assert_called_with(
                self._vmutils.get_vm_scsi_controller())
            self._vmutils.attach_ide_drive.assert_called_with(
                'fake instance', 'fake/disk/path',
                self._vmutils.get_vm_scsi_controller(), 9999)

    def test_attach_volume_volume_exists(self):
        self._test_attach_volume(True)

    def test_attach_volume_volume_does_not_exist(self):
        self._test_attach_volume(False)

    def test_detach_volume(self):
        connection_info = mock.MagicMock()

        self._volumeops.get_local_disk_path = mock.MagicMock()
        self._volumeops.get_local_disk_path.return_value = 'fake/disk/path'

        self._vmutils.detach_vhd_disk = mock.MagicMock()

        self._volumeops.detach_volume(connection_info, 'fake instance')
        self._volumeops.get_local_disk_path.assert_called_with(connection_info)
        self._vmutils.detach_vhd_disk.assert_called_with('fake instance',
                                                         'fake/disk/path')
