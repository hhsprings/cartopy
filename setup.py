# Copyright Cartopy Contributors
#
# This file is part of Cartopy and is released under the LGPL license.
# See COPYING and COPYING.LESSER in the root of the repository for full
# licensing details.

# NOTE: This file must remain Python 2 compatible for the foreseeable future,
# to ensure that we error out properly for people with outdated setuptools
# and/or pip.
import sys

PYTHON_MIN_VERSION = (3, 5)

if sys.version_info < PYTHON_MIN_VERSION:
    error = """
Beginning with Cartopy 0.19, Python {} or above is required.
You are using Python {}.

This may be due to an out of date pip.

Make sure you have pip >= 9.0.1.
""".format('.'.join(str(n) for n in PYTHON_MIN_VERSION),
           '.'.join(str(n) for n in sys.version_info[:3]))
    sys.exit(error)


import fnmatch
import io
import os
import subprocess
import warnings
import distutils
import shlex
from collections import defaultdict
from distutils.spawn import find_executable
from distutils.sysconfig import get_config_var

from setuptools import Command, Extension, convert_path, setup

"""
Distribution definition for Cartopy.

"""

# The existence of a PKG-INFO directory is enough to tell us whether this is a
# source installation or not (sdist).
HERE = os.path.dirname(__file__)
IS_SDIST = os.path.exists(os.path.join(HERE, 'PKG-INFO'))
FORCE_CYTHON = os.environ.get('FORCE_CYTHON', False)

if not IS_SDIST or FORCE_CYTHON:
    import Cython
    if Cython.__version__ < '0.28':
        raise ImportError(
            "Cython 0.28+ is required to install cartopy from source.")

    from Cython.Distutils import build_ext as cy_build_ext


try:
    import numpy as np
except ImportError:
    raise ImportError('NumPy 1.10+ is required to install cartopy.')


# Please keep in sync with INSTALL file.
GEOS_MIN_VERSION = (3, 3, 3)
PROJ_MIN_VERSION = (4, 9, 0)


def file_walk_relative(top, remove=''):
    """
    Return a generator of files from the top of the tree, removing
    the given prefix from the root/file result.

    """
    top = top.replace('/', os.path.sep)
    remove = remove.replace('/', os.path.sep)
    for root, dirs, files in os.walk(top):
        for file in files:
            yield os.path.join(root, file).replace(remove, '')


def find_package_tree(root_path, root_package):
    """
    Return the package and all its sub-packages.

    Automated package discovery - extracted/modified from Distutils Cookbook:
    https://wiki.python.org/moin/Distutils/Cookbook/AutoPackageDiscovery

    """
    packages = [root_package]
    # Accept a root_path with Linux path separators.
    root_path = root_path.replace('/', os.path.sep)
    root_count = len(root_path.split(os.path.sep))
    for (dir_path, dir_names, _) in os.walk(convert_path(root_path)):
        # Prune dir_names *in-place* to prevent unwanted directory recursion
        for dir_name in list(dir_names):
            if not os.path.isfile(os.path.join(dir_path, dir_name,
                                               '__init__.py')):
                dir_names.remove(dir_name)
        if dir_names:
            prefix = dir_path.split(os.path.sep)[root_count:]
            packages.extend(['.'.join([root_package] + prefix + [dir_name])
                             for dir_name in dir_names])
    return packages


# Dependency checks
# =================

# GEOS
try:
    geos_version = subprocess.check_output(['geos-config', '--version'])
    geos_version = tuple(int(v) for v in geos_version.split(b'.')
                         if 'dev' not in str(v))
    geos_includes = subprocess.check_output(['geos-config', '--includes'])
    geos_clibs = subprocess.check_output(['geos-config', '--clibs'])
except (OSError, ValueError, subprocess.CalledProcessError):
    warnings.warn(
        'Unable to determine GEOS version. Ensure you have %s or later '
        'installed, or installation may fail.' % (
            '.'.join(str(v) for v in GEOS_MIN_VERSION), ))

    geos_includes = []
    geos_library_dirs = []
    geos_libraries = ['geos_c']
