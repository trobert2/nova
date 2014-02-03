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
from nova.virt.hyperv import hostops
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


class HostOpsTestCase(unittest.TestCase):
    """Unit tests for Vmops calls."""
    _FAKE_NAME = 'fake name'
    _FAKE_USER_ID = 'fake user ID'
    _FAKE_PROJECT_ID = 'fake project ID'
    _FAKE_INSTANCE_DATA = 'fake instance data'
    _FAKE_IMAGE_ID = 'fake image id'
    _FAKE_IMAGE_METADATA = 'fake image data'
    _FAKE_NETWORK_INFO = 'fake network info'
    #TODO(rtingirica): use db_fakes.get_fake_instance_data for this dict
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
        self.hostutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_hostutils')

        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_hostutils = self.hostutils_patcher.start()

        self._hostops = hostops.HostOps()
        super(HostOpsTestCase, self).setUp()

    def tearDown(self):
        self.hostutils_patcher.stop()
        self.pathutils_patcher.stop()

        super(HostOpsTestCase, self).tearDown()

    def test_get_cpu_info(self):

        mock_processors = mock.MagicMock()
        info = {0: {'Architecture': 0,
                    'Name': 'fake name',
                    'Manufacturer': 'fake manufacturer',
                    'NumberOfCores': 1,
                    'NumberOfLogicalProcessors': 2}}

        def getitem(key):
            return info[key]
        mock_processors.__getitem__.side_effect = getitem
        self.mock_hostutils().get_cpus_info.return_value = mock_processors
        response = self._hostops._get_cpu_info()
        self.mock_hostutils().get_cpus_info.assert_called_once_with()
        expected = [mock.call(3), mock.call(6), mock.call(7), mock.call(8),
                    mock.call(9), mock.call(10), mock.call(12),
                    mock.call(13), mock.call(17), mock.call(20),
                    mock.call(21)]
        self.assertEqual(
            self.mock_hostutils().is_cpu_feature_present.call_args_list,
            expected)
        expected_response = {'vendor': 'fake manufacturer',
                             'model': 'fake name',
                             'arch': 'x86',
                             'features': ['mmx', 'sse', '3dnow', 'rdtsc',
                                          'pae', 'sse2', 'nx', 'sse3',
                                          'xsave', 'slat', 'vmx'],
                             'topology': {'cores': 1,
                                          'threads': 2,
                                          'sockets': 0}}
        self.assertEqual(response, expected_response)

    def test_get_memory_info(self):
        self.mock_hostutils().get_memory_info.return_value = (2048, 1024)
        response = self._hostops._get_memory_info()
        self.mock_hostutils().get_memory_info.assert_called_once_with()
        self.assertEqual(response, (2, 1, 1))

    def test_get_local_hdd_info_gb(self):
        self.mock_pathutils().get_instance_dir.return_value = os.path.join(
            'fake', 'path')
        self.mock_hostutils().get_volume_info.return_value = (2 * unit.Gi,
                                                              1 * unit.Gi)
        response = self._hostops._get_local_hdd_info_gb()
        self.mock_pathutils().get_instances_dir.assert_called_once_with()
        self.mock_hostutils().get_volume_info.assert_called_once_with('')
        self.assertEqual(response, (2, 1, 1))

    def test_get_hypervisor_version(self):
        self.mock_hostutils().get_windows_version.return_value = 'fake'
        response = self._hostops._get_hypervisor_version()
        self.mock_hostutils().get_windows_version.assert_called_once_with()
        self.assertEqual(response, 'fake')

    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_cpu_info')
    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_memory_info')
    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_hypervisor_version')
    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_local_hdd_info_gb')
    @mock.patch('platform.node')
    def test_get_available_resource(self, mock_node,
                                    mock_get_local_hdd_info_gb,
                                    mock_get_hypervisor_version,
                                    mock_get_memory_info, mock_get_cpu_info):
        mock_get_memory_info.return_value = (2, 1, 1)
        mock_get_local_hdd_info_gb.return_value = (2, 1, 1)
        mock_get_cpu_info.return_value = {'vendor': 'fake manufacturer',
                                          'model': 'fake name',
                                          'arch': 'x86',
                                          'features': ['mmx', 'sse',
                                                       '3dnow', 'rdtsc',
                                                       'pae', 'sse2', 'nx',
                                                       'sse3',
                                                       'xsave', 'slat',
                                                       'vmx'],
                                          'topology': {'cores': 1,
                                                       'threads': 2,
                                                       'sockets': 0}}
        mock_get_hypervisor_version.return_value = 'fake'
        response = self._hostops.get_available_resource()
        mock_get_memory_info.assert_called_once_with()
        mock_get_cpu_info.assert_called_once_with()
        mock_get_hypervisor_version.assert_called_once_with()
        expected = {'supported_instances': '[["i686", "hyperv", "hvm"], '
                                           '["x86_64", "hyperv", "hvm"]]',
                    'hypervisor_hostname': mock_node(),
                    'cpu_info': '{"arch": "x86", '
                    '"model": "fake name", "vendor": "fake manufacturer", '
                    '"features": ["mmx", "sse", "3dnow", "rdtsc", "pae", '
                    '"sse2", "nx", "sse3", "xsave", "slat", "vmx"], '
                    '"topology": {"cores": 1, "threads": 2, "sockets": 0}}',
                    'hypervisor_version': 'fake',
                    'local_gb': 2,
                    'memory_mb_used': 1,
                    'vcpus_used': 0,
                    'hypervisor_type': 'hyperv',
                    'local_gb_used': 1,
                    'memory_mb': 2,
                    'vcpus': 0}
        self.assertEqual(response, expected)

    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_memory_info')
    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_local_hdd_info_gb')
    @mock.patch('platform.node')
    def test_update_stats(self, mock_node, mock_get_local_hdd_info_gb,
                          mock_get_memory_info):
        mock_get_memory_info.return_value = (2, 1, 1)
        mock_get_local_hdd_info_gb.return_value = (2, 1, 1)
        self._hostops._update_stats()
        mock_get_memory_info.assert_called_once_with()
        mock_get_local_hdd_info_gb.assert_called_once_with()
        expected = {'disk_available': 1,
                    'disk_total': 2,
                    'disk_used': 1,
                    'host_memory_free': 1,
                    'host_memory_free_computed': 1,
                    'host_memory_overhead': 1,
                    'host_memory_total': 2,
                    'hypervisor_hostname': mock_node(),
                    'supported_instances': [('i686', 'hyperv', 'hvm'),
                                            ('x86_64', 'hyperv', 'hvm')]}
        self.assertEqual(self._hostops._stats, expected)

    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_memory_info')
    @mock.patch('nova.virt.hyperv.hostops.HostOps._get_local_hdd_info_gb')
    @mock.patch('platform.node')
    def test_get_host_stats(self, mock_node, mock_get_local_hdd_info_gb,
                            mock_get_memory_info):
        mock_get_memory_info.return_value = (2, 1, 1)
        mock_get_local_hdd_info_gb.return_value = (2, 1, 1)
        self._hostops.get_host_stats(refresh=True)
        expected = {'disk_available': 1,
                    'disk_total': 2,
                    'disk_used': 1,
                    'host_memory_free': 1,
                    'host_memory_free_computed': 1,
                    'host_memory_overhead': 1,
                    'host_memory_total': 2,
                    'hypervisor_hostname': mock_node(),
                    'supported_instances': [('i686', 'hyperv', 'hvm'),
                                            ('x86_64', 'hyperv', 'hvm')]}
        self.assertEqual(self._hostops._stats, expected)

    def test_get_host_ip_addr(self):
        CONF.set_override('my_ip', None)
        self.mock_hostutils().get_local_ips.return_value = ['10.11.12.13']
        response = self._hostops.get_host_ip_addr()
        self.mock_hostutils().get_local_ips.assert_called_once_with()
        self.assertEqual(response, '10.11.12.13')
