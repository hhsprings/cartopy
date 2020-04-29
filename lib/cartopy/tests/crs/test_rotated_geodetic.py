# Copyright Cartopy Contributors
#
# This file is part of Cartopy and is released under the LGPL license.
# See COPYING and COPYING.LESSER in the root of the repository for full
# licensing details.
"""
Tests for the Transverse Mercator projection, including OSGB and OSNI.

"""

from __future__ import (absolute_import, division, print_function)

import cartopy.crs as ccrs
from .helpers import check_proj_params


common_other_args = {'o_proj=latlon', 'to_meter=0.0174532925199433'}


def test_default():
    geos = ccrs.RotatedPole(60, 50, 80)
    other_args = common_other_args | {'ellps=WGS84', 'lon_0=240', 'o_lat_p=50',
                                      'o_lon_p=80'}
    check_proj_params('ob_tran', geos, other_args)
