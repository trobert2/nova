# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2014 Cloudbase Solutions Srl
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
import unittest

from nova.compute import task_states
from nova.virt.hyperv import snapshotops
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


class SnapshotOpsTestCase(unittest.TestCase):
    """Unit tests for Vmops calls."""
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
        self.pathutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_pathutils')
        self.vmutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                          '.get_vmutils')
        self.vhdutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                           '.get_vhdutils')

        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_vmutils = self.vmutils_patcher.start()
        self.mock_vhdutils = self.vhdutils_patcher.start()

        self._snapshotops = snapshotops.SnapshotOps()
        super(SnapshotOpsTestCase, self).setUp()

    def tearDown(self):
        self.vmutils_patcher.stop()
        self.pathutils_patcher.stop()
        self.vhdutils_patcher.stop()

        super(SnapshotOpsTestCase, self).tearDown()

    @mock.patch('nova.image.glance.get_remote_image_service')
    def test_save_glance_image(self, mock_get_remote_image_service):
        mock_context = mock.MagicMock()
        image_metadata = {"is_public": False,
                          "disk_format": "vhd",
                          "container_format": "bare",
                          "properties": {}}
        glance_image_service = mock.MagicMock()
        fake_path = os.path.join('fake', 'path')
        mock_get_remote_image_service.return_value = (glance_image_service,
                                                      'fake id')
        self._snapshotops._save_glance_image(context=mock_context,
                                             name='fake name',
                                             image_vhd_path=fake_path)
        mock_get_remote_image_service.assert_called_once_with(mock_context,
                                                              'fake name')
        self.mock_pathutils().open.assert_called_with(fake_path, 'rb')
        glance_image_service.update.assert_called_once_with(
            mock_context, 'fake id', image_metadata,
            self.mock_pathutils().open().__enter__())

    @mock.patch('nova.virt.hyperv.snapshotops.SnapshotOps._save_glance_image')
    def _test_snapshot(self, mock_save_glance_image, base_disk_path):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        mock_update = mock.MagicMock()
        fake_src_path = os.path.join('fake', 'path')
        self.mock_pathutils().lookup_root_vhd_path.return_value = fake_src_path
        fake_exp_dir = os.path.join(os.path.join('fake', 'exp'), 'dir')
        self.mock_pathutils().get_export_dir.return_value = fake_exp_dir
        self.mock_vhdutils().get_vhd_parent_path.return_value = base_disk_path
        fake_snapshot_path = os.path.join('fake', 'snapshot')
        self.mock_vmutils().take_vm_snapshot.return_value = fake_snapshot_path

        self._snapshotops.snapshot(context=mock_context,
                                   instance=mock_instance, name='fake_name',
                                   update_task_state=mock_update)

        self.mock_vmutils().take_vm_snapshot.assert_called_once_with(
            mock_instance['name'])
        self.mock_pathutils().lookup_root_vhd_path.assert_called_once_with(
            mock_instance['name'])
        self.mock_vhdutils().get_vhd_parent_path.assert_called_once_with(
            fake_src_path)
        self.mock_pathutils().get_export_dir.assert_called_once_with(
            mock_instance['name'])

        expected = [mock.call(
            fake_src_path, os.path.join(fake_exp_dir,
                                        os.path.basename(fake_src_path)))]
        dest_vhd_path = os.path.join(fake_exp_dir,
                                     os.path.basename(fake_src_path))
        if base_disk_path:
            basename = os.path.basename(base_disk_path)
            base_dest_disk_path = os.path.join(fake_exp_dir, basename)
            expected.append(mock.call(base_disk_path, base_dest_disk_path))
            self.mock_vhdutils().reconnect_parent_vhd.assert_called_once_with(
                dest_vhd_path, base_dest_disk_path)
            self.mock_vhdutils().merge_vhd.assert_called_once_with(
                dest_vhd_path, base_dest_disk_path)
            mock_save_glance_image.assert_called_once_with(
                mock_context, 'fake_name', base_dest_disk_path)
        else:
            mock_save_glance_image.assert_called_once_with(
                mock_context, 'fake_name', dest_vhd_path)
        self.assertEqual(self.mock_pathutils().copyfile.call_args_list,
                         expected)
        expected_update = [
            mock.call(task_state=task_states.IMAGE_PENDING_UPLOAD),
            mock.call(task_state=task_states.IMAGE_UPLOADING,
                      expected_state=task_states.IMAGE_PENDING_UPLOAD)]
        self.assertEqual(mock_update.call_args_list, expected_update)
        self.mock_vmutils().remove_vm_snapshot.assert_called_once_with(
            fake_snapshot_path)
        self.mock_pathutils().rmtree.assert_called_once_with(fake_exp_dir)

    def test_snapshot(self):
        base_disk_path = os.path.join('fake', 'disk')
        self._test_snapshot(base_disk_path=base_disk_path)

    def test_snapshot_no_base_disk(self):
        self._test_snapshot(base_disk_path=None)
