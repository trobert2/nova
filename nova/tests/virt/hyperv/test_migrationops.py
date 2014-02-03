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

from nova import unit
from nova.virt.hyperv import migrationops
from nova.virt.hyperv import vmutils
from oslo.config import cfg


CONF = cfg.CONF
CONF.import_opt('vswitch_name', 'nova.virt.hyperv.vif', 'hyperv')


def get_instance_mock(instance_data):
    instance = mock.MagicMock(spec_set=dict)

    def setitem(key, value):
        instance_data[key] = value

    def getitem(key):
        return instance_data[key]

    instance.__setitem__.side_effect = setitem
    instance.__getitem__.side_effect = getitem
    return instance


class MigrationOpsTestCase(unittest.TestCase):
    """Unit tests for MigrationOps calls."""
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
        self.hostutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_hostutils')
        self.vhdutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                           '.get_vhdutils')

        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_vmutils = self.vmutils_patcher.start()
        self.mock_hostutils = self.hostutils_patcher.start()
        self.mock_vhdutils = self.vhdutils_patcher.start()

        self._migrationops = migrationops.MigrationOps()
        super(MigrationOpsTestCase, self).setUp()

    def tearDown(self):
        self.mock_vhdutils.mock_add_spec(None)
        self.vmutils_patcher.stop()
        self.hostutils_patcher.stop()
        self.pathutils_patcher.stop()
        self.vhdutils_patcher.stop()

        super(MigrationOpsTestCase, self).tearDown()

    def _test_migrate_disk_files(self, host, side_effect):
        fake_dest = os.path.join('fake', 'dest')
        fake_revert_path = os.path.join('fake', 'revert')
        instance_path = os.path.join(os.path.join('fake', 'instance'), 'path')
        self.mock_hostutils().get_local_ips.return_value = [host]
        self.mock_pathutils().get_instance_dir.return_value = instance_path
        get_revert_dir = self.mock_pathutils().get_instance_migr_revert_dir
        get_revert_dir.return_value = fake_revert_path
        self.mock_pathutils().exists.return_value = True
        self.mock_pathutils().rename.side_effect = side_effect
        expected_get_dir = [mock.call('fake name')]
        expected_rename = [mock.call(instance_path, fake_revert_path)]
        if side_effect is Exception:
            self.assertRaises(Exception,
                              self._migrationops._migrate_disk_files,
                              'fake name', ['disk file'], fake_dest)
        else:
            self._migrationops._migrate_disk_files(instance_name='fake name',
                                                   disk_files=['disk file'],
                                                   dest=fake_dest)
            self.mock_hostutils().get_local_ips.assert_called_once_with()
            get_revert_dir.assert_called_with(
                'fake name', remove_dir=True)
            if host == fake_dest:
                fake_dest_path = '%s_tmp' % instance_path
                self.mock_pathutils().exists.assert_called_once_with(
                    fake_dest_path)
                self.mock_pathutils().rmtree.assert_called_once_with(
                    fake_dest_path)
                self.mock_pathutils().makedirs.assert_called_once_with(
                    fake_dest_path)
                expected_rename.append(mock.call(fake_dest_path,
                                                 instance_path))
            else:
                fake_dest_path = instance_path
                expected_get_dir.append(mock.call('fake name', fake_dest,
                                        remove_dir=True))
            self.assertEqual(
                self.mock_pathutils().get_instance_dir.call_args_list,
                expected_get_dir)
            self.mock_pathutils().copy.assert_called_once_with('disk file',
                                                               fake_dest_path)
            self.assertEqual(self.mock_pathutils().rename.call_args_list,
                             expected_rename)

    def test_migrate_disk_files(self):
        self._test_migrate_disk_files(host='different host', side_effect=None)

    def test_migrate_disk_files_same_host(self):
        fake_host = os.path.join('fake', 'dest')
        self._test_migrate_disk_files(host=fake_host, side_effect=None)

    def test_migrate_disk_files_exception(self):
        self._test_migrate_disk_files(host='fake host', side_effect=Exception)

    def test_cleanup_failed_disk_migration(self):
        instance_path = os.path.join(os.path.join('fake', 'instance'), 'path')
        fake_revert_path = os.path.join('fake', 'revert')
        fake_dest = os.path.join('fake', 'dest')
        self.mock_pathutils().exists.return_value = True
        self._migrationops._cleanup_failed_disk_migration(
            instance_path=instance_path, revert_path=fake_revert_path,
            dest_path=fake_dest)
        expected = [mock.call(fake_dest),
                    mock.call(fake_revert_path)]
        self.assertEqual(self.mock_pathutils().exists.call_args_list,
                         expected)
        self.mock_pathutils().rmtree.assert_called_once_with(fake_dest)
        self.mock_pathutils().rename.assert_called_once_with(
            fake_revert_path, instance_path)

    def test_check_target_flavor(self):
        mock_instance = get_instance_mock(self.instance_data)
        self.assertRaises(vmutils.VHDResizeException,
                          self._migrationops._check_target_flavor,
                          mock_instance, {'root_gb': 0})

    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps'
                '._check_target_flavor')
    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps'
                '._migrate_disk_files')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_off')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.destroy')
    def test_migrate_disk_and_power_off(self, mock_destroy, mock_power_off,
                                        mock_migrate_disk_files,
                                        mock_check_target_flavor):
        fake_dest = os.path.join('fake', 'dest')
        mock_instance = get_instance_mock(self.instance_data)
        mock_context = mock.MagicMock()
        self.mock_vmutils().get_vm_storage_paths.return_value = (
            ['fake disk file'], ['volume drive'])
        response = self._migrationops.migrate_disk_and_power_off(
            context=mock_context, instance=mock_instance, dest=fake_dest,
            flavor='fake flavor', network_info='fake net info')
        mock_check_target_flavor.assert_called_once_with(mock_instance,
                                                         'fake flavor')
        mock_power_off.assert_called_once_with(mock_instance)
        self.mock_vmutils().get_vm_storage_paths.assert_called_once_with(
            mock_instance['name'])
        mock_migrate_disk_files.assert_called_once_with(mock_instance['name'],
                                                        ['fake disk file'],
                                                        fake_dest)
        mock_destroy.assert_called_once_with(mock_instance,
                                             destroy_disks=False)
        self.assertEqual(response, "")

    def test_confirm_migration(self):
        mock_instance = get_instance_mock(self.instance_data)
        self._migrationops.confirm_migration(migration='fake migration',
                                             instance=mock_instance,
                                             network_info='fake net info')
        self.mock_pathutils().get_instance_migr_revert_dir.assert_called_with(
            mock_instance['name'], remove_dir=True)

    def test_revert_migration_files(self):
        instance_path = os.path.join(os.path.join('fake', 'instance'), 'path')
        revert_path = os.path.join(os.path.join('fake', 'revert'), 'path')

        self.mock_pathutils().get_instance_dir.return_value = instance_path
        get_revert_dir = self.mock_pathutils().get_instance_migr_revert_dir
        get_revert_dir.return_value = revert_path
        self._migrationops._revert_migration_files(instance_name='fake name')
        self.mock_pathutils().get_instance_dir.assert_called_once_with(
            'fake name', create_dir=False, remove_dir=True)
        self.mock_pathutils().get_instance_migr_revert_dir.assert_called_with(
            'fake name')
        self.mock_pathutils().rename.assert_called_once_with(revert_path,
                                                             instance_path)

    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps'
                '._revert_migration_files')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.ebs_root_in_block_devices')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.create_instance')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_on')
    def _test_finish_revert_migration(self, mock_power_on,
                                      mock_create_instance,
                                      mock_ebs_root_in_block_devices,
                                      mock_revert_migration_files,
                                      in_block_device):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        fake_root = os.path.join('fake', 'root')
        fake_ephemeral_path = os.path.join('fake', 'ephemeral')
        mock_ebs_root_in_block_devices.return_value = in_block_device
        self.mock_pathutils().lookup_root_vhd_path.return_value = fake_root
        lookup_ephemeral = self.mock_pathutils().lookup_ephemeral_vhd_path
        lookup_ephemeral.return_value = fake_ephemeral_path
        self._migrationops.finish_revert_migration(
            context=mock_context, instance=mock_instance,
            network_info='fake net info', block_device_info='fake block info',
            power_on=True)
        mock_revert_migration_files.assert_called_once_with(
            mock_instance['name'])
        mock_ebs_root_in_block_devices.assert_called_once_with(
            'fake block info')
        if not in_block_device:
            self.mock_pathutils().lookup_root_vhd_path.assert_called_with(
                mock_instance['name'])
            fake_root_path = fake_root
        else:
            fake_root_path = None
        lookup_ephemeral.assert_called_with(mock_instance['name'])
        mock_create_instance.assert_called_once_with(mock_instance,
                                                     'fake net info',
                                                     'fake block info',
                                                     fake_root_path,
                                                     fake_ephemeral_path)
        mock_power_on.assert_called_once_with(mock_instance)

    def test_finish_revert_migration(self):
        self._test_finish_revert_migration(in_block_device=True)

    def test_finish_revert_migration_not_in_block_device(self):
        self._test_finish_revert_migration(in_block_device=False)

    def _test_merge_base_vhd(self, side_effect):
        fake_diff_vhd_path = os.path.join(os.path.join('fake', 'diff'),
                                          'path')
        fake_base_vhd_path = os.path.join(os.path.join('fake', 'base'),
                                          'path')
        base_vhd_copy_path = os.path.join(os.path.dirname(fake_diff_vhd_path),
                                          os.path.basename(
                                              fake_base_vhd_path))
        self.mock_pathutils().copyfile.side_effect = side_effect
        self.mock_pathutils().exists.return_value = True
        if side_effect is Exception:
            self.assertRaises(Exception, self._migrationops._merge_base_vhd,
                              fake_diff_vhd_path, fake_base_vhd_path)
            self.mock_pathutils().exists.assert_called_once_with(
                base_vhd_copy_path)
            self.mock_pathutils().remove.assert_called_once_with(
                base_vhd_copy_path)
        else:
            self._migrationops._merge_base_vhd(
                diff_vhd_path=fake_diff_vhd_path,
                base_vhd_path=fake_base_vhd_path)
            self.mock_pathutils().copyfile.assert_called_once_with(
                fake_base_vhd_path, base_vhd_copy_path)
            self.mock_vhdutils().reconnect_parent_vhd.assert_called_once_with(
                fake_diff_vhd_path, base_vhd_copy_path)
            self.mock_vhdutils().merge_vhd.assert_called_once_with(
                fake_diff_vhd_path, base_vhd_copy_path)
            self.mock_pathutils().rename.assert_called_once_with(
                base_vhd_copy_path, fake_diff_vhd_path)

    def test_merge_base_vhd(self):
        self._test_merge_base_vhd(side_effect=None)

    def test_merge_base_vhd_exception(self):
        self._test_merge_base_vhd(side_effect=Exception)

    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps._resize_vhd')
    def _test_check_resize_vhd(self, mock_resize_vhd, new_size):
        fake_vhd_path = os.path.join('fake', 'path')
        if new_size < 1:
            self.assertRaises(vmutils.VHDResizeException,
                              self._migrationops._check_resize_vhd,
                              fake_vhd_path, {'MaxInternalSize': 1}, new_size)
        else:
            self._migrationops._check_resize_vhd(
                vhd_path=fake_vhd_path, vhd_info={'MaxInternalSize': 1},
                new_size=new_size)
            mock_resize_vhd.assert_called_once_with(fake_vhd_path, new_size)

    def test_check_resize_vhd(self):
        self._test_check_resize_vhd(new_size=2)

    def test_check_resize_vhd_exception(self):
        self._test_check_resize_vhd(new_size=0)

    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps._merge_base_vhd')
    def test_resize_vhd(self, mock_merge_base_vhd):
        fake_vhd_path = os.path.join('fake', 'path.vhd')
        fake_base = os.path.join('fake', 'base')
        self.mock_vhdutils().get_vhd_parent_path.return_value = fake_base
        self._migrationops._resize_vhd(vhd_path=fake_vhd_path, new_size=2)
        self.mock_vhdutils().get_vhd_parent_path.assert_called_once_with(
            fake_vhd_path)
        mock_merge_base_vhd.assert_called_once_with(fake_vhd_path, fake_base)
        self.mock_vhdutils().resize_vhd.assert_called_once_with(
            fake_vhd_path, 2)

    @mock.patch('nova.virt.hyperv.imagecache.ImageCache.get_cached_image')
    def test_check_base_disk(self, mock_get_cached_image):
        mock_context = mock.MagicMock()
        fake_diff_vhd_path = os.path.join(os.path.join('fake', 'diff'),
                                          'path')
        fake_src_vhd_path = os.path.join(os.path.join('fake', 'src'),
                                         'path')
        mock_instance = get_instance_mock(self.instance_data)
        fake_base_vhd = os.path.join('fake', 'vhd')
        mock_get_cached_image.return_value = fake_base_vhd
        self._migrationops._check_base_disk(
            context=mock_context, instance=mock_instance,
            diff_vhd_path=fake_diff_vhd_path,
            src_base_disk_path=fake_src_vhd_path)
        mock_get_cached_image.assert_called_once_with(mock_context,
                                                      mock_instance)
        self.mock_vhdutils().reconnect_parent_vhd.assert_called_once_with(
            fake_diff_vhd_path, fake_base_vhd)

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.ebs_root_in_block_devices')
    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps._check_base_disk')
    @mock.patch('nova.virt.hyperv.migrationops.MigrationOps._check_resize_vhd')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.create_ephemeral_vhd')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.create_instance')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_on')
    def _test_finish_migration(self, mock_power_on, mock_create_instance,
                               mock_create_ephemeral_vhd,
                               mock_check_resize_vhd,
                               mock_check_base_disk,
                               mock_ebs_root_in_block_devices,
                               in_block_device, fake_root, ephemeral_path):
        fake_src_base = os.path.join('fake', 'src')
        fake_new_eph_path = os.path.join('fake', 'eph')
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        mock_vhd_info = mock.MagicMock()
        mock_eph_info = mock.MagicMock()
        mock_vhd_info.get.return_value = fake_src_base
        mock_ebs_root_in_block_devices.return_value = in_block_device
        self.mock_pathutils().lookup_root_vhd_path.return_value = fake_root
        self.mock_vhdutils().get_vhd_info.side_effect = [mock_vhd_info,
                                                         mock_eph_info]
        look_up_ephemeral = self.mock_pathutils().lookup_ephemeral_vhd_path
        look_up_ephemeral.return_value = ephemeral_path
        expected_check_resize = []
        expected_get_info = []
        mock_create_ephemeral_vhd.return_value = fake_new_eph_path
        if not in_block_device and fake_root is None:
            self.assertRaises(vmutils.HyperVException,
                              self._migrationops.finish_migration,
                              mock_context, 'fake migration', mock_instance,
                              'fake disk info', 'fake net info',
                              'fake image meta', True, None, True)
        else:
            self._migrationops.finish_migration(context=mock_context,
                                                migration='fake migration',
                                                instance=mock_instance,
                                                disk_info='fake disk info',
                                                network_info='fake net info',
                                                image_meta='fake image meta',
                                                resize_instance=True)
            print mock_instance.mock_calls
            mock_ebs_root_in_block_devices.assert_called_once_with(None)
            if not in_block_device:
                root_vhd_path = fake_root
                self.mock_pathutils().lookup_root_vhd_path.assert_called_with(
                    mock_instance['name'])
                expected_get_info = [mock.call(fake_root)]
                mock_vhd_info.get.assert_called_once_with("ParentPath")
                mock_check_base_disk.assert_called_once_with(mock_context,
                                                             mock_instance,
                                                             fake_root,
                                                             fake_src_base)
                expected_check_resize.append(
                    mock.call(fake_root, mock_vhd_info,
                              mock_instance['root_gb'] * unit.Gi))
            else:
                root_vhd_path = None
            look_up_ephemeral.assert_called_once_with(mock_instance['name'])
            if ephemeral_path is None:
                mock_create_ephemeral_vhd.assert_called_once_with(
                    mock_instance)
                ephemeral_path = fake_new_eph_path
            else:
                expected_get_info.append(mock.call(ephemeral_path))
                expected_check_resize.append(
                    mock.call(ephemeral_path, mock_eph_info,
                              mock_instance.get().__mul__(unit.Gi)))
            self.assertEqual(mock_check_resize_vhd.call_args_list,
                             expected_check_resize)
            self.assertEqual(self.mock_vhdutils().get_vhd_info.call_args_list,
                             expected_get_info)
            mock_create_instance.assert_called_once_with(mock_instance,
                                                         'fake net info',
                                                         None,
                                                         root_vhd_path,
                                                         ephemeral_path)
            mock_power_on.assert_called_once_with(mock_instance)

    def test_finish_migration(self):
        fake_root = os.path.join('fake', 'root')
        fake_eph = os.path.join('fake', 'eph')
        self._test_finish_migration(in_block_device=False,
                                    fake_root=fake_root,
                                    ephemeral_path=fake_eph)

    def test_finish_migration_not_in_block_device(self):
        fake_root = os.path.join('fake', 'root')
        fake_eph = os.path.join('fake', 'eph')
        self._test_finish_migration(in_block_device=False,
                                    fake_root=fake_root,
                                    ephemeral_path=fake_eph)

    def test_finish_migration_no_ephemeral(self):
        fake_root = os.path.join('fake', 'root')
        self._test_finish_migration(in_block_device=False,
                                    fake_root=fake_root,
                                    ephemeral_path=None)

    def test_finish_migration_no_root(self):
        fake_eph = os.path.join('fake', 'eph')
        self._test_finish_migration(in_block_device=False,
                                    fake_root=None,
                                    ephemeral_path=fake_eph)
