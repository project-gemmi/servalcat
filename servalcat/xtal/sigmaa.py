"""
Author: "Keitaro Yamashita, Garib N. Murshudov"
MRC Laboratory of Molecular Biology
    
This software is released under the
Mozilla Public License, version 2.0; see LICENSE.
"""
from __future__ import absolute_import, division, print_function, generators
import argparse
import gemmi
import numpy
import pandas
import scipy.special
import scipy.optimize
from servalcat.utils import logger
from servalcat import utils

"""
DFc = D(Fc,0 + D1 Fc,1 + D2 Fc,2 + ...)
The last Fc,n is bulk solvent contribution.
"""

def add_arguments(parser):
    parser.description = 'Sigma-A parameter estimation for crystallographic data'
    parser.add_argument('--hklin', required=True,
                        help='Input MTZ file')
    parser.add_argument('--labin', required=True,
                        help='MTZ column for F,SIGF')
    parser.add_argument('--model', required=True, nargs="+", action="append",
                        help='Input atomic model file(s)')
    parser.add_argument("-d", '--d_min', type=float)
    #parser.add_argument('--d_max', type=float)
    parser.add_argument('--nbins', type=int, default=20,
                        help="Number of bins (default: %(default)d)")
    parser.add_argument('-s', '--source', choices=["electron", "xray", "neutron"], default="xray")
    parser.add_argument('-o','--output_prefix', default="sigmaa",
                        help='output file name prefix (default: %(default)s)')
# add_arguments()

def parse_args(arg_list):
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return parser.parse_args(arg_list)
# parse_args()

def calc_abs_sum_Fc(Ds, Fcs):
    Fc = Fcs[0].copy()
    for i in range(1, len(Ds)): Fc += Ds[i] * Fcs[i]
    return numpy.abs(Fc)
# calc_abs_sum_Fc()

def deriv_DFc2_and_DFc_dDj(Ds, Fcs):
    """
    [(d/dDj D^2|Fc0,+sum(Dk * Fc,k)|^2,
      d/dDj D|Fc0,+sum(Dk * Fc,k)|), ....] for j = 0 .. N-1
    """
    Fc = Fcs[0].copy()
    for i in range(1, len(Ds)): Fc += Ds[i] * Fcs[i]
    
    ret = [(2 * Ds[0] * numpy.abs(Fc)**2, numpy.abs(Fc))]

    for j in range(1, len(Ds)):
        rsq = 2 * numpy.real(Fcs[j] * Fc.conj())
        ret.append((Ds[0]**2 * rsq,
                    Ds[0] * 0.5 / numpy.abs(Fc) * rsq))
    return ret
# deriv_DFc2_and_DFc_dDj()

def fom_acentric(Fo, varFo, Fcs, Ds, S, epsilon):
    Sigma = 2 * varFo + epsilon * S
    return gemmi.bessel_i1_over_i0(2 * Fo * Ds[0] * calc_abs_sum_Fc(Ds, Fcs) / Sigma)
# fom_acentric()

def fom_centric(Fo, varFo, Fcs, Ds, S, epsilon):
    Sigma = varFo + epsilon * S
    return numpy.tanh(Fo * Ds[0] * calc_abs_sum_Fc(Ds, Fcs) / Sigma)
# fom_centric()

def mlf_acentric(Fo, varFo, Fcs, Ds, S, epsilon):
    # https://doi.org/10.1107/S0907444911001314
    # eqn (4)
    Sigma = 2 * varFo + epsilon * S
    DFc = Ds[0] * calc_abs_sum_Fc(Ds, Fcs)
    ret = numpy.log(2) + numpy.log(Fo) - numpy.log(Sigma)
    ret += -(Fo**2 + DFc**2)/Sigma
    ret += gemmi.log_bessel_i0(2*Fo*DFc/Sigma)
    return -ret
# mlf_acentric()

