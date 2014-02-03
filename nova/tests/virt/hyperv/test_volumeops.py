# vim: tabstop=4 shiftwidth=4 softtabstop=4

#  Copyright 2014 Cloudbase Solutions Srl
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
import unittest

from nova import exception
from nova.virt.hyperv import volumeops
from nova.virt.hyperv import vmutils
from oslo.config import cfg


CONF = cfg.CONF
CONF.import_opt('vswitch_name', 'nova.virt.hyperv.vif', 'hyperv')


def get_instance_mock(instance_data):
    instance = mock.MagicMock()

    def setitem(key, value):
        instance_data[key] = value

    def getitem(key):
        return instance_data[key]

    instance.__setitem__.side_effect = setitem
    instance.__getitem__.side_effect = getitem
    return instance


class VolumeOpsTestCase(unittest.TestCase):
    """Unit tests for VolumeOps calls."""
    _FAKE_NAME = 'fake name'
    _FAKE_USER_ID = 'fake user ID'
    _FAKE_PROJECT_ID = 'fake project ID'
    _FAKE_INSTANCE_DATA = 'fake instance data'
    _FAKE_IMAGE_ID = 'fake image id'
    _FAKE_IMAGE_METADATA = 'fake image data'
    _FAKE_NETWORK_INFO = 'fake network info'
    #TODO(rtingirica): use db_fakes.get_fake_instance_data for this dict:
    instance_data = {'name': _FAKE_NAME,
                     'memory_mb': 1024,
                     'vcpus': 1,
                     'image_ref': _FAKE_IMAGE_ID,
                     'root_gb': 10,
                     'ephemeral_gb': 10,
                     'uuid': _FAKE_IMAGE_ID,
                     'user_id': _FAKE_USER_ID,
                     'project_id': _FAKE_PROJECT_ID}

    def setUp(self):
        self.vmutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                          '.get_vmutils')
        self.hostutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_hostutils')
        self.volumeutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                              '.get_volumeutils')

        self.mock_vmutils = self.vmutils_patcher.start()
        self.mock_hostutils = self.hostutils_patcher.start()
        self.mock_volutils = self.volumeutils_patcher.start()

        self._volumeops = volumeops.VolumeOps()
        super(VolumeOpsTestCase, self).setUp()

    def tearDown(self):
        self.vmutils_patcher.stop()
        self.hostutils_patcher.stop()
        self.volumeutils_patcher.stop()

        super(VolumeOpsTestCase, self).tearDown()

    def test_ebs_root_in_block_devices(self):
        response = self._volumeops.ebs_root_in_block_devices(
            block_device_info='fake info')
        self.mock_volutils().volume_in_mapping.assert_called_once_with(
            self._volumeops._default_root_device, 'fake info')
        self.assertEqual(response, self.mock_volutils().volume_in_mapping())

    @mock.patch('nova.virt.driver.block_device_info_get_mapping')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps.attach_volume')
    def test_attach_volumes(self, mock_attach_volume,
                            mock_block_device_info_get_mapping):
        mapping = [{'connection_info': 'fake connection info'},
                   {'connection_info': 'fake connection info 2'}]
        mock_block_device_info_get_mapping.return_value = mapping
        self._volumeops.attach_volumes(block_device_info='fake info',
                                       instance_name='fake name',
                                       ebs_root=True)
        mock_block_device_info_get_mapping.assert_called_once_with(
            'fake info')
        expected = [mock.call('fake connection info', 'fake name', True),
                    mock.call('fake connection info 2', 'fake name')]
        self.assertEqual(mock_attach_volume.call_args_list, expected)

    @mock.patch('nova.virt.driver.block_device_info_get_mapping')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps._login_storage_target')
    def test_login_storage_targets(self, mock_login_storage_target,
                                   mock_block_device_info_get_mapping):
        mapping = [{'connection_info': 'fake connection info'}]
        mock_block_device_info_get_mapping.return_value = mapping
        self._volumeops.login_storage_targets(block_device_info='fake info')
        mock_block_device_info_get_mapping.assert_called_once_with(
            'fake info')
        mock_login_storage_target.assert_called_once_with(
            'fake connection info')

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '._get_mounted_disk_from_lun')
    def _test_login_storage_target(self, mock_get_mounted_disk_from_lun,
                                   ret_value):
        connection_info = {'data': {'target_lun': 'fake lun',
                                    'target_iqn': 'fake iqn',
                                    'target_portal': 'fake portal'}}
        get_number = self.mock_volutils().get_device_number_for_target
        get_number.return_value = ret_value
        self._volumeops._login_storage_target(connection_info=connection_info)
        get_number.assert_called_once_with('fake iqn', 'fake lun')
        if ret_value is None:
            self.mock_volutils().login_storage_target.assert_called_once_with(
                'fake lun', 'fake iqn', 'fake portal')
            mock_get_mounted_disk_from_lun.assert_called_once_with(
                'fake iqn', 'fake lun', True)

    def test_login_storage_target(self):
        self._test_login_storage_target(ret_value=None)

    def test_login_storage_target_already_logged(self):
        self._test_login_storage_target(ret_value='fake value')

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps._login_storage_target')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '._get_mounted_disk_from_lun')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '._get_free_controller_slot')
    def _test_attach_volume(self, mock_get_free_controller_slot,
                            mock_get_mounted_disk_from_lun,
                            mock_login_storage_target, ebs_root, side_effect):
        connection_info = {'data': {'target_lun': 'fake lun',
                                    'target_iqn': 'fake iqn',
                                    'target_portal': 'fake portal'}}
        fake_mounted_disk_path = os.path.join(os.path.join('fake', 'disk'),
                                              'path')
        mock_get_mounted_disk_from_lun.return_value = fake_mounted_disk_path
        fake_ide_path = os.path.join(os.path.join('fake', 'ide'),
                                     'path')
        self.mock_vmutils().get_vm_ide_controller.return_value = fake_ide_path
        fake_scsi_path = os.path.join(os.path.join('fake', 'scsi'),
                                      'path')
        get_scsi_path = self.mock_vmutils().get_vm_scsi_controller
        get_scsi_path.return_value = fake_scsi_path
        mock_get_free_controller_slot.return_value = 1
        attach = self.mock_vmutils().attach_volume_to_controller
        attach.side_effect = [side_effect]
        if side_effect is Exception:
            self.assertRaises(vmutils.HyperVException,
                              self._volumeops.attach_volume, connection_info,
                              'fake name', ebs_root)
            self.mock_volutils().logout_storage_target.assert_called_with(
                'fake iqn')
        else:
            self._volumeops.attach_volume(connection_info=connection_info,
                                          instance_name='fake name',
                                          ebs_root=ebs_root)
            mock_login_storage_target.assert_called_once_with(connection_info)
            mock_get_mounted_disk_from_lun.assert_called_once_with('fake iqn',
                                                                   'fake lun')
            if ebs_root:
                get_ide_path = self.mock_vmutils().get_vm_ide_controller
                get_ide_path.assert_called_once_with('fake name', 0)
                attach.assert_called_once_with('fake name', fake_ide_path, 0,
                                               fake_mounted_disk_path)
            else:
                self.mock_vmutils().get_vm_scsi_controller.assert_called_with(
                    'fake name')
                mock_get_free_controller_slot.assert_called_once_with(
                    fake_scsi_path)
                attach.assert_called_once_with('fake name', fake_scsi_path, 1,
                                               fake_mounted_disk_path)

    def test_attach_ide_volume(self):
        self._test_attach_volume(ebs_root=True, side_effect=None)

    def test_attach_scsi_volume(self):
        self._test_attach_volume(ebs_root=False, side_effect=None)

    def test_attach_volume_exception(self):
        self._test_attach_volume(ebs_root=False, side_effect=Exception)

    def test_get_free_controller_slot(self):
        fake_path = os.path.join('fake', 'path')
        response = self._volumeops._get_free_controller_slot(
            scsi_controller_path=fake_path)
        self.mock_vmutils().get_attached_disks_count.assert_called_once_with(
            fake_path)
        self.assertEqual(response,
                         self.mock_vmutils().get_attached_disks_count())

    @mock.patch('nova.virt.driver.block_device_info_get_mapping')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps.detach_volume')
    def test_detach_volumes(self, mock_detach_volume,
                            mock_block_device_info_get_mapping):
        mock_block_device_info_get_mapping.return_value = [
            {'connection_info': 'fake connection info'}]
        self._volumeops.detach_volumes(block_device_info='fake info',
                                       instance_name='fake name')
        mock_block_device_info_get_mapping.assert_called_once_with(
            'fake info')
        mock_detach_volume.assert_called_once_with('fake connection info',
                                                   'fake name')

    def test_logout_storage_target(self):
        self._volumeops.logout_storage_target(target_iqn='fake iqn')
        self.mock_volutils().logout_storage_target.assert_called_once_with(
            'fake iqn')

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '._get_mounted_disk_from_lun')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps.logout_storage_target')
    def test_detach_volume(self, mock_logout_storage_target,
                           mock_get_mounted_disk_from_lun):
        connection_info = {'data': {'target_lun': 'fake lun',
                                    'target_iqn': 'fake iqn',
                                    'target_portal': 'fake portal'}}
        self._volumeops.detach_volume(connection_info=connection_info,
                                      instance_name='fake name')
        mock_get_mounted_disk_from_lun.assert_called_once_with('fake iqn',
                                                               'fake lun')
        self.mock_vmutils().detach_vm_disk.assert_called_once_with(
            'fake name', mock_get_mounted_disk_from_lun(), )
        mock_logout_storage_target.assert_called_once_with('fake iqn')

    def test_get_volume_connector(self):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_volutils().get_iscsi_initiator.return_value = 'fake init'
        expected = {'ip': CONF.my_ip,
                    'host': CONF.host,
                    'initiator': 'fake init'}
        response = self._volumeops.get_volume_connector(
            instance=mock_instance)
        self.mock_volutils().get_iscsi_initiator.assert_called_once_with()
        self.assertEqual(response, expected)

    def _test_get_mounted_disk_from_lun(self, device_number, disk_path):
        get_device_no = self.mock_volutils().get_device_number_for_target
        disk_by_number = self.mock_vmutils().get_mounted_disk_by_drive_number
        get_device_no.return_value = device_number
        CONF.set_override('volume_attach_retry_interval', 1, 'hyperv')
        disk_by_number.return_value = disk_path
        if device_number is None or disk_path is None:
            self.assertRaises(exception.NotFound,
                              self._volumeops._get_mounted_disk_from_lun,
                              'fake iqn', 'fake lun', False)
        else:
            response = self._volumeops._get_mounted_disk_from_lun(
                target_iqn='fake iqn', target_lun='fake lun',
                wait_for_device=False)
            get_device_no.assert_called_with('fake iqn', 'fake lun')
            disk_by_number.assert_called_once_with(device_number)
            self.assertEqual(response, disk_path)

    def test_get_mounted_disk_from_lun(self):
        fake_path = os.path.join('fake', 'path')
        self._test_get_mounted_disk_from_lun(device_number=1,
                                             disk_path=fake_path)

    def test_get_mounted_disk_from_lun_no_number(self):
        fake_path = os.path.join('fake', 'path')
        self._test_get_mounted_disk_from_lun(device_number=None,
                                             disk_path=fake_path)

    def test_get_mounted_disk_from_lun_no_path(self):
        self._test_get_mounted_disk_from_lun(device_number=1, disk_path=None)

    def test_disconnect_volume(self):
        fake_path = os.path.join('fake', 'path')
        self._volumeops.disconnect_volume(physical_drive_path=fake_path)
        get_session_id = self.mock_volutils().get_session_id_from_mounted_disk
        get_session_id.assert_called_once_with(fake_path)
        self.mock_volutils().execute_log_out.assert_called_once_with(
            get_session_id())

    def test_get_target_from_disk_path(self):
        fake_path = os.path.join('fake', 'path')
        response = self._volumeops.get_target_from_disk_path(
            physical_drive_path=fake_path)
        self.mock_volutils().get_target_from_disk_path.assert_called_with(
            fake_path)
        self.assertEqual(response,
                         self.mock_volutils().get_target_from_disk_path())