else:
    if geos_version < GEOS_MIN_VERSION:
        print('GEOS version %s is installed, but cartopy requires at least '
              'version %s.' % ('.'.join(str(v) for v in geos_version),
                               '.'.join(str(v) for v in GEOS_MIN_VERSION)),
              file=sys.stderr)
        exit(1)

    geos_includes = shlex.split(geos_includes.decode())
    geos_libraries = []
    geos_library_dirs = []
    for entry in shlex.split(geos_clibs.decode()):
        if entry.startswith('-L'):
            geos_library_dirs.append(entry[2:])
        elif entry.startswith('-l'):
            geos_libraries.append(entry[2:])


# Proj
def find_proj_version_if_no_pkgconfig(conda=None):
    if conda is not None:
        proj = find_executable('proj')
        if proj is None:
            print(
                'Proj {} must be installed.'.format(
                    '.'.join(str(v) for v in PROJ_MIN_VERSION)),
                file=sys.stderr)
            exit(1)

        if conda not in proj:
            print(
                'Proj {} must be installed in Conda environment "{}".'.format(
                    '.'.join(str(v) for v in PROJ_MIN_VERSION), conda),
                file=sys.stderr)
            exit(1)

    # If user doesn't use conda, we'll try detecting proj's version by
    # its header, assuming that user set required environment variables
    # such as "INCLUDE", etc. This is due to two reasons: there is a package
    # manager that doesn't provide a proj executable, like vcpkg on Windows,
    # and there is a python package called "proj" that has nothing to do with
    # projection.
    import tempfile

    tmpc = tempfile.mktemp(suffix=".c")
    cc = distutils.ccompiler.new_compiler()
    tmpbin = os.path.splitext(tmpc)[0]
    srcs = [r"""\
        #include <stdio.h>
        #include <proj.h>
        int main()
        {
            printf("%d.%d.%d",
                PROJ_VERSION_MAJOR, PROJ_VERSION_MINOR, PROJ_VERSION_PATCH);
        }""", r"""\
        #include <stdio.h>
        #define ACCEPT_USE_OF_DEPRECATED_PROJ_API_H
        #include <proj_api.h>
        int main()
        {
            printf("%d", PJ_VERSION);
        }"""
        ]
    try:
        io.open(tmpc, "w").write(srcs[0])
        objs = cc.compile([tmpc])
        cc.link_executable(objs, tmpbin)
        proj_version = tuple(
            map(int, subprocess.check_output(
                [tmpbin]).decode().strip().split(".")))
    except (
            OSError, ValueError,
            distutils.errors.CompileError,
            distutils.errors.DistutilsExecError,
            subprocess.CalledProcessError):
        try:
            io.open(tmpc, "w").write(srcs[1])
            objs = cc.compile([tmpc])
            cc.link_executable(objs, tmpbin)
            proj_version = tuple(
                map(int, list(subprocess.check_output(
                    [tmpbin]).decode().strip())))
        except (
                OSError, ValueError,
                distutils.errors.CompileError,
                distutils.errors.DistutilsExecError,
                subprocess.CalledProcessError):
            warnings.warn(
                'Unable to determine Proj version. Ensure you have %s '
                'or later installed, or installation may fail. '
                'If proj is installed but not detected, consider pre-setting '
                'environment variables (such as CFLAGS, INCLUDE, etc.) '
                'to make proj.h (or proj_api.h) visible for detection.' % (
                    '.'.join(str(v) for v in PROJ_MIN_VERSION), ))
            proj_version = (0, 0, 0)
    finally:
        os.remove(tmpc)
        ext = ""
        if cc.exe_extension:
            ext = cc.exe_extension
        if os.path.exists(tmpbin + ext):
            os.remove(tmpbin + ext)

    return proj_version


def get_proj_libraries():
    """
    This function gets the PROJ libraries to cythonize with
    """
    proj_libraries = ["proj"]
    if os.name == "nt" and (6, 0, 0) <= proj_version < (6, 3, 0):
        proj_libraries = [
            "proj_{}_{}".format(proj_version[0], proj_version[1])
        ]
    return proj_libraries