def deriv_mlf_wrt_D_S_acentric(Fo, varFo, Fcs, Ds, S, epsilon):
    deriv = numpy.zeros(1+len(Ds))
    Sigma = 2 * varFo + epsilon * S
    Fo2 = Fo**2
    tmp = deriv_DFc2_and_DFc_dDj(Ds, Fcs)
    DFc = Ds[0] * tmp[0][1]
    i1_i0_x = gemmi.bessel_i1_over_i0(2*Fo*DFc/Sigma) # m
    for i, (sqder, der) in enumerate(tmp):
        deriv[i] = -numpy.sum(-sqder / Sigma + i1_i0_x * 2 * Fo * der / Sigma)
    
    deriv[-1] = -numpy.sum((-1/Sigma + (Fo2 + DFc**2 - i1_i0_x * 2 * Fo * DFc) / Sigma**2) * epsilon)
    return deriv
# deriv_mlf_wrt_D_S_acentric()

def mlf_centric(Fo, varFo, Fcs, Ds, S, epsilon):
    # https://doi.org/10.1107/S0907444911001314
    # eqn (4)
    Sigma = varFo + epsilon * S
    DFc = Ds[0] * calc_abs_sum_Fc(Ds, Fcs)
    ret = 0.5 * (numpy.log(2 / numpy.pi) - numpy.log(Sigma))
    ret += -0.5 * (Fo**2 + DFc**2) / Sigma
    ret += gemmi.log_cosh(Fo * DFc / Sigma)
    return -ret
# mlf_centric()

def deriv_mlf_wrt_D_S_centric(Fo, varFo, Fcs, Ds, S, epsilon):
    deriv = numpy.zeros(1+len(Ds))
    Sigma = varFo + epsilon * S
    Fo2 = Fo**2
    tmp = deriv_DFc2_and_DFc_dDj(Ds, Fcs)
    DFc = Ds[0] * tmp[0][1]
    tanh_x = numpy.tanh(Fo*DFc/Sigma)
    for i, (sqder, der) in enumerate(tmp):
        deriv[i] = -numpy.sum(-0.5 * sqder / Sigma + tanh_x * Fo * der / Sigma)
    deriv[-1] = -numpy.sum((-0.5 / Sigma + (0.5*(Fo2+DFc**2) - tanh_x * Fo*DFc)/Sigma**2)*epsilon)
    return deriv
# deriv_mlf_wrt_D_S_centric()

def mlf(df, Ds, S):
    ret = 0.
    func = (mlf_acentric, mlf_centric)
    for c, g in df.groupby("centric", sort=False):
        Fcs = [g["FC{}".format(i)].to_numpy() for i in range(len(Ds))]
        ret += numpy.sum(func[c](g.FP.to_numpy(), g.SIGFP.to_numpy()**2, Fcs, Ds, S, g.epsilon.to_numpy()))
    return ret
# mlf()

def deriv_mlf_wrt_D_S(df, Ds, S):
    ret = []
    func = (deriv_mlf_wrt_D_S_acentric, deriv_mlf_wrt_D_S_centric)
    for c, g in df.groupby("centric", sort=False):
        Fcs = [g["FC{}".format(i)].to_numpy() for i in range(len(Ds))]
        ret.append(func[c](g.FP.to_numpy(), g.SIGFP.to_numpy()**2, Fcs, Ds, S, g.epsilon.to_numpy()))
    return sum(ret)
# deriv_mlf_wrt_D_S()

def calc_fom(df, Ds, S):
    ret = pandas.Series(index=df.index)
    func = (fom_acentric, fom_centric)
    for c, g in df.groupby("centric", sort=False):
        Fcs = [g["FC{}".format(i)].to_numpy() for i in range(len(Ds))]
        ret[g.index] = func[c](g.FP.to_numpy(), g.SIGFP.to_numpy()**2, Fcs, Ds, S, g.epsilon.to_numpy())
    return ret
# calc_fom()

