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

from nova.virt.hyperv import livemigrationops
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


class LiveMigrationOpsTestCase(unittest.TestCase):
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
        self.hostutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_hostutils')
        self.livemigrationutils_patcher = mock.patch(
            'nova.virt.hyperv.utilsfactory.get_livemigrationutils')
        self.pathutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                            '.get_pathutils')

        self.mock_hostutils = self.hostutils_patcher.start()
        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_livemigrutils = self.livemigrationutils_patcher.start()

        self.mock_pathutils().check_min_windows_version.return_value = True
        self._livemigrops = livemigrationops.LiveMigrationOps()
        super(LiveMigrationOpsTestCase, self).setUp()

    def tearDown(self):
        self.pathutils_patcher.stop()
        self.livemigrationutils_patcher.stop()
        self.hostutils_patcher.stop()

        super(LiveMigrationOpsTestCase, self).tearDown()

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps.logout_storage_target')
    def _test_live_migration(self, mock_logout_storage_target, side_effect):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        mock_post = mock.MagicMock()
        mock_recover = mock.MagicMock()
        fake_dest = os.path.join('fake', 'dest')
        self.mock_livemigrutils().live_migrate_vm.return_value = [
            ('fake iqn', 'fake tun')]
        mock_logout_storage_target.side_effect = [side_effect]
        if side_effect is Exception:
            self.assertRaises(Exception, self._livemigrops.live_migration,
                              mock_context, mock_instance, fake_dest,
                              mock_post, mock_recover, False, None)
            mock_recover.assert_called_once_with(mock_context,
                                                 mock_instance, fake_dest,
                                                 False)
        else:
            self._livemigrops.live_migration(context=mock_context,
                                             instance_ref=mock_instance,
                                             dest=fake_dest,
                                             post_method=mock_post,
                                             recover_method=mock_recover)
            self.mock_livemigrutils().live_migrate_vm.assert_called_once_with(
                mock_instance['name'], fake_dest)
            mock_logout_storage_target.assert_called_once_with('fake iqn')
            mock_post.assert_called_once_with(mock_context, mock_instance,
                                              fake_dest, False)

    def test_live_migration(self):
        self._test_live_migration(side_effect=None)

    def test_live_migration_exception(self):
        self._test_live_migration(side_effect=Exception)

    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.ebs_root_in_block_devices')
    @mock.patch('nova.virt.hyperv.imagecache.ImageCache.get_cached_image')
    @mock.patch('nova.virt.hyperv.volumeops.VolumeOps'
                '.login_storage_targets')
    def test_pre_live_migration(self, mock_login_storage_targets,
                                mock_get_cached_image,
                                mock_ebs_root_in_block_devices):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        mock_ebs_root_in_block_devices.return_value = None
        self._livemigrops.pre_live_migration(context=mock_context,
                                             instance=mock_instance,
                                             block_device_info='block info',
                                             network_info='network info')
        check_config = self.mock_livemigrutils().check_live_migration_config
        check_config.assert_called_once_with()
        CONF.set_override('use_cow_images', True)
        mock_ebs_root_in_block_devices.assert_called_once_with('block info')
        mock_get_cached_image.assert_called_once_with(mock_context,
                                                      mock_instance)
        mock_login_storage_targets.assert_called_once_with('block info')
