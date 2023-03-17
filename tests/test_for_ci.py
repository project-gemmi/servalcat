"""
Author: "Keitaro Yamashita, Garib N. Murshudov"
MRC Laboratory of Molecular Biology

This software is released under the
Mozilla Public License, version 2.0; see LICENSE.
"""
from __future__ import absolute_import, division, print_function, generators
import unittest
import json
import os
import sys
import pipes
from servalcat import utils
from servalcat import ext
root = os.path.abspath(os.path.dirname(__file__))

class TestCI(unittest.TestCase):
    def test_ext(self):
        pdbin = os.path.join(root, "biotin", "biotin_talos.pdb")
        st = utils.fileio.read_structure(pdbin)
        g = ext.Geometry(st)

        
if __name__ == '__main__':
    unittest.main()