def write_mtz(hkldata, mtz_out):
    map_labs = "FWT", "DELFWT", "FC", "Fmask"
    other_labs = ["FOM", "FP"]
    other_types = ["W", "F"]
    data = numpy.empty((len(hkldata.df.index), len(map_labs)*2+len(other_labs)+3))
    data[:,:3] = hkldata.df[["H","K","L"]]
    for i, lab in enumerate(map_labs):
        data[:,3+i*2] = numpy.abs(hkldata.df[lab])
        data[:,3+i*2+1] = numpy.angle(hkldata.df[lab], deg=True)

    for i, lab in enumerate(other_labs):
        data[:,3+len(map_labs)*2+i] = hkldata.df[lab]
        
    mtz = gemmi.Mtz()
    mtz.spacegroup = hkldata.sg
    mtz.cell = hkldata.cell
    mtz.add_dataset('HKL_base')
    for label in ['H', 'K', 'L']: mtz.add_column(label, 'H')

    for lab in map_labs:
        mtz.add_column(lab, "F")
        mtz.add_column(("PH"+lab).replace("FWT", "WT"), "P")
    for lab, typ in zip(other_labs, other_types):
        mtz.add_column(lab, typ)

    mtz.set_data(data)
    mtz.write_to_file(mtz_out)

def determine_mlf_params(hkldata, nmodels):
    # Initial values
    hkldata.binned_df["D"] = 1.
    for i in range(1, nmodels):
        hkldata.binned_df["D{}".format(i)] = 1.

    hkldata.binned_df["S"] = 10000.
    for i_bin, idxes in hkldata.binned():
        FC = numpy.abs(hkldata.df.FC.to_numpy()[idxes])
        FP = hkldata.df.FP.to_numpy()[idxes]
        D = numpy.corrcoef(FP, FC)[1,0]
        hkldata.binned_df.loc[i_bin, "D"] = D
        hkldata.binned_df.loc[i_bin, "S"] = numpy.var(FP - D * FC)

    logger.write("Initial estimates:")
    logger.write(hkldata.binned_df.to_string())

    for i_bin, idxes in hkldata.binned():
        Ds = [hkldata.binned_df.D[i_bin]]
        for i in range(1, nmodels):
            Ds.append(hkldata.binned_df["D{}".format(i)][i_bin])

        S = hkldata.binned_df.S[i_bin]
        x0 = Ds + [S]
        def target(x):
            return mlf(hkldata.df.loc[idxes], x[:-1], x[-1])
        def grad(x):
            return deriv_mlf_wrt_D_S(hkldata.df.loc[idxes], x[:-1], x[-1])

        # test derivative
        if 0:
            gana = grad(x0)
            e = 1e-4
            for i in range(len(x0)):
                tmp = x0.copy()
                f0 = target(tmp)
                tmp[i] += e
                fe = target(tmp)
                gnum = (fe-f0)/e
                print("DERIV:", i, gnum, gana[i], gana[i]/gnum)

        #print("Bin", i_bin)
        res = scipy.optimize.minimize(fun=target, x0=x0, jac=grad)
        #print(res)
        
        hkldata.binned_df.loc[i_bin, "D"] = res.x[0]
        for i in range(1, nmodels):
            hkldata.binned_df.loc[i_bin, "D{}".format(i)] = res.x[i]
        hkldata.binned_df.loc[i_bin, "S"] = res.x[-1]

    logger.write("Refined estimates:")
    logger.write(hkldata.binned_df.to_string())
# determine_mlf_params()

def merge_models(sts): # simply merge models. no fix in chain ids etc.
    model = gemmi.Model("1")
    for st in sts:
        for m in st:
            for c in m:
                model.add_chain(c)
    return model
# merge_models()

