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
from nova import unit
from nova.openstack.common import processutils
from nova.virt.hyperv import constants
from nova.virt.hyperv import vmops
from nova.virt.hyperv import vmutils
from nova.virt.hyperv import vhdutilsv2
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


class VmopsTestCase(unittest.TestCase):
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
        self.hostutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_hostutils')
        self.networkutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                               '.get_networkutils')
        self.volumeutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                              '.get_volumeutils')
        self.vhdutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                           '.get_vhdutils')

        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_vmutils = self.vmutils_patcher.start()
        self.mock_hostutils = self.hostutils_patcher.start()
        self.mock_networkutils = self.networkutils_patcher.start()
        self.mock_volutils = self.volumeutils_patcher.start()
        self.mock_vhdutils = self.vhdutils_patcher.start()

        self._vmops = vmops.VMOps()
        super(VmopsTestCase, self).setUp()

    def tearDown(self):
        self.mock_vhdutils.mock_add_spec(None)
        self.vmutils_patcher.stop()
        self.hostutils_patcher.stop()
        self.networkutils_patcher.stop()
        self.pathutils_patcher.stop()
        self.volumeutils_patcher.stop()
        self.vhdutils_patcher.stop()

        super(VmopsTestCase, self).tearDown()

    @mock.patch('nova.openstack.common.importutils.import_object')
    def _test_load_vif_driver_class(self, mock_import_object,
                                    ret_val):
        mock_import_object.side_effect = [ret_val]
        if ret_val is KeyError:
            self.assertRaises(TypeError, self._vmops._load_vif_driver_class)
        else:
            self._vmops._load_vif_driver_class()
            mock_import_object.assert_called_once_with(
                self._vmops._vif_driver_class_map[CONF.network_api_class])
            self.assertEqual(self._vmops._vif_driver, ret_val)

    def test_load_vif_driver_class(self):
        mock_class = mock.MagicMock()
        self._test_load_vif_driver_class(ret_val=mock_class)

    def test_load_vif_driver_class_error(self):
        self._test_load_vif_driver_class(ret_val=KeyError)

    def test_list_instances(self):
        fake_instance = mock.MagicMock()
        self.mock_vmutils().list_instances.return_value = fake_instance
        response = self._vmops.list_instances()
        self.mock_vmutils().list_instances.assert_called_once_with()
        self.assertEqual(response, fake_instance)

    def _test_get_info(self, vm_exists):
        mock_instance = get_instance_mock(self.instance_data)
        mock_info = mock.MagicMock(spec_set=dict)
        fake_info = {'EnabledState': 2,
                     'MemoryUsage': 'fake memory usage',
                     'NumberOfProcessors': 'fake no of cpus',
                     'UpTime': 'fake time'}

        def getitem(key):
            return fake_info[key]
        mock_info.__getitem__.side_effect = getitem
        expected = {'state': constants.HYPERV_POWER_STATE[2],
                    'max_mem': 'fake memory usage',
                    'mem': 'fake memory usage',
                    'num_cpu': 'fake no of cpus',
                    'cpu_time': 'fake time'}

        self.mock_vmutils().vm_exists.return_value = vm_exists
        self.mock_vmutils().get_vm_summary_info.return_value = mock_info

        if not vm_exists:
            self.assertRaises(exception.InstanceNotFound,
                              self._vmops.get_info, mock_instance)
        else:
            response = self._vmops.get_info(mock_instance)
            self.mock_vmutils().vm_exists.assert_called_once_with(
                self._FAKE_NAME)
            self.mock_vmutils().get_vm_summary_info.assert_called_once_with(
                self._FAKE_NAME)
            self.assertEqual(response, expected)

    def test_get_info(self):
        self._test_get_info(vm_exists=True)

    def test_get_info_exception(self):
        self._test_get_info(vm_exists=False)

    @mock.patch('nova.virt.hyperv.imagecache.ImageCache.get_cached_image')
    def _test_create_root_vhd(self, mock_get_cached_image, use_cow_images,
                              instance_type, vhd_size):
        mock_instance = get_instance_mock(self.instance_data)
        mock_context = mock.MagicMock()
        fake_vhd_path = os.path.join(os.path.join('fake', 'vhd'), 'path.ext')
        mock_get_cached_image.return_value = fake_vhd_path
        fake_root_path = os.path.join(os.path.join('fake', 'root'), 'path.ext')
        self.mock_pathutils().get_root_vhd_path.return_value = fake_root_path
        CONF.set_override('use_cow_images', use_cow_images)
        self.mock_vhdutils.mock_add_spec(instance_type)
        self.mock_vhdutils().get_vhd_info.return_value = {'MaxInternalSize':
                                                          vhd_size}
        self.mock_pathutils().exists.return_value = True
        root_vhd_internal_size = mock_instance['root_gb'] * unit.Gi
        self.mock_vhdutils.get_internal_vhd_size_by_file_size.return_value = (
            root_vhd_internal_size)
        if root_vhd_internal_size < vhd_size and not use_cow_images:
            self.assertRaises(vmutils.HyperVException,
                              self._vmops._create_root_vhd, mock_context,
                              mock_instance)
            self.mock_pathutils().exists.assert_called_once_with(
                fake_root_path)
            self.mock_pathutils().remove.assert_called_once_with(
                fake_root_path)
        else:
            response = self._vmops._create_root_vhd(context=mock_context,
                                                    instance=mock_instance)
            self.mock_pathutils().get_root_vhd_path.assert_called_with(
                mock_instance['name'], 'ext')
            differencing_vhd = self.mock_vhdutils().create_differencing_vhd
            get_size = self.mock_vhdutils().get_internal_vhd_size_by_file_size
            if use_cow_images:
                differencing_vhd.assert_called_with(fake_root_path,
                                                    fake_vhd_path)
            else:
                self.mock_pathutils().copyfile.assert_called_once_with(
                    fake_vhd_path, fake_root_path)
                self.mock_vhdutils().get_vhd_info.assert_called_once_with(
                    fake_vhd_path)
                if not isinstance(self.mock_vhdutils, vhdutilsv2.VHDUtilsV2):
                    get_size.assert_called_once_with(fake_root_path,
                                                     root_vhd_internal_size)
                if root_vhd_internal_size > vhd_size:
                    self.mock_vhdutils().resize_vhd.assert_called_once_with(
                        fake_root_path, root_vhd_internal_size)
                self.assertEqual(response, fake_root_path)

    def test_create_root_vhd(self):
        self._test_create_root_vhd(use_cow_images=False,
                                   instance_type=vhdutilsv2.VHDUtilsV2,
                                   vhd_size=11)

    def test_create_root_vhd_use_cow_images_true(self):
        self._test_create_root_vhd(use_cow_images=True,
                                   instance_type=vhdutilsv2.VHDUtilsV2,
                                   vhd_size=11)

    def test_create_root_vhd_size_less_than_internal(self):
        self._test_create_root_vhd(use_cow_images=False,
                                   instance_type=None,
                                   vhd_size=9)

    def test_create_ephemeral_vhd(self):
        mock_instance = get_instance_mock(instance_data=self.instance_data)
        mock_instance.get.return_value = mock_instance['ephemeral_gb']
        best_supported = self.mock_vhdutils().get_best_supported_vhd_format
        best_supported.return_value = 'fake format'
        self.mock_pathutils().get_ephemeral_vhd_path.return_value = 'fake path'
        response = self._vmops.create_ephemeral_vhd(instance=mock_instance)
        self.mock_vhdutils().get_best_supported_vhd_format.assert_called_with()
        self.mock_pathutils().get_ephemeral_vhd_path.assert_called_with(
            mock_instance['name'], 'fake format')
        self.mock_vhdutils().create_dynamic_vhd.assert_called_with(
            'fake path', mock_instance['ephemeral_gb'] * unit.Gi,
            'fake format')
        self.assertEqual(response, 'fake path')

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.ebs_root_in_block_devices')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.create_ephemeral_vhd')
    @mock.patch('nova.virt.hyperv.vmops.VMOps._delete_disk_files')
    @mock.patch('nova.virt.hyperv.vmops.VMOps._create_root_vhd')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.create_instance')
    @mock.patch('nova.virt.hyperv.vmops.VMOps._create_config_drive')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_on')
    @mock.patch('nova.virt.hyperv.vmops.VMOps.destroy')
    @mock.patch('nova.virt.configdrive.required_by')
    def _test_spawn(self, mock_required_by, mock_destroy, mock_power_on,
                    mock_create_config_drive,
                    mock_create_instance,
                    mock_create_root_vhd,
                    mock_delete_disk_files,
                    mock_create_ephemeral_vhd,
                    mock_ebs_root_in_block_devices, exists, root_in_block,
                    required_by, fail):
        mock_instance = get_instance_mock(instance_data=self.instance_data)
        mock_context = mock.MagicMock()

        fake_ephemeral_path = os.path.join(os.path.join('fake', 'path'),
                                           'ephemeral')
        self.mock_vmutils().vm_exists.return_value = exists
        mock_ebs_root_in_block_devices.return_value = root_in_block
        if root_in_block:
            fake_root_path = None
        else:
            fake_root_path = os.path.join(os.path.join('fake', 'root'),
                                          'path')
        mock_create_root_vhd.return_value = fake_root_path
        mock_create_ephemeral_vhd.return_value = fake_ephemeral_path
        mock_required_by.return_value = required_by
        mock_create_instance.side_effect = [fail]
        if exists:
            self.assertRaises(exception.InstanceExists, self._vmops.spawn,
                              mock_context, mock_instance, None,
                              ['fake file'], 'fake password', 'fake info',
                              'device info')
        elif fail is Exception:
            self.assertRaises(vmutils.HyperVException, self._vmops.spawn,
                              mock_context, mock_instance, None,
                              ['fake file'], 'fake password', 'fake info',
                              'device info')
            mock_destroy.assert_called_once_with(mock_instance)
        else:
            self._vmops.spawn(context=mock_context, instance=mock_instance,
                              image_meta=None, injected_files=['fake file'],
                              admin_password='fake password',
                              network_info='fake info',
                              block_device_info='device info')
            self.mock_vmutils().vm_exists.assert_called_once_with(
                mock_instance['name'])
            mock_delete_disk_files.assert_called_once_with(
                mock_instance['name'])
            mock_ebs_root_in_block_devices.assert_called_once_with(
                'device info')
            if not root_in_block:
                mock_create_root_vhd.assert_called_once_with(mock_context,
                                                             mock_instance)
            mock_create_ephemeral_vhd.assert_called_once_with(mock_instance)
            mock_create_instance.assert_called_once_with(mock_instance,
                                                         'fake info',
                                                         'device info',
                                                         fake_root_path,
                                                         fake_ephemeral_path)
            mock_required_by.assert_called_once_with(mock_instance)
            if required_by:
                mock_create_config_drive.assert_called_once_with(
                    mock_instance, ['fake file'], 'fake password')
            mock_power_on.assert_called_once_with(mock_instance)

    def test_spawn(self):
        self._test_spawn(exists=False, root_in_block=False,
                         required_by=True, fail=None)

    def test_spawn_instance_exists(self):
        self._test_spawn(exists=True, root_in_block=False, required_by=True,
                         fail=None)

    def test_spawn_create_instance_exception(self):
        self._test_spawn(exists=False, root_in_block=False, required_by=True,
                         fail=Exception)

    def test_spawn_not_required(self):
        self._test_spawn(exists=False, root_in_block=False,
                         required_by=False, fail=None)

    def test_spawn_root_in_block(self):
        self._test_spawn(exists=False, root_in_block=True,
                         required_by=False, fail=None)

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.attach_volumes')
    def _test_create_instance(self, mock_attach_volumes, fake_root_path,
                              fake_ephemeral_path, enable_instance_metrics):
        mock_vif_driver = mock.MagicMock()
        self._vmops._vif_driver = mock_vif_driver
        CONF.set_override('enable_instance_metrics_collection',
                          enable_instance_metrics, 'hyperv')
        fake_network_info = {'id': 'fake id',
                             'address': 'fake address'}
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.create_instance(instance=mock_instance,
                                    network_info=[fake_network_info],
                                    block_device_info='device info',
                                    root_vhd_path=fake_root_path,
                                    eph_vhd_path=fake_ephemeral_path)
        self.mock_vmutils().create_vm.assert_called_once_with(
            mock_instance['name'], mock_instance['memory_mb'],
            mock_instance['vcpus'], CONF.hyperv.limit_cpu_features,
            CONF.hyperv.dynamic_memory_ratio)
        print self.mock_vmutils.mock_calls
        expected = []
        ctrl_disk_addr = 0
        if fake_root_path:
            expected.append(mock.call(mock_instance['name'], fake_root_path,
                                      0, ctrl_disk_addr, constants.IDE_DISK))
            ctrl_disk_addr += 1
        if fake_ephemeral_path:
            expected.append(mock.call(mock_instance['name'],
                                      fake_ephemeral_path, 0, ctrl_disk_addr,
                                      constants.IDE_DISK))
        self.assertEqual(self.mock_vmutils().attach_ide_drive.call_args_list,
                         expected)
        self.mock_vmutils().create_scsi_controller.assert_called_once_with(
            mock_instance['name'])
        mock_attach_volumes.assert_called_once_with('device info',
                                                    mock_instance['name'],
                                                    fake_root_path is None)
        self.mock_vmutils().create_nic.assert_called_once_with(
            mock_instance['name'], 'fake id', 'fake address')
        mock_vif_driver.plug.assert_called_once_with(
            mock_instance, fake_network_info)
        mock_enable = self.mock_vmutils().enable_vm_metrics_collection
        if enable_instance_metrics:
            mock_enable.assert_called_once_with(mock_instance['name'])

    def test_create_instance(self):
        fake_root_path = os.path.join(os.path.join('fake', 'root'),
                                      'path')
        fake_ephemeral_path = os.path.join(os.path.join('fake', 'path'),
                                           'ephemeral')
        self._test_create_instance(fake_root_path=fake_root_path,
                                   fake_ephemeral_path=fake_ephemeral_path,
                                   enable_instance_metrics=True)

    def test_create_instance_no_root_path(self):
        fake_ephemeral_path = os.path.join(os.path.join('fake', 'path'),
                                           'ephemeral')
        self._test_create_instance(fake_root_path=None,
                                   fake_ephemeral_path=fake_ephemeral_path,
                                   enable_instance_metrics=True)

    def test_create_instance_no_ephemeral_path(self):
        fake_root_path = os.path.join(os.path.join('fake', 'path'),
                                      'root')
        self._test_create_instance(fake_root_path=fake_root_path,
                                   fake_ephemeral_path=None,
                                   enable_instance_metrics=True)

    def test_create_instance_no_path(self):
        self._test_create_instance(fake_root_path=None,
                                   fake_ephemeral_path=None,
                                   enable_instance_metrics=False)

    def test_create_instance_enable_instance_metrics_false(self):
        fake_root_path = os.path.join(os.path.join('fake', 'root'),
                                      'path')
        fake_ephemeral_path = os.path.join(os.path.join('fake', 'path'),
                                           'ephemeral')
        self._test_create_instance(fake_root_path=fake_root_path,
                                   fake_ephemeral_path=fake_ephemeral_path,
                                   enable_instance_metrics=False)

    @mock.patch('nova.api.metadata.base.InstanceMetadata')
    @mock.patch('nova.virt.configdrive.ConfigDriveBuilder')
    @mock.patch('nova.utils.execute')
    def _test_create_config_drive(self, mock_execute, mock_ConfigDriveBuilder,
                                  mock_InstanceMetadata, config_drive_format,
                                  ret_val):
        mock_instance = get_instance_mock(self.instance_data)
        CONF.set_override('config_drive_format', config_drive_format)
        CONF.set_override('config_drive_inject_password', True, 'hyperv')
        CONF.set_override('config_drive_cdrom', False, 'hyperv')
        self.mock_pathutils().get_instance_dir.return_value = 'fake'
        mock_ConfigDriveBuilder().__enter__().make_drive.side_effect = [
            ret_val]

        #exception not raised, no raise before hyperv Exception

        #if config_drive_format != 'iso9660':
        #    self.assertRaises(vmutils.HyperVException,
        #                      self._vmops._create_config_drive,
        #                      mock_instance, ['fake file'], 'fake password')
        if ret_val is processutils.ProcessExecutionError:
            self.assertRaises(processutils.ProcessExecutionError,
                              self._vmops._create_config_drive,
                              mock_instance, ['fake file'], 'fake password')
        else:
            self._vmops._create_config_drive(instance=mock_instance,
                                             injected_files=['fake file'],
                                             admin_password='fake password')
            mock_InstanceMetadata.assert_called_once_with(
                mock_instance, content=['fake ' 'file'],
                extra_md={'admin_pass': 'fake password'})
            self.mock_pathutils().get_instance_dir.assert_called_once_with(
                mock_instance['name'])
            mock_ConfigDriveBuilder.assert_called_with(
                instance_md=mock_InstanceMetadata())
            print mock_ConfigDriveBuilder.mock_calls
            mock_make_drive = mock_ConfigDriveBuilder().__enter__().make_drive
            mock_make_drive.assert_called_once_with(
                os.path.join('fake', 'configdrive.iso'))
            mock_execute.assert_called_once_with(
                CONF.hyperv.qemu_img_cmd, 'convert', '-f', 'raw', '-O', 'vpc',
                os.path.join('fake', 'configdrive.iso'),
                os.path.join('fake', 'configdrive.vhd'), attempts=1)
            self.mock_pathutils().remove.assert_called_once_with(
                os.path.join('fake', 'configdrive.iso'))
            self.mock_vmutils().attach_ide_drive.assert_called_once_with(
                mock_instance['name'],
                os.path.join('fake', 'configdrive.vhd'), 1, 0,
                constants.IDE_DISK)

    def test_create_config_drive(self):
        self._test_create_config_drive(config_drive_format='iso9660',
                                       ret_val=None)

    def test_create_config_drive_other_drive_format(self):
        self._test_create_config_drive(config_drive_format='other',
                                       ret_val=None)

    def test_create_config_drive_execution_error(self):
        self._test_create_config_drive(
            config_drive_format='iso9660',
            ret_val=processutils.ProcessExecutionError)

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.disconnect_volume')
    def test_disconnect_volumes(self, mock_disconnect_volume):
        self._vmops._disconnect_volumes(volume_drives=['fake volume'])
        mock_disconnect_volume.assert_called_once_with('fake volume')

    def test_delete_disk_files(self):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops._delete_disk_files(mock_instance['name'])
        self.mock_pathutils().get_instance_dir.assert_called_once_with(
            mock_instance['name'], create_dir=False, remove_dir=True)

    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_off')
    @mock.patch('nova.virt.hyperv.vmops.VMOps._disconnect_volumes')
    @mock.patch('nova.virt.hyperv.vmops.VMOps._delete_disk_files')
    def _test_destroy(self, mock_delete_disk_files, mock_disconnect_volumes,
                      mock_power_off, exception):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().destroy_vm.side_effect = [exception]
        self.mock_vmutils().vm_exists.return_value = True
        self.mock_vmutils().get_vm_storage_paths.return_value = (
            'fake disk', 'fake volume')
        if exception is Exception:
            self.assertRaises(vmutils.HyperVException, self._vmops.destroy,
                              mock_instance)
        else:
            self._vmops.destroy(instance=mock_instance)
            self.mock_vmutils().vm_exists.assert_called_with(
                mock_instance['name'])
            mock_power_off.assert_called_once_with(mock_instance)
            self.mock_vmutils().get_vm_storage_paths.assert_called_once_with(
                mock_instance['name'])
            self.mock_vmutils().destroy_vm.assert_called_once_with(
                mock_instance['name'])
            mock_disconnect_volumes.assert_called_once_with('fake volume')
            mock_delete_disk_files.assert_called_once_with(
                mock_instance['name'])

    def test_destroy(self):
        self._test_destroy(exception=None)

    def test_destroy_exception(self):
        self._test_destroy(exception=Exception)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_reboot(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.reboot(instance=mock_instance, network_info='fake info',
                           reboot_type='fake type')
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_REBOOT)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_pause(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.pause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_PAUSED)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_unpause(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.unpause(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_suspend(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.suspend(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_SUSPENDED)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_resume(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.resume(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_ENABLED)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_power_off(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.power_off(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_DISABLED)

    @mock.patch('nova.virt.hyperv.vmops.VMOps._set_vm_state')
    def test_power_on(self, mock_set_vm_state):
        mock_instance = get_instance_mock(self.instance_data)
        self._vmops.power_on(instance=mock_instance)
        mock_set_vm_state.assert_called_once_with(
            mock_instance['name'], constants.HYPERV_VM_STATE_ENABLED)

    def _test_set_vm_state(self, exception):
        self.mock_vmutils().set_vm_state.side_effect = [exception]
        if exception is Exception:
            self.assertRaises(vmutils.HyperVException,
                              self._vmops._set_vm_state, 'fake name',
                              'fake state')
        else:
            self._vmops._set_vm_state('fake name', 'fake state')
        self.mock_vmutils().set_vm_state.assert_called_once_with('fake name',
                                                                 'fake state')

    def test_set_vm_state(self):
        self._test_set_vm_state(exception=None)

    def test_set_vm_state_exception(self):
        self._test_set_vm_state(exception=Exception)
