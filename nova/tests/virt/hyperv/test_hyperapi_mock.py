# vim: tabstop=4 shiftwidth=4 softtabstop=4

#  Copyright 2012 Cloudbase Solutions Srl
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

"""
Test suite for the Hyper-V driver and related APIs.
"""

import io
import mock
import os
import platform
import shutil
import time
import uuid
import unittest

from oslo.config import cfg

from nova.api.metadata import base as instance_metadata
from nova.compute import power_state
from nova.compute import task_states
from nova import context
from nova import db
from nova import exception
from nova.image import glance
from nova.openstack.common.gettextutils import _
from nova import test
from nova.tests import fake_network
from nova.tests.image import fake as fake_image
from nova.tests import matchers
from nova.tests.virt.hyperv import db_fakes
from nova.tests.virt.hyperv import fake
from nova import unit
from nova import utils
from nova.virt import configdrive
from nova.virt import driver
from nova.virt.hyperv import basevolumeutils
from nova.virt.hyperv import constants
from nova.virt.hyperv import driver as driver_hyperv
from nova.virt.hyperv import hostutils
from nova.virt.hyperv import livemigrationutils
from nova.virt.hyperv import networkutils
from nova.virt.hyperv import networkutilsv2
from nova.virt.hyperv import pathutils
from nova.virt.hyperv import utilsfactory
from nova.virt.hyperv import vhdutils
from nova.virt.hyperv import vhdutilsv2
from nova.virt.hyperv import vmutils
from nova.virt.hyperv import vmutilsv2
from nova.virt.hyperv import volumeutils
from nova.virt.hyperv import volumeutilsv2
from nova.virt import images

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