conda = os.getenv('CONDA_DEFAULT_ENV')
if conda is not None and conda in sys.prefix:
    # Conda does not provide pkg-config compatibility, but the search paths
    # should be set up so that nothing extra is required. We'll still check
    # the version, though.
    proj_version = find_proj_version_if_no_pkgconfig(conda)
    if proj_version < PROJ_MIN_VERSION:
        print(
            'Proj version %s is installed, but cartopy requires at least '
            'version %s.' % ('.'.join(str(v) for v in proj_version),
                             '.'.join(str(v) for v in PROJ_MIN_VERSION)),
            file=sys.stderr)
        exit(1)

    proj_includes = []
    proj_libraries = get_proj_libraries()
    proj_library_dirs = []

else:
    try:
        proj_version = subprocess.check_output(['pkg-config', '--modversion',
                                                'proj'],
                                               stderr=subprocess.STDOUT)
        proj_version = tuple(int(v) for v in proj_version.split(b'.'))
        proj_includes = subprocess.check_output(['pkg-config', '--cflags',
                                                 'proj'])
        proj_clibs = subprocess.check_output(['pkg-config', '--libs', 'proj'])
    except (OSError, ValueError, subprocess.CalledProcessError):
        proj_version = find_proj_version_if_no_pkgconfig()
        if proj_version < PROJ_MIN_VERSION:
            print(
                'Proj version %s is installed, but cartopy requires at least '
                'version %s.' % ('.'.join(str(v) for v in proj_version),
                                 '.'.join(str(v) for v in PROJ_MIN_VERSION)),
                file=sys.stderr)
            exit(1)

        proj_includes = []
        proj_libraries = get_proj_libraries()
        proj_library_dirs = []
    else:
        if proj_version < PROJ_MIN_VERSION:
            print(
                'Proj version %s is installed, but cartopy requires at least '
                'version %s.' % ('.'.join(str(v) for v in proj_version),
                                 '.'.join(str(v) for v in PROJ_MIN_VERSION)),
                file=sys.stderr)
            exit(1)

        proj_includes = [
            proj_include[2:] if proj_include.startswith('-I') else
            proj_include for proj_include in shlex.split(
                proj_includes.decode())]

        proj_libraries = []
        proj_library_dirs = []
        for entry in shlex.split(proj_clibs.decode()):
            if entry.startswith('-L'):
                proj_library_dirs.append(entry[2:])
            elif entry.startswith('-l'):
                proj_libraries.append(entry[2:])

# Python dependencies
extras_require = {}
for name in os.listdir(os.path.join(HERE, 'requirements')):
    with open(os.path.join(HERE, 'requirements', name)) as fh:
        section, ext = os.path.splitext(name)
        extras_require[section] = []
        for line in fh:
            if line.startswith('#'):
                pass
            elif line.startswith('-'):
                pass
            else:
                extras_require[section].append(line.strip())
install_requires = extras_require.pop('default')
tests_require = extras_require.get('tests', [])

# General extension paths
if sys.platform.startswith('win'):
    def get_config_var(name):
        return '.'
include_dir = get_config_var('INCLUDEDIR')
library_dir = get_config_var('LIBDIR')
extra_extension_args = defaultdict(list)
if not sys.platform.startswith('win'):
    extra_extension_args["runtime_library_dirs"].append(
        get_config_var('LIBDIR')
    )

# Description
# ===========
with open(os.path.join(HERE, 'README.md')) as fh:
    description = ''.join(fh.readlines())


cython_coverage_enabled = os.environ.get('CYTHON_COVERAGE', None)
if proj_version >= (6, 0, 0):
    extra_extension_args["define_macros"].append(
        ('ACCEPT_USE_OF_DEPRECATED_PROJ_API_H', '1')
    )
if cython_coverage_enabled:
    extra_extension_args["define_macros"].append(
        ('CYTHON_TRACE_NOGIL', '1')
    )

extensions = [
    Extension(
        'cartopy.trace',
        ['lib/cartopy/trace.pyx'],
        include_dirs=([include_dir, './lib/cartopy', np.get_include()] +
                      proj_includes + geos_includes),
        libraries=proj_libraries + geos_libraries,
        library_dirs=[library_dir] + proj_library_dirs + geos_library_dirs,
        language='c++',
        **extra_extension_args),
    Extension(
        'cartopy._crs',
        ['lib/cartopy/_crs.pyx'],
        include_dirs=[include_dir, np.get_include()] + proj_includes,
        libraries=proj_libraries,
        library_dirs=[library_dir] + proj_library_dirs,
        **extra_extension_args),
    # Requires proj v4.9
    Extension(
        'cartopy.geodesic._geodesic',
        ['lib/cartopy/geodesic/_geodesic.pyx'],
        include_dirs=[include_dir, np.get_include()] + proj_includes,
        libraries=proj_libraries,
        library_dirs=[library_dir] + proj_library_dirs,
        **extra_extension_args),
]


