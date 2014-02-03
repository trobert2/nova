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
from nova.virt.hyperv import imagecache
from nova.virt.hyperv import vhdutils
from nova.virt.hyperv import vhdutilsv2
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
        self.vhdutils_patcher = mock.patch('nova.virt.hyperv.utilsfactory'
                                           '.get_vhdutils')

        self.mock_pathutils = self.pathutils_patcher.start()
        self.mock_vhdutils = self.vhdutils_patcher.start()

        self._imagecache = imagecache.ImageCache()

        super(HostOpsTestCase, self).setUp()

    def tearDown(self):
        self.vhdutils_patcher.stop()
        self.pathutils_patcher.stop()

        super(HostOpsTestCase, self).tearDown()

    def _test_validate_vhd_image(self, side_effect):
        fake_vhd_path = os.path.join('fake', 'path')
        self.mock_vhdutils().validate_vhd.side_effect = [side_effect]
        if side_effect is Exception:
            self.assertRaises(vmutils.HyperVException,
                              self._imagecache._validate_vhd_image,
                              fake_vhd_path)
        else:
            self._imagecache._validate_vhd_image(fake_vhd_path)
            self.mock_vhdutils().validate_vhd.assert_called_once_with(
                fake_vhd_path)

    def test_validate_vhd_image(self):
        self._test_validate_vhd_image(side_effect=None)

    def test_validate_vhd_image_exception(self):
        self._test_validate_vhd_image(side_effect=Exception)

    @mock.patch('nova.compute.flavors.extract_flavor')
    def _test_get_root_vhd_size_gb(self, mock_extract_flavor, ret_value):
        mock_instance = get_instance_mock(self.instance_data)
        mock_extract_flavor.return_value = ret_value
        response = self._imagecache._get_root_vhd_size_gb(
            instance=mock_instance)
        mock_extract_flavor.assert_called_once_with(mock_instance,
                                                    prefix='old_')
        if ret_value:
            self.assertEqual(response, ret_value['root_gb'])
        else:
            self.assertEqual(response, mock_instance['root_gb'])

    def test_get_root_vhd_size(self):
        ret_value = {'root_gb': 1}
        self._test_get_root_vhd_size_gb(ret_value=ret_value)

    def test_get_root_vhd_size_key_error(self):
        self._test_get_root_vhd_size_gb(ret_value={})

    @mock.patch('nova.virt.hyperv.imagecache.ImageCache._get_root_vhd_size_gb')
    def _test_resize_and_cache_vhd(self, mock_get_root_vhd_size_gb,
                                   instance_type, internal_size,
                                   side_effect=None):
        mock_instance = get_instance_mock(self.instance_data)
        fake_path = os.path.join('fake', 'path')
        fake_info = {'MaxInternalSize': 10}
        get_size = self.mock_vhdutils().get_internal_vhd_size_by_file_size
        self.mock_vhdutils().get_vhd_info.return_value = fake_info
        self.mock_vhdutils.mock_add_spec(instance_type)
        self.mock_vhdutils().resize_vhd.side_effect = [side_effect]
        mock_get_root_vhd_size_gb.return_value = internal_size
        get_size.return_value = internal_size
        self.mock_pathutils().exists.return_value = False
        if internal_size < 10:
            self.assertRaises(vmutils.HyperVException,
                              self._imagecache._resize_and_cache_vhd,
                              mock_instance, fake_path)
        elif side_effect is Exception:
            self.assertRaises(Exception,
                              self._imagecache._resize_and_cache_vhd,
                              mock_instance, fake_path)
        else:
            response = self._imagecache._resize_and_cache_vhd(
                instance=mock_instance, vhd_path=fake_path)
            self.mock_vhdutils().get_vhd_info.assert_called_once_with(
                fake_path)
            mock_get_root_vhd_size_gb.assert_called_once_with(mock_instance)
            if not isinstance(self.mock_vhdutils, vhdutilsv2.VHDUtilsV2):
                get_size.assert_called_once_with(fake_path,
                                                 internal_size * unit.Gi)
            vhd_path = os.path.join('fake',
                                    'path_' + str(internal_size))
            self.mock_pathutils().exists.assert_called_with(vhd_path)
            self.mock_pathutils().copyfile.assert_called_with(fake_path,
                                                              vhd_path)
            self.mock_vhdutils().resize_vhd.assert_called_once_with(
                vhd_path, internal_size * unit.Gi)
            self.assertEqual(response, vhd_path)

    def test_resize_and_cache_vhd(self):
        self._test_resize_and_cache_vhd(instance_type=vhdutils.VHDUtils,
                                        internal_size=11)

    def test_resize_and_cache_vhd_is_instance_v2(self):
        self._test_resize_and_cache_vhd(instance_type=vhdutilsv2.VHDUtilsV2,
                                        internal_size=11)

    def test_resize_and_cache_vhd_size_smaller(self):
        self._test_resize_and_cache_vhd(instance_type=vhdutils.VHDUtils,
                                        internal_size=9)

    def test_resize_and_cache_vhd_exception(self):
        self._test_resize_and_cache_vhd(instance_type=vhdutils.VHDUtils,
                                        internal_size=11,
                                        side_effect=Exception)

    @mock.patch('nova.virt.images.fetch')
    @mock.patch('nova.virt.hyperv.imagecache.ImageCache'
                '._resize_and_cache_vhd')
    def _test_get_cached_image(self, mock_resize_and_cache_vhd, mock_fetch,
                               exists, side_effect=None):
        mock_context = mock.MagicMock()
        mock_instance = get_instance_mock(self.instance_data)
        fake_dir = os.path.join('fake', 'dir')
        self.mock_pathutils().get_base_vhd_dir.return_value = fake_dir
        base_dir = os.path.join(fake_dir, mock_instance['image_ref'])
        self.mock_vhdutils().get_vhd_format.return_value = 'fake'
        self.mock_pathutils().rename.side_effect = side_effect
        mock_resize_and_cache_vhd.return_value = base_dir + '.vhd'
        if side_effect is Exception:
            self.mock_pathutils().exists.side_effect = [False, False]
            self.assertRaises(Exception, self._imagecache.get_cached_image,
                              mock_instance, mock_context)
        else:
            self.mock_pathutils().exists.return_value = exists
            response = self._imagecache.get_cached_image(
                instance=mock_instance, context=mock_context)
            self.mock_pathutils().get_base_vhd_dir.assert_called_once_with()
            if not exists:
                mock_fetch.assert_called_once_with(
                    mock_context, mock_instance['image_ref'], base_dir,
                    mock_instance['user_id'], mock_instance['project_id'])
                self.mock_vhdutils().get_vhd_format.assert_called_with(
                    base_dir)
                self.mock_pathutils().rename.assert_called_once_with(
                    base_dir, base_dir + '.fake')
                self.assertEqual(response, base_dir + '.fake')
            else:
                CONF.set_override('use_cow_images', True)
                mock_resize_and_cache_vhd.assert_called_once_with(
                    mock_instance, base_dir + '.vhd')
                self.assertEqual(response, base_dir + '.vhd')

    def test_get_cached_image(self):
        self._test_get_cached_image(exists=False)

    def test_get_cached_image_exists(self):
        self._test_get_cached_image(exists=True)

    def test_get_cached_image_exists_exception(self):
        self._test_get_cached_image(exists=True, side_effect=Exception)