class HyperVAPITestCase(unittest.TestCase):
    """Unit tests for Hyper-V driver calls."""
    _FAKE_NAME = 'fake name'
    _FAKE_USER_ID = 'fake user ID'
    _FAKE_PROJECT_ID = 'fake project ID'
    _FAKE_INSTANCE_DATA = 'fake instance data'
    _FAKE_IMAGE_ID = 'fake image id'
    _FAKE_IMAGE_METADATA = 'fake image data'
    _FAKE_NETWORK_INFO = 'fake network info'
    #use db_fakes.get_fake_instance_data for this dict:
    instance_data = {'name': _FAKE_NAME,
                     'memory_mb': 1024,
                     'vcpu': 1,
                     'image_ref': _FAKE_IMAGE_ID,
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
        self.mock_hostutils._conn_cimv2.Win32_OperatingSystem()[0].Version =\
            'win32'
        self.mock_networkutils = self.networkutils_patcher.start()
        self.mock_volutils = self.volumeutils_patcher.start()
        self.mock_vhdutils = self.vhdutils_patcher.start()

        self._driver = driver_hyperv.HyperVDriver(None)
        self._driver._conn_wmi = mock.MagicMock()
        self._driver._conn_cimv2 = mock.MagicMock()
        super(HyperVAPITestCase, self).setUp()

    def tearDown(self):
        self.vmutils_patcher.stop()
        self.hostutils_patcher.stop()
        self.networkutils_patcher.stop()
        self.pathutils_patcher.stop()
        self.volumeutils_patcher.stop()
        self.vhdutils_patcher.stop()

        super(HyperVAPITestCase, self).tearDown()

    def test_list_instances(self):
        response = self._driver.list_instances()
        self.assertEqual(response, self.mock_vmutils().list_instances())
        self.mock_vmutils().list_instances.assert_called_with()

    @mock.patch('nova.virt.images.fetch')
    def _test_spawn(self, mock_fetch, vm_exists, in_block_devices,
                    path_exists, exception_to_raise):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        root_vhd_path = None
        test_path = 'fake/path/dir/' + self._FAKE_IMAGE_ID
        expected = [mock.call(test_path + '.vhd'),
                    mock.call(test_path + '.vhdx')]

        self.mock_vmutils().vm_exists.return_value = vm_exists
        self.mock_volutils().ebs_root_in_block_devices.return_value = in_block_devices
        self.mock_vhdutils().get_best_supported_vhd_format.return_value = \
            'fake format'
        self.mock_pathutils().get_base_vhd_dir.return_value = 'fake/path/dir'
        self.mock_pathutils.get_ephemeral_vhd_path.return_value = 'fake/path'
        self.mock_pathutils().exists.return_value = path_exists
        self.mock_vhdutils.get_vhd_format.return_value = 'vhd'
        mock_fetch.side_effect = exception_to_raise
        CONF.set_override('use_cow_images', True)

        if vm_exists:
            self.assertRaises(exception.InstanceExists,  self._driver.spawn,
                              mock_context, mock_instance,
                              self._FAKE_IMAGE_METADATA,
                              [], None, self._FAKE_NETWORK_INFO, None)
        elif not path_exists and exception_to_raise is Exception:
            #combine with vm exists to avoid code duplication
            self.assertRaises(exception.InstanceExists,  self._driver.spawn,
                              mock_context, mock_instance,
                              self._FAKE_IMAGE_METADATA,
                              [], None, self._FAKE_NETWORK_INFO, None)
            expected.append(mock.call(root_vhd_path))
            self.mock_pathutils().remove.assert_called_once_with(
                        root_vhd_path)
            self.assertEqual(
                    self.mock_pathutils().exists.call_args_list, expected)

        else:
            self._driver.spawn(context=mock_context, instance=mock_instance,
                               image_meta=self._FAKE_IMAGE_METADATA,
                               injected_files=[], admin_password=None,
                               network_info=self._FAKE_NETWORK_INFO,
                               block_device_info=None)
            self.mock_vmutils().vm_exists.assert_called_with(self._FAKE_NAME)
            self.mock_pathutils().get_instance_dir.assert_called_with(
                self._FAKE_NAME, False, True)
            self.mock_volutils().volume_in_mapping.assert_called_once_with(
                self.mock_volutils()._default_root_device, None)

            if not in_block_devices:
                #get_cached_image
                self.mock_pathutils().get_base_vhd_dir.assert_called_once_with()
                if not path_exists:
                    mock_fetch.assert_called_once_with(mock_context,
                                                       self._FAKE_IMAGE_ID,
                                                       test_path,
                                                       self._FAKE_USER_ID,
                                                       self._FAKE_PROJECT_ID)
                    self.mock_vhdutils.get_vhd_format.assert_called_with(
                        test_path)
                    root_vhd_path = test_path + '.' + 'vhd'
                    self.mock_pathutils.rename.assert_called_with(
                        test_path, root_vhd_path)
                    #resize and cache vhd

                #end

            #create ephemeral vhd
            self.mock_vhdutils().get_best_supported_vhd_format\
                .assert_called_with()
            self.mock_pathutils.get_ephemeral_vhd_path.assert_called_with(
                self._FAKE_NAME, 'fake format')
            self.mock_vhdutils().create_dynamic_vhd.assert_called_with(
                'fake/path', 'wroooooong', 'fake format')


            self.mock_vmutils().create_vm.assert_called_with(
                self._FAKE_NAME, 1024, 1, CONF.hyperv.limit_cpu_features,
                CONF.hyperv.dynamic_memory_ratio)

            if root_vhd_path:
                self.mock_vmutils().attach_ide_drive.assert_called_with(
                    self._FAKE_NAME, root_vhd_path, 0, 0, constants.IDE_DISK)


    # def test_spawn(self):
    #     self._test_spawn(vm_exists=False)
    #
    # def test_spawn_vm_exists(self):
    #     self._test_spawn(vm_exists=True)

    def _test_reboot(self, response):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.reboot,
                              mock_context, mock_instance,
                            self._FAKE_NETWORK_INFO, 'fake', None, None)
        else:
            self._driver.reboot(context=mock_context, instance=mock_instance,
                                network_info=self._FAKE_NETWORK_INFO,
                                reboot_type='fake',
                                block_device_info=None,
                                bad_volumes_callback=None)
            print mock_instance.__getitem__.mock_calls
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_REBOOT)

    def test_reboot(self):
        self._test_reboot(response=None)

    def test_reboot_exception(self):
        self._test_reboot(response=Exception)

    @mock.patch('nova.virt.hyperv.vmops.VMOps.power_off')
    def _test_destroy(self, mock_power_off, exception_to_raise):

        self.mock_vmutils().vm_exists.return_value = True
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().get_vm_storage_paths.return_value = ['fake/disk/file'], ['fake/volumedrive']
        self.mock_volutils().get_session_id_from_mounted_disk.return_value = \
            'fake'
        self.mock_vmutils().destroy_vm.side_effect = [exception_to_raise]
        if exception_to_raise is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.destroy,
                              mock_context, mock_instance, None, None, True)
        else:
            self._driver.destroy(context=mock_context, instance=mock_instance,
                                 network_info=None, block_device_info=None,
                                 destroy_disks=True)
            self.mock_vmutils().vm_exists.assert_called_with(self._FAKE_NAME)
            mock_power_off.assert_called_once_with(mock_instance)
            self.mock_vmutils().vm_exists.assert_called_once_with(
                self._FAKE_NAME)
            self.mock_vmutils().get_vm_storage_paths.assert_called_once_with(
                self._FAKE_NAME)
            self.mock_vmutils().destroy_vm.assert_called_once_with(
                self._FAKE_NAME)
            self.mock_volutils().get_session_id_from_mounted_disk\
                .assert_called_once_with('fake/volumedrive')
            self.mock_volutils().execute_log_out.assert_called_once_with(
                'fake')
            self.mock_pathutils().get_instance_dir.assert_called_once_with(
                'fake name', create_dir=False, remove_dir=True)

    def test_destroy(self):
        self._test_destroy(exception_to_raise=None)

    def test_destroy_exception(self):
        self._test_destroy(exception_to_raise=Exception)

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
                              self._driver.get_info, mock_instance)
        else:
            response = self._driver.get_info(mock_instance)
            self.mock_vmutils().vm_exists.assert_called_once_with(self._FAKE_NAME)
            self.mock_vmutils().get_vm_summary_info.assert_called_once_with(
                self._FAKE_NAME)
            self.assertEqual(response, expected)

    def test_get_info(self):
        self._test_get_info(vm_exists=True)

    def test_get_info_exception(self):
        self._test_get_info(vm_exists=False)

    @mock.patch('time.sleep')
    def _test_attach_volume(self, mock_sleep, device_number1,
                            device_number2, mounted_disk_path):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        fake_info = {'data':{'target_lun': 'fake lun',
                             'target_iqn': 'fake iqn',
                             'target_portal': 'fake portal'}}
        mock_info = mock.MagicMock(spec_set=dict)
        def getitem(key):
            return fake_info[key]
        mock_info.__getitem__.side_effect = getitem
        CONF.set_override('volume_attach_retry_count', 1, 'hyperv')

        self.mock_volutils().get_device_number_for_target.side_effect = [
            device_number1, device_number2, device_number2]
        self.mock_vmutils().get_mounted_disk_by_drive_number.return_value = mounted_disk_path
        self.mock_vmutils().get_vm_scsi_controller.return_value = 'fake/ctrl/path'
        self.mock_vmutils().get_attached_disks_count.return_value = 'fake slot'

        if not device_number1 and not device_number2 or mounted_disk_path is None:
            self.assertRaises(vmutils.HyperVException,
                              self._driver.attach_volume, mock_context,
                              mock_info, mock_instance, 'fake mount point')
            if device_number1 or device_number2:
                self.mock_volutils().logout_storage_target.assert_called_with(
                    mock_info['data']['target_iqn'])

        else:
            self._driver.attach_volume(context=mock_context,
                                       connection_info=mock_info,
                                       instance=mock_instance,
                                       mountpoint='fake mount point')
            if not device_number1:
                self.mock_volutils().login_storage_target.assert_called_with(
                    mock_info['data']['target_lun'],
                    mock_info['data']['target_iqn'],
                    mock_info['data']['target_portal'])
                self.assertEqual(self.mock_volutils()
                                 .get_device_number_for_target.call_count, 3)
            else:
                self.assertEqual(self.mock_volutils().get_device_number_for_target.call_count, 2)

            self.mock_volutils().get_device_number_for_target.assert_called_with(
            mock_info['data']['target_iqn'],
            mock_info['data']['target_lun'])

            self.mock_vmutils().get_mounted_disk_by_drive_number.assert_called_with(device_number2)
            self.mock_vmutils().get_vm_scsi_controller.assert_called_with(
                self._FAKE_NAME)
            self.mock_vmutils().get_attached_disks_count\
                .assert_called_with('fake/ctrl/path')
            self.mock_vmutils().attach_volume_to_controller.assert_called_with(
                self._FAKE_NAME, 'fake/ctrl/path', 'fake slot',
                mounted_disk_path)

    def test_attach_volume(self):
        self._test_attach_volume(device_number1=None, device_number2=2,
                                 mounted_disk_path='fake/mounted/path')

    def test_attach_volume_already_logged_in(self):
        self._test_attach_volume(device_number1=1, device_number2=2,
                                 mounted_disk_path='fake/mounted/path')

    def test_attach_volume_exception_no_device_number(self):
        self._test_attach_volume(device_number1=None, device_number2=None,
                                 mounted_disk_path='fake/mounted/path')

    def test_attach_volume_exception_no_mounted_disk_path(self):
        self._test_attach_volume(device_number1=1, device_number2=None,
                                 mounted_disk_path=None)

    def _test_detach_volume(self, mounted_disk_path):
        mock_instance = get_instance_mock(self.instance_data)
        fake_info = {'data':{'target_lun': 'fake lun',
                             'target_iqn': 'fake iqn',
                             'target_portal': 'fake portal'}}
        mock_info = mock.MagicMock(spec_set=dict)
        def getitem(key):
            return fake_info[key]
        mock_info.__getitem__.side_effect = getitem
        CONF.set_override('volume_attach_retry_count', 1, 'hyperv')
        self.mock_volutils().get_device_number_for_target.side_effect =[1, 2]
        self.mock_vmutils().get_mounted_disk_by_drive_number.return_value = mounted_disk_path

        if mounted_disk_path is None:
            self.assertRaises(exception.NotFound,
                              self._driver.detach_volume, mock_info,
                              mock_instance, 'fake mount point')
        else:
            self._driver.detach_volume(connection_info=mock_info,
                                       instance=mock_instance,
                                       mountpoint='fake mount point')

            self.mock_volutils().get_device_number_for_target.assert_called_with(
                mock_info['data']['target_iqn'],
                mock_info['data']['target_lun'])
            self.mock_vmutils().detach_vm_disk.assert_called_with(
                self._FAKE_NAME, mounted_disk_path)
            self.mock_volutils().logout_storage_target.assert_called_with(
                mock_info['data']['target_iqn'])

    def test_detach_volume(self):
        self._test_detach_volume(mounted_disk_path='fake/path')

    def test_detach_volume_no_mounted_disk_path_exception(self):
        self._test_detach_volume(mounted_disk_path=None)

    def test_get_volume_connector(self):
        mock_instance = get_instance_mock(self.instance_data)
        mock_initiator = mock.MagicMock()
        self.mock_volutils().get_iscsi_initiator.return_value = mock_initiator
        response = self._driver.get_volume_connector(instance=mock_instance)
        self.mock_volutils().get_iscsi_initiator.assert_called_once_with()
        self.assertEqual(response, {'ip': CONF.my_ip,
                                    'host': CONF.host,
                                    'initiator': mock_initiator})

    def test_get_available_resource(self):
        mock_processors = mock.MagicMock(spec_set=dict)
        fake_info = {0: {'Architecture': 0,
                         'Name': 'fake name',
                         'Manufacturer': 'fake Manufacturer',
                         'NumberOfCores': 1,
                         'NumberOfLogicalProcessors': 2}}

        def getitem(key):
            return fake_info[key]
        mock_processors.__getitem__.side_effect = getitem
        mock_processors.__len__.return_value = 1

        self.mock_hostutils().get_memory_info.return_value = (20 * 1024,
                                                              10 * 1024)
        self.mock_pathutils().get_instances_dir.return_value = '/fake/path'
        self.mock_hostutils().get_volume_info.return_value = (
            100 * unit.Gi, 50 * unit.Gi)
        self.mock_hostutils().get_cpus_info.return_value = mock_processors
        self.mock_hostutils().is_cpu_feature_present.return_value = True
        self.mock_hostutils().get_windows_version.return_value = '2'

        response = self._driver.get_available_resource(
            nodename=self._FAKE_NAME)

        self.mock_hostutils().get_memory_info.assert_called_with()
        self.mock_pathutils().get_instances_dir.assert_called_with()
        self.mock_hostutils().get_volume_info.assert_called_with('')
        self.mock_hostutils().get_cpus_info.assert_called_with()
        expected = {'supported_instances':
                        '[["i686", "hyperv", "hvm"], ["x86_64", "hyperv", "hvm"]]',
                    'hypervisor_hostname': 'ubuntu-VirtualBox',
                    'cpu_info': '{"vendor": "fake Manufacturer", "model": "fake name", "arch": "x86", "features": ["mmx", "sse", "3dnow", "rdtsc", "pae", "sse2", "nx", "sse3", "xsave", "slat", "vmx"], "topology": {"cores": 1, "threads": 2, "sockets": 1}}',
                    'hypervisor_version': '2',
                    'local_gb': 100,
                    'memory_mb_used': 10,
                    'vcpus_used': 0,
                    'hypervisor_type': 'hyperv',
                    'local_gb_used': 50,
                    'memory_mb': 20,
                    'vcpus': 2}
        self.assertEqual(response, expected)

    def _test_get_host_stats(self, refresh):
        self.mock_hostutils().get_memory_info.return_value = (20 * 1024,
                                                              10 * 1024)
        self.mock_pathutils().get_instances_dir.return_value = '/fake/path'
        self.mock_hostutils().get_volume_info.return_value = ( 100 * unit.Gi,
                                                               50 * unit.Gi)
        self._driver._hostops._stats = {'fake key': 'fake index'}
        self._driver.get_host_stats(refresh=refresh)
        if refresh:
            self.assertEqual(self._driver._hostops._stats, {'host_memory_free_computed': 10, 'disk_available': 50, 'supported_instances': [('i686', 'hyperv', 'hvm'), ('x86_64', 'hyperv', 'hvm')], 'host_memory_overhead': 10, 'hypervisor_hostname': 'ubuntu-VirtualBox', 'host_memory_free': 10, 'disk_total': 100, 'host_memory_total': 20, 'disk_used': 50}
)
            self.mock_hostutils().get_memory_info.assert_called_with()
            self.mock_pathutils().get_instances_dir.assert_called_with()
            self.mock_hostutils().get_volume_info.assert_called_with('')
        else:
            self.assertEqual(self._driver._hostops._stats,
                             {'fake key': 'fake index'})

    def test_get_host_stats_refresh_true(self):
        self._test_get_host_stats(refresh=True)

    def test_get_host_stats_refresh_false(self):
        self._test_get_host_stats(refresh=False)

    @mock.patch('nova.image.glance.get_remote_image_service')
    def test_snapshot(self, mock_get_remote_image_service):
        update_task_state = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        mock_context = mock.MagicMock()
        mock_glance_service = mock.MagicMock()
        self.mock_pathutils().lookup_root_vhd_path.return_value = 'fake/path'
        self.mock_pathutils().get_export_dir.return_value = 'fake/export/path'
        mock_get_remote_image_service.return_value = (mock_glance_service,
        'fake image id')
        self.mock_vmutils().take_vm_snapshot.return_value = 'fake/snap/path'

        self._driver.snapshot(context=mock_context, instance=mock_instance,
                              name='fake', update_task_state=update_task_state)
        self.mock_vmutils().take_vm_snapshot.assert_called_once_with(
            mock_instance['name'])
        self.mock_pathutils().lookup_root_vhd_path.assert_called_with(
            mock_instance['name'])
        self.mock_vhdutils().get_vhd_parent_path.assert_called_with('fake/path')
        self.mock_pathutils.get_export_dir(mock_instance['name'])
        expected_copyfile = [mock.call('fake/path', 'fake/path'),
                             mock.call('fake/path', 'fake/path')]
        self.assertEqual(self.mock_pathutils().copyfile.call_args_list,
                         expected_copyfile)
        self.mock_vhdutils().reconnect_parent_vhd.assert_called_with(
            'fake/path', 'fake/path')
        self.mock_vhdutils().merge_vhd.assert_called_with(
            'fake/path', 'fake/path')
        expected_state = [mock.call(
            task_state=task_states.IMAGE_PENDING_UPLOAD),
                          mock.call(
                              task_state=task_states.IMAGE_UPLOADING,
                              expected_state=task_states.IMAGE_PENDING_UPLOAD)]
        self.assertEqual(update_task_state.call_args_list, expected_state)
        mock_get_remote_image_service.assert_called_once_with(
            mock_context, mock_instance['name'])
        image_metadata = {"is_public": False,
                          "disk_format": "vhd",
                          "container_format": "bare",
                          "properties": {}}
        with self.mock_pathutils.open as f:
            mock_glance_service().update.assert_called_with(
                mock_context, 'fake image id', image_metadata, f)
        self.mock_vmutils().remove_vm_snapshot.assert_called_once_with(
            'fake/snap/path')
        self.mock_pathutils.rmtree.assert_called_once_with()

    def _test_pause(self, response):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.pause,
                              mock_instance)
        else:
            self._driver.pause(instance=mock_instance)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_PAUSED)

    def test_pause(self):
        self._test_pause(response=None)

    def test_pause_exception(self):
        self._test_pause(response=Exception)

    def _test_unpause(self, response):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.unpause,
                              mock_instance)
        else:
            self._driver.unpause(instance=mock_instance)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_ENABLED)

    def test_unpause(self):
        self._test_pause(response=None)

    def test_unpause_exception(self):
        self._test_pause(response=Exception)

    def _test_suspend(self, response):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.suspend,
                              mock_instance)
        else:
            self._driver.suspend(instance=mock_instance)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_SUSPENDED)

    def test_suspend(self):
        self._test_pause(response=None)

    def test_suspend_exception(self):
        self._test_pause(response=Exception)

    def _test_resume(self, response):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.resume,
                              mock_context, mock_instance,
                              self._FAKE_NETWORK_INFO, None)
        else:
            self._driver.resume(context=mock_context, instance=mock_instance,
                                network_info=self._FAKE_NETWORK_INFO,
                                block_device_info=None)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_ENABLED)

    def test_resume(self):
        self._test_pause(response=None)

    def test_resume_exception(self):
        self._test_pause(response=Exception)

    def _test_power_off(self, response):
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.power_off,
                              mock_instance)
        else:
            self._driver.power_off(instance=mock_instance)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_DISABLED)

    def test_power_off(self):
        self._test_pause(response=None)

    def test_power_off_exception(self):
        self._test_pause(response=Exception)

    def _test_power_on(self, response):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        self.mock_vmutils().set_vm_state.side_effect = [response]
        if response is Exception:
            self.assertRaises(vmutils.HyperVException, self._driver.power_on,
                              mock_context, mock_instance,
                              self._FAKE_NETWORK_INFO, None)
        else:
            self._driver.power_on(context=mock_context,
                                  instance=mock_instance,
                                  network_info=self._FAKE_NETWORK_INFO,
                                  block_device_info=None)
            self.mock_vmutils().set_vm_state.assert_called_once_with(
                self._FAKE_NAME, constants.HYPERV_VM_STATE_ENABLED)

    def test_power_on(self):
        self._test_pause(response=None)

    def test_power_on_exception(self):
        self._test_pause(response=Exception)