if cython_coverage_enabled:
    # We need to explicitly cythonize the extension in order
    # to control the Cython compiler_directives.
    from Cython.Build import cythonize

    directives = {'linetrace': True,
                  'binding': True}
    extensions = cythonize(extensions, compiler_directives=directives)


def decythonize(extensions, **_ignore):
    # Remove pyx sources from extensions.
    # Note: even if there are changes to the pyx files, they will be ignored.
    for extension in extensions:
        sources = []
        for sfile in extension.sources:
            path, ext = os.path.splitext(sfile)
            if ext in ('.pyx',):
                if extension.language == 'c++':
                    ext = '.cpp'
                else:
                    ext = '.c'
                sfile = path + ext
            sources.append(sfile)
        extension.sources[:] = sources
    return extensions


if IS_SDIST and not FORCE_CYTHON:
    extensions = decythonize(extensions)
    cmdclass = {}
else:
    cmdclass = {'build_ext': cy_build_ext}


# Main setup
# ==========
setup(
    name='Cartopy',
    url='https://scitools.org.uk/cartopy/docs/latest/',
    download_url='https://github.com/SciTools/cartopy',
    author='UK Met Office',
    description='A cartographic python library with Matplotlib support for '
                'visualisation',
    long_description=description,
    long_description_content_type='text/markdown',
    license="LGPLv3",
    keywords="cartography map transform projection proj proj.4 geos shapely "
             "shapefile",

    install_requires=install_requires,
    extras_require=extras_require,
    tests_require=tests_require,

    use_scm_version={
        'write_to': 'lib/cartopy/_version.py',
    },

    packages=find_package_tree('lib/cartopy', 'cartopy'),
    package_dir={'': 'lib'},
    package_data={'cartopy': list(file_walk_relative('lib/cartopy/tests/'
                                                     'mpl/baseline_images/',
                                                     remove='lib/cartopy/')) +
                  list(file_walk_relative('lib/cartopy/data/raster',
                                          remove='lib/cartopy/')) +
                  list(file_walk_relative('lib/cartopy/data/netcdf',
                                          remove='lib/cartopy/')) +
                  list(file_walk_relative('lib/cartopy/data/'
                                          'shapefiles/gshhs',
                                          remove='lib/cartopy/')) +
                  list(file_walk_relative('lib/cartopy/tests/lakes_shapefile',
                                          remove='lib/cartopy/')) +
                  ['io/srtm.npz']},

    scripts=['tools/cartopy_feature_download.py'],

    # requires proj headers
    ext_modules=extensions,
    cmdclass=cmdclass,
    python_requires='>=' + '.'.join(str(n) for n in PYTHON_MIN_VERSION),
    classifiers=[
            'Development Status :: 4 - Beta',
            'Framework :: Matplotlib',
            'License :: OSI Approved :: GNU Lesser General Public License v3 '
            'or later (LGPLv3+)',
            'Operating System :: MacOS :: MacOS X',
            'Operating System :: Microsoft :: Windows',
            'Operating System :: POSIX',
            'Operating System :: POSIX :: AIX',
            'Operating System :: POSIX :: Linux',
            'Programming Language :: C++',
            'Programming Language :: Python',
            'Programming Language :: Python :: 3',
            'Programming Language :: Python :: 3.5',
            'Programming Language :: Python :: 3.6',
            'Programming Language :: Python :: 3.7',
            'Programming Language :: Python :: 3.8',
            'Programming Language :: Python :: 3.9',
            'Programming Language :: Python :: 3 :: Only',
            'Topic :: Scientific/Engineering',
            'Topic :: Scientific/Engineering :: GIS',
            'Topic :: Scientific/Engineering :: Visualization',
          ],
)
