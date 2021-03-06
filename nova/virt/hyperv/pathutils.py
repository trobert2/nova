# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
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

import ctypes
import os
import shutil
import sys

if sys.platform == 'win32':
    import wmi

from nova.virt.hyperv import vmutils
from nova.openstack.common.gettextutils import _
from nova.openstack.common import log as logging
from nova import utils
from oslo.config import cfg

LOG = logging.getLogger(__name__)

hyperv_opts = [
    cfg.StrOpt('instances_path_share',
               default="",
               help='The name of a Windows share name mapped to the '
                    '"instances_path" dir and used by the resize feature '
                    'to copy files to the target host. If left blank, an '
                    'administrative share will be used, looking for the same '
                    '"instances_path" used locally'),
]

CONF = cfg.CONF
CONF.register_opts(hyperv_opts, 'hyperv')
CONF.import_opt('instances_path', 'nova.compute.manager')


class PathUtils(object):
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

    def __init__(self):
        self._vmutils = vmutils.VMUtils()
        self.smb_conn = wmi.WMI(moniker="root\Microsoft\Windows\SMB")

    def open(self, path, mode):
        """Wrapper on __builtin__.open used to simplify unit testing."""
        import __builtin__
        return __builtin__.open(path, mode)

    def exists(self, path):
        return os.path.exists(path)

    def makedirs(self, path):
        os.makedirs(path)

    def remove(self, path):
        os.remove(path)

    def rename(self, src, dest):
        os.rename(src, dest)

    def is_symlink(self, path):
        """
        Test if a file is a symlink on python 2.x
        """
        if sys.version_info >= (3, 2):
            return os.path.islink(path)

        file_attr = ctypes.windll.kernel32.GetFileAttributesW(unicode(path))
        if os.path.isdir(path) and \
           (file_attr & self._FILE_ATTRIBUTE_REPARSE_POINT):
            return True
        else:
            return False

    def create_sym_link(self, link, target, target_is_dir=False):
        """
        If target_is_dir is True, a junction will be created.
        NOTE: Juctions only work on same filesystem.
        """
        SymLink = ctypes.windll.kernel32.CreateSymbolicLinkW
        SymLink.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
        )
        SymLink.restype = ctypes.c_ubyte
        retcode = SymLink(link, target, target_is_dir)
        if retcode == 0:
            raise WindowsError("Could not create symbolic link")
        return True

    def copyfile(self, src, dest):
        self.copy(src, dest)

    def copy(self, src, dest):
        # With large files this is 2x-3x faster than shutil.copy(src, dest),
        # especially when copying to a UNC target.
        # shutil.copyfileobj(...) with a proper buffer is better than
        # shutil.copy(...) but still 20% slower than a shell copy.
        # It can be replaced with Win32 API calls to avoid the process
        # spawning overhead.
        output, ret = utils.execute('cmd.exe', '/C', 'copy', '/Y', src, dest)
        if ret:
            raise IOError(_('The file copy from %(src)s to %(dest)s failed')
                           % {'src': src, 'dest': dest})

    def rmtree(self, path):
        shutil.rmtree(path)

    def get_instances_dir(self, remote_server=None):
        local_instance_path = os.path.normpath(CONF.instances_path)

        if remote_server:
            if CONF.hyperv.instances_path_share:
                path = CONF.hyperv.instances_path_share
            else:
                # Use an administrative share
                path = local_instance_path.replace(':', '$')
            return ('\\\\%(remote_server)s\\%(path)s' %
                {'remote_server': remote_server, 'path': path})
        else:
            return local_instance_path

    def _check_create_dir(self, path):
        if not self.exists(path):
            LOG.debug(_('Creating directory: %s') % path)
            self.makedirs(path)

    def _check_remove_dir(self, path):
        if self.exists(path):
            LOG.debug(_('Removing directory: %s') % path)
            self.rmtree(path)

    def _get_instances_sub_dir(self, dir_name, remote_server=None,
                               create_dir=True, remove_dir=False):
        instances_path = self.get_instances_dir(remote_server)
        path = os.path.join(instances_path, dir_name)
        if remove_dir:
            self._check_remove_dir(path)
        if create_dir:
            self._check_create_dir(path)
        return path

    def get_instance_migr_revert_dir(self, instance_name, create_dir=False,
                                     remove_dir=False):
        dir_name = '%s_revert' % instance_name
        return self._get_instances_sub_dir(dir_name, None, create_dir,
                                           remove_dir)

    def get_instance_dir(self, instance_name, remote_server=None,
                         create_dir=True, remove_dir=False):
        return self._get_instances_sub_dir(instance_name, remote_server,
                                           create_dir, remove_dir)

    def _lookup_vhd_path(self, instance_name, vhd_path_func):
        vhd_path = None
        for format_ext in ['vhd', 'vhdx']:
            test_path = vhd_path_func(instance_name, format_ext)
            if self.exists(test_path):
                vhd_path = test_path
                break
        return vhd_path

    def lookup_root_vhd_path(self, instance_name):
        return self._lookup_vhd_path(instance_name, self.get_root_vhd_path)

    def lookup_ephemeral_vhd_path(self, instance_name):
        return self._lookup_vhd_path(instance_name,
                                     self.get_ephemeral_vhd_path)

    def get_root_vhd_path(self, instance_name, format_ext):
        instance_path = self.get_instance_dir(instance_name)
        return os.path.join(instance_path, 'root.' + format_ext.lower())

    def get_ephemeral_vhd_path(self, instance_name, format_ext):
        instance_path = self.get_instance_dir(instance_name)
        return os.path.join(instance_path, 'ephemeral.' + format_ext.lower())

    def get_base_vhd_dir(self):
        return self._get_instances_sub_dir('_base')

    def get_export_dir(self, instance_name):
        dir_name = os.path.join('export', instance_name)
        return self._get_instances_sub_dir(dir_name, create_dir=True,
                                           remove_dir=True)

    def mount_smb(self, remote_path, local_path=None, options={}):
        """ Parameters:
                string LocalPath
                string RemotePath
                string UserName
                string Password
                boolean Persistent
                boolean SaveCredentials
                boolean HomeFolder
        """
        mappings = self.smb_conn.query("SELECT * from "
                                       "MSFT_SmbMapping "
                                       "WHERE RemotePath='%s'" %
                                       remote_path)
        if len(mappings) == 0:
            if local_path:
                options['LocalPath'] = local_path
            else:
                options['LocalPath'] = '*'
            options['RemotePath'] = remote_path

            (job_path,
             ret_val) = self.smb_conn.Msft_SmbMapping.Create(**options)
            self._vmutils.check_ret_val(ret_val, job_path)
