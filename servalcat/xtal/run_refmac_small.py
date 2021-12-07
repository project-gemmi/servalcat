"""
Author: "Keitaro Yamashita, Garib N. Murshudov"
MRC Laboratory of Molecular Biology
    
This software is released under the
Mozilla Public License, version 2.0; see LICENSE.
"""
from __future__ import absolute_import, division, print_function, generators
import gemmi
import numpy
import json
import os
import argparse
from servalcat.utils import logger
from servalcat import utils

def add_arguments(parser):
    parser.description = 'Run REFMAC5 for small molecule crystallography'
    parser.add_argument('--exe', default="refmac5", help='refmac5 binary')
    parser.add_argument('--cif', help="cif file containing model and data")
    parser.add_argument('--model', 
                        help='Input atomic model file')
    parser.add_argument('--hklin',
                        help='Input reflection file')
    parser.add_argument('--resolution',
                        type=float,
                        help='')
    parser.add_argument('--ncycle', type=int, default=10)
    #parser.add_argument('--jellybody', action='store_true')
    #parser.add_argument('--jellybody_params', nargs=2, type=float,
    #                    metavar=("sigma", "dmax"), default=[0.01, 4.2])
    parser.add_argument('--hout', action='store_true', help="write hydrogen atoms in the output model")

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--weight_auto_scale', type=float,
                       help="'weight auto' scale value. automatically determined from resolution and mask/box volume ratio if unspecified")
    group.add_argument('--weight_matrix', type=float,
                       help="weight matrix value")
    
    parser.add_argument('--bref', choices=["aniso","iso"], default="aniso")
    parser.add_argument('--unrestrained', action='store_true')
    parser.add_argument('-s', '--source', choices=["electron", "xray", "neutron"], default="electron") #FIXME
    parser.add_argument('--keywords', nargs='+', action="append",
                        help="refmac keyword(s)")
    parser.add_argument('--keyword_file', nargs='+', action="append",
                        help="refmac keyword file(s)")
    parser.add_argument('--external_restraints_json')
    parser.add_argument('--show_refmac_log', action='store_true')
    parser.add_argument('--output_prefix', default="refined",
                        help='output file name prefix')
    parser.add_argument("--monlib",
                        help="Monomer library path. Default: $CLIBD_MON")
# add_arguments()
                        
def parse_args(arg_list):
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return parser.parse_args(arg_list)
# parse_args()

def write_mtz(mtz_out, asudata, hklf):
    data = numpy.hstack((asudata.miller_array,
                         asudata.value_array["value"][:,numpy.newaxis],
                         asudata.value_array["sigma"][:,numpy.newaxis]))
    mtz = gemmi.Mtz()
    mtz.spacegroup = asudata.spacegroup
    mtz.cell = asudata.unit_cell
    mtz.add_dataset('HKL_base')
    for label in ['H', 'K', 'L']: mtz.add_column(label, 'H')

    if hklf == 3:
        mtz.add_column("F", "F")
        mtz.add_column("SIGF", "Q")
    else:
        mtz.add_column("I", "J")
        mtz.add_column("SIGI", "Q")

    mtz.set_data(data)
    mtz.write_to_file(mtz_out)
# write_mtz()
    
def main(args):
    if not args.cif and not (args.model and args.hklin):
        logger.error("Give [--model and --hklin] or --cif")
        return

    if args.cif:
        asudata, ss, info = utils.fileio.read_smcif_shelx(args.cif)
        st = utils.model.cx_to_mx(ss)
        mtz_in = "input.mtz"
        write_mtz(mtz_in, asudata, info.get("hklf"))
    else:
        st = utils.fileio.read_small_structure(args.model)
        logger.write(" Cell from model: {}".format(st.cell))
        logger.write(" Space group from model: {}".format(st.spacegroup_hm))

        if args.hklin.endswith(".mtz"): # TODO may be unmerged mtz
            mtz_in = args.hklin
            logger.write("Reading MTZ file: {}".format(mtz_in))
            mtz = gemmi.read_mtz_file(mtz_in)
            logger.write(" Cell from mtz: {}".format(mtz.cell))
            # TODO cell.approx can be used (next gemmi)
            if any([abs(a-b)>1e-3 for a,b in zip(mtz.cell.parameters,st.cell.parameters)]):
                logger.write(" Warning: unit cell mismatch!")
            logger.write(" Space group from mtz: {}".format(mtz.spacegroup.hm))
            if mtz.spacegroup != st.find_spacegroup():
                logger.write(" Warning: space group mismatch!")
        
        else:
            logger.error("Unsupported hkl file: {}".format(args.hklin))
            return

        st.cell = mtz.cell
        st.spacegroup_hm = mtz.spacegroup.hm

    if args.keyword_file:
        args.keyword_file = sum(args.keyword_file, [])
        for f in args.keyword_file:
            logger.write("Keyword file: {}".format(f))
            assert os.path.exists(f)
    else:
        args.keyword_file = []
            
    if args.keywords:
        args.keywords = sum(args.keywords, [])
    else:
        args.keywords = []


    # FIXME in some cases mtz space group should be modified. 
    utils.fileio.write_model(st, prefix="input", pdb=True, cif=True)

    if args.bref == "aniso":
        args.keywords.append("refi bref aniso")
        
    if args.unrestrained:
        args.keywords.append("refi type unre")
        args.hout = False
    else:
        monlib = utils.restraints.load_monomer_library(st, monomer_dir=args.monlib, #cif_files=args.ligand, 
                                                       stop_for_unknowns=False)#, check_hydrogen=(args.hydrogen=="yes"))

    # Run Refmac
    refmac = utils.refmac.Refmac(prefix="refined", global_mode="cx",
                                 exe=args.exe,
                                 source=args.source,
                                 monlib_path=args.monlib,
                                 xyzin="input.mmcif",
                                 hklin=mtz_in,
                                 ncycle=args.ncycle,
                                 weight_matrix=args.weight_matrix,
                                 weight_auto_scale=args.weight_auto_scale,
                                 hout=args.hout,
                                 resolution=args.resolution,
                                 keyword_files=args.keyword_file,
                                 keywords=args.keywords)
    #refmac.set_libin(args.ligand)
    refmac_summary = refmac.run_refmac()

if __name__ == "__main__":
    import sys
    args = parse_args(sys.argv[1:])
    main(args)