# TODO isotropize map
# TODO add missing reflections
def main(args):
    if args.nbins < 1:
        logger.error("--nbins must be > 0")
        return

    args.model = sum(args.model, [])
    sts = []
    for xyzin in args.model:
        sts.append(utils.fileio.read_structure(xyzin))

    for st in sts[1:]:
        if st.cell.parameters != sts[0].cell.parameters:
            logger.write("WARNING: resetting cell to 1st model.")
            st.cell = sts[0].cell
        if st.find_spacegroup() != sts[0].find_spacegroup():
            logger.write("WARNING: resetting space group to 1st model.")
            st.spacegroup_hm = sts[0].spacegroup_hm
        
    nmodels = len(sts) + 1 # bulk
    mtz = gemmi.read_mtz_file(args.hklin)
    d_min = args.d_min
    if d_min is None: d_min = mtz.resolution_high()
    labin = args.labin.split(",")
    assert len(labin) == 2
    scaleto = mtz.get_value_sigma(*labin)
    fp = mtz.get_float(labin[0])
    sigfp = mtz.get_float(labin[1])

    logger.write("Calculating solvent contribution..")
    grid = gemmi.FloatGrid()
    grid.setup_from(sts[0], spacing=0.4)
    masker = gemmi.SolventMasker(gemmi.AtomicRadiiSet.Cctbx)
    masker.put_mask_on_float_grid(grid, merge_models(sts))
    fmask_asu = gemmi.transform_map_to_f_phi(grid).prepare_asu_data(dmin=d_min)

    # TODO no need to make multiple AsuData (just inefficient)
    fc_asu = [utils.model.calc_fc_fft(st, d_min, source=args.source, mott_bethe=args.source=="electron") for st in sts]
    if len(fc_asu) == 1:
        fc_asu_total = fc_asu[0]
    else:
        fc_asu_total = type(fc_asu[0])(fc_asu[0].unit_cell, fc_asu[0].spacegroup, fc_asu[0].miller_array, fc_asu[0].value_array)
        for asu in fc_asu[1:]:
            fc_asu_total.value_array[:] += asu.value_array
        
    logger.write("Scaling Fc..")
    scaling = gemmi.Scaling(sts[0].cell, sts[0].find_spacegroup())
    scaling.use_solvent = True
    scaling.prepare_points(fc_asu_total, scaleto, fmask_asu)
    scaling.fit_isotropic_b_approximately()
    scaling.fit_parameters()
    b_aniso = scaling.b_overall
    logger.write(" k_ov= {:.2e} B= {}".format(scaling.k_overall, b_aniso))

    # TODO 'merge' not needed; they must have same hkl array (really? what if missing data?)
    hkldata = utils.hkl.hkldata_from_asu_data(fp, "FP")
    hkldata.merge_asu_data(sigfp, "SIGFP")
    for i, asu in enumerate(fc_asu):
        hkldata.merge_asu_data(asu, "FC{}".format(i))
    hkldata.merge_asu_data(fmask_asu, "FC{}".format(nmodels-1)) # will become Fbulk

    overall_scale = scaling.get_overall_scale_factor(hkldata.miller_array())
    solvent_scale = scaling.get_solvent_scale(0.25 / hkldata.d_spacings()**2)
    hkldata.df["FC{}".format(nmodels-1)] *= solvent_scale
    for i in range(nmodels):
        hkldata.df["FC{}".format(i)] *= overall_scale
    
    # total
    hkldata.df["FC"] = 0j
    for i in range(nmodels):
        hkldata.df.FC += hkldata.df["FC{}".format(i)]

    fca = numpy.abs(hkldata.df.FC.to_numpy())
    fpa = hkldata.df.FP.to_numpy()
    logger.write(" CC(Fo,Fc)= {:.4f}".format(numpy.corrcoef(fca, fpa)[0,1]))
    logger.write(" Rcryst= {:.4f}".format(numpy.sum(numpy.abs(fca-fpa))/numpy.sum(fpa)))

    hkldata.calc_epsilon()
    hkldata.calc_centric()
    hkldata.setup_binning(n_bins=args.nbins)

    logger.write("Estimating sigma-A parameters..")
    determine_mlf_params(hkldata, nmodels)

    log_out = "{}.log".format(args.output_prefix)
    ofs = open(log_out, "w")
    ofs.write("""$TABLE: Statistics :
$GRAPHS
: log(Mn(|F|^2)) and variances :A:1,7,8,9,10:
: FOM :A:1,11,12:
: D :A:1,{Dns}:
: number of reflections :A:1,3,4:
$$
1/resol^2 bin n_a n_c d_max d_min log(Mn(|Fo|^2)) log(Mn(|Fc|^2)) log(Mn(|DFc|^2)) log(Sigma) FOM_a FOM_c {Ds} 
$$
$$
""".format(Dns=",".join(map(str, range(13, 13+nmodels))),
           Ds=" ".join(["D{}".format(i) if i > 0 else "D" for i in range(nmodels)])))
    tmpl = "{:.4f} {:3d} {:7d} {:7d} {:7.3f} {:7.3f} {:.4e} {:.4e} {:4e}"
    tmpl += "{: .4f} " * nmodels
    tmpl += "{: .4e} {:.4f} {:.4f}\n"

    hkldata.df["FWT"] = 0j
    hkldata.df["DELFWT"] = 0j
    hkldata.df["FOM"] = 0.
    for i_bin, idxes in hkldata.binned():
        bin_d_min = hkldata.binned_df.d_min[i_bin]
        bin_d_max = hkldata.binned_df.d_max[i_bin]
        Ds = [hkldata.binned_df["D{}".format(i) if i > 0 else "D"][i_bin] for i in range(nmodels)]
        S = hkldata.binned_df.S[i_bin]

        # 0: acentric 1: centric
        mean_fom = [0, 0]
        nrefs = [0, 0]
        fom_func = [fom_acentric, fom_centric]
        for c, g2 in hkldata.df.loc[idxes].groupby("centric", sort=False):
            Fcs = [g2["FC{}".format(i)].to_numpy() for i in range(len(Ds))]

            Fc = numpy.abs(g2.FC.to_numpy())
            phic = numpy.angle(g2.FC.to_numpy())
            expip = numpy.cos(phic) + 1j*numpy.sin(phic)
            Fo = g2.FP.to_numpy()
            
            m = fom_func[c](Fo, g2.SIGFP**2, Fcs, Ds, S, g2.epsilon.to_numpy())
            mean_fom[c] = numpy.mean(m)
            nrefs[c] = len(g2.index)

            DFc = Ds[0] * calc_abs_sum_Fc(Ds, Fcs)
            hkldata.df.loc[g2.index, "FOM"] = m
            hkldata.df.loc[g2.index, "DELFWT"] = (m * Fo - DFc ) * expip
            if c == 0:
                hkldata.df.loc[g2.index, "FWT"] = (2 * m * Fo - DFc) * expip
            else:
                hkldata.df.loc[g2.index, "FWT"] = (m * Fo) * expip
        
        ofs.write(tmpl.format(1/bin_d_min**2, i_bin, nrefs[0], nrefs[1], bin_d_max, bin_d_min,
                              numpy.log(numpy.average(numpy.abs(Fo)**2)),
                              numpy.log(numpy.average(numpy.abs(Fc)**2)),
                              numpy.log(numpy.average(DFc**2)),
                              numpy.log(S), mean_fom[0], mean_fom[1], *Ds)) # no python2 support!
    ofs.close()
    logger.write("output log: {}".format(log_out))
    
    hkldata.merge_asu_data(fmask_asu, "Fmask")
    mtz_out = args.output_prefix+".mtz"
    write_mtz(hkldata, mtz_out)
    logger.write("output mtz: {}".format(mtz_out))

    return hkldata
# main()
if __name__ == "__main__":
    import sys
    args = parse_args(sys.argv[1:])
    main(args)
