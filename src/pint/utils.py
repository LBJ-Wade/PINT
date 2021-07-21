"""Miscellaneous potentially-helpful functions."""
import re
from contextlib import contextmanager
from copy import deepcopy
from io import StringIO
from collections import OrderedDict

import astropy.constants as const
import astropy.coordinates as coords
import astropy.units as u
import astropy.coordinates.angles as angles
import numpy as np
import scipy.optimize.zeros as zeros
from astropy import log
from scipy.special import fdtrc

import pint.pulsar_ecliptic

__all__ = [
    "PosVel",
    "numeric_partial",
    "numeric_partials",
    "check_all_partials",
    "has_astropy_unit",
    "PrefixError",
    "split_prefixed_name",
    "taylor_horner",
    "taylor_horner_deriv",
    "open_or_use",
    "lines_of",
    "interesting_lines",
    "show_param_cov_matrix",
    "dmxparse",
    "dmxstats",
    "dmx_ranges_old",
    "dmx_ranges",
    "p_to_f",
    "pferrs",
    "weighted_mean",
    "ELL1_check",
    "mass_funct",
    "mass_funct2",
    "pulsar_mass",
    "companion_mass",
    "pulsar_age",
    "pulsar_edot",
    "pulsar_B",
    "pulsar_B_lightcyl",
    "FTest",
    "add_dummy_distance",
    "remove_dummy_distance",
    "calculate_random_models",
]


# Actual exported tools


class PosVel:
    """Position/Velocity class.

    The class is used to represent the 6 values describing position
    and velocity vectors.  Instances have 'pos' and 'vel' attributes
    that are numpy arrays of floats (and can have attached astropy
    units).  The 'pos' and 'vel' params are 3-vectors of the positions
    and velocities respectively.

    The coordinates are generally assumed to be aligned with ICRF (J2000),
    i.e. they are in an intertial, not earth-rotating frame

    The 'obj' and 'origin' components are strings that can optionally
    be used to specify names for endpoints of the vectors.  If present,
    addition/subtraction will check that vectors are being combined in
    a consistent way.

    Specifically, if two PosVel objects are added, the obj of one must
    equal the origin of the other (either way around). If the two
    vectors agree on both ends, then the result vector will choose the
    origin of the vector on the left.

    """

    def __init__(self, pos, vel, obj=None, origin=None):
        if len(pos) != 3:
            raise ValueError("Position vector has length %d instead of 3" % len(pos))
        if isinstance(pos, u.Quantity):
            self.pos = pos
        else:
            self.pos = np.asarray(pos)

        if len(vel) != 3:
            raise ValueError("Position vector has length %d instead of 3" % len(pos))
        if isinstance(vel, u.Quantity):
            self.vel = vel
        else:
            self.vel = np.asarray(vel)

        if len(self.pos.shape) != len(self.vel.shape):
            # FIXME: could broadcast them, but have to be careful
            raise ValueError(
                "pos and vel must have the same number of dimensions but are {} and {}".format(
                    self.pos.shape, self.vel.shape
                )
            )
        elif self.pos.shape != self.vel.shape:
            self.pos, self.vel = np.broadcast_arrays(self.pos, self.vel, subok=True)

        if bool(obj is None) != bool(origin is None):
            raise ValueError(
                "If one of obj and origin is specified, the other must be too."
            )
        self.obj = obj
        self.origin = origin
        # FIXME: what about dtype compatibility?

    def _has_labels(self):
        return (self.obj is not None) and (self.origin is not None)

    def __neg__(self):
        return PosVel(-self.pos, -self.vel, obj=self.origin, origin=self.obj)

    def __add__(self, other):
        obj = None
        origin = None
        if self._has_labels() and other._has_labels():
            # here we check that the addition "makes sense", ie the endpoint
            # of self is the origin of other (or vice-versa)
            if self.obj == other.origin:
                origin = self.origin
                obj = other.obj
            elif self.origin == other.obj:
                origin = other.origin
                obj = self.obj
            else:
                raise ValueError(
                    "Attempting to add incompatible vectors: "
                    + "%s->%s + %s->%s"
                    % (self.origin, self.obj, other.origin, other.obj)
                )

        return PosVel(
            self.pos + other.pos, self.vel + other.vel, obj=obj, origin=origin
        )

    def __sub__(self, other):
        return self.__add__(other.__neg__())

    def __str__(self):
        if self._has_labels():
            return (
                "PosVel("
                + str(self.pos)
                + ", "
                + str(self.vel)
                + " "
                + self.origin
                + "->"
                + self.obj
                + ")"
            )
        else:
            return "PosVel(" + str(self.pos) + ", " + str(self.vel) + ")"

    def __getitem__(self, k):
        """Allow extraction of slices of the contained arrays"""
        colon = slice(None, None, None)
        if isinstance(k, tuple):
            ix = (colon,) + k
        else:
            ix = (colon, k)
        return self.__class__(
            self.pos[ix], self.vel[ix], obj=self.obj, origin=self.origin
        )


def numeric_partial(f, args, ix=0, delta=1e-6):
    """Compute the partial derivative of f numerically.

    This uses symmetric differences to estimate the partial derivative
    of a function (that takes some number of numeric arguments and may
    return an array) with respect to one of its arguments.

    """
    # r = np.array(f(*args))
    args2 = list(args)
    args2[ix] = args[ix] + delta / 2.0
    r2 = np.array(f(*args2))
    args3 = list(args)
    args3[ix] = args[ix] - delta / 2.0
    r3 = np.array(f(*args3))
    return (r2 - r3) / delta


def numeric_partials(f, args, delta=1e-6):
    """Compute all the partial derivatives of f numerically.

    Returns a matrix of the partial derivative of every return value
    with respect to every input argument. f is assumed to take a flat list
    of numeric arguments and return a list or array of values.
    """
    r = [numeric_partial(f, args, i, delta) for i in range(len(args))]
    return np.array(r).T


def check_all_partials(f, args, delta=1e-6, atol=1e-4, rtol=1e-4):
    """Check the partial derivatives of a function that returns derivatives.

    The function is assumed to return a pair (values, partials), where
    partials is supposed to be a matrix of the partial derivatives of f
    with respect to all its arguments. These values are checked against
    numerical partial derivatives.
    """
    _, jac = f(*args)
    jac = np.asarray(jac)
    njac = numeric_partials(lambda *args: f(*args)[0], args, delta)

    try:
        np.testing.assert_allclose(jac, njac, atol=atol, rtol=rtol)
    except AssertionError:
        # print jac
        # print njac
        d = np.abs(jac - njac) / (atol + rtol * np.abs(njac))
        print("fail fraction:", np.sum(d > 1) / float(np.sum(d >= 0)))
        worst_ix = np.unravel_index(np.argmax(d.reshape((-1,))), d.shape)
        print("max fail:", np.amax(d), "at", worst_ix)
        print("jac there:", jac[worst_ix], "njac there:", njac[worst_ix])
        raise


def has_astropy_unit(x):
    """Test whether x has a unit attribute containing an astropy unit.

    This is useful, because different data types can still have units
    associated with them.

    """
    return hasattr(x, "unit") and isinstance(x.unit, u.core.UnitBase)


# Define prefix parameter pattern
prefix_pattern = [
    re.compile(r"^([a-zA-Z]*\d+[a-zA-Z]+)(\d+)$"),  # For the prefix like T2EFAC2
    re.compile(r"^([a-zA-Z]+)(\d+)$"),  # For the prefix like F12
    re.compile(r"^([a-zA-Z0-9]+_)(\d+)$"),  # For the prefix like DMXR1_3
    # re.compile(r'([a-zA-Z]\d[a-zA-Z]+)(\d+)'),  # for prefixes like PLANET_SHAPIRO2?
]


class PrefixError(ValueError):
    pass


def split_prefixed_name(name):
    """Split a prefixed name.

    Parameters
    ----------
    name : str
       Prefixed name

    Returns
    -------
    prefixPart : str
       The prefix part of the name
    indexPart : str
       The index part from the name
    indexValue : int
       The absolute index value

    Example
    -------

        >>> split_prefixed_name("DMX_0123")
        ('DMX_', '0123', 123)
        >>> split_prefixed_name("T2EFAC17")
        ('T2EFAC', '17', 17)
        >>> split_prefixed_name("F12")
        ('F', '12', 12)
        >>> split_prefixed_name("DMXR1_2")
        ('DMXR1_', '2', 2)
        >>> split_prefixed_name("PEPOCH")
        Traceback (most recent call last):
          File "<stdin>", line 1, in <module>
          File "pint/utils.py", line 406, in split_prefixed_name
            raise PrefixError("Unrecognized prefix name pattern '%s'." % name)
        pint.utils.PrefixError: Unrecognized prefix name pattern 'PEPOCH'.

    """
    for pt in prefix_pattern:
        try:
            prefix_part, index_part = pt.match(name).groups()
            break
        except AttributeError:
            continue
    else:
        raise PrefixError("Unrecognized prefix name pattern '%s'." % name)
    return prefix_part, index_part, int(index_part)


def taylor_horner(x, coeffs):
    """Evaluate a Taylor series of coefficients at x via the Horner scheme.

    For example, if we want: 10 + 3*x/1! + 4*x^2/2! + 12*x^3/3! with
    x evaluated at 2.0, we would do::

        In [1]: taylor_horner(2.0, [10, 3, 4, 12])
        Out[1]: 40.0

    Parameters
    ----------
    x: astropy.units.Quantity
        Input value; may be an array.
    coeffs: list of astropy.units.Quantity
        Coefficient array; must have length at least one. The coefficient in
        position ``i`` is multiplied by ``x**i``. Each coefficient should
        just be a number, not an array. The units should be compatible once
        multiplied by an appropriate power of x.

    Returns
    -------
    astropy.units.Quantity
        Output value; same shape as input. Units as inferred from inputs.
    """
    return taylor_horner_deriv(x, coeffs, deriv_order=0)


def taylor_horner_deriv(x, coeffs, deriv_order=1):
    """Evaluate the nth derivative of a Taylor series.

    For example, if we want: first order of (10 + 3*x/1! + 4*x^2/2! + 12*x^3/3!)
    with respect to x evaluated at 2.0, we would do::

        In [1]: taylor_horner_deriv(2.0, [10, 3, 4, 12], 1)
        Out[1]: 15.0

    Parameters
    ----------
    x: astropy.units.Quantity
        Input value; may be an array.
    coeffs: list of astropy.units.Quantity
        Coefficient array; must have length at least one. The coefficient in
        position ``i`` is multiplied by ``x**i``. Each coefficient should
        just be a number, not an array. The units should be compatible once
        multiplied by an appropriate power of x.
    deriv_order: int
        The order of the derivative to take (that is, how many times to differentiate).
        Must be non-negative.

    Returns
    -------
    astropy.units.Quantity
        Output value; same shape as input. Units as inferred from inputs.
    """
    result = 0.0
    if hasattr(coeffs[-1], "unit"):
        if not hasattr(x, "unit"):
            x = x * u.Unit("")
        result *= coeffs[-1].unit / x.unit
    der_coeffs = coeffs[deriv_order::]
    fact = len(der_coeffs)
    for coeff in der_coeffs[::-1]:
        result = result * x / fact + coeff
        fact -= 1.0
    return result


@contextmanager
def open_or_use(f, mode="r"):
    """Open a filename or use an open file.

    Specifically, if f is a string, try to use it as an argument to
    open. Otherwise just yield it. In particular anything that is not
    a subclass of ``str`` will be passed through untouched.

    """
    if isinstance(f, (str, bytes)):
        with open(f, mode) as fl:
            yield fl
    else:
        yield f


def lines_of(f):
    """Iterate over the lines of a file, an open file, or an iterator.

    If ``f`` is a string, try to open a file of that name. Otherwise
    treat it as an iterator and yield its values. For open files, this
    results in the lines one-by-one. For lists or other iterators it
    just yields them right through.

    """
    with open_or_use(f) as fo:
        for l in fo:
            yield l


def interesting_lines(lines, comments=None):
    """Iterate over lines skipping whitespace and comments.

    Each line has its whitespace stripped and then it is checked whether
    it .startswith(comments) . This means comments can be a string or
    a list of strings.

    """
    if comments is None:
        cc = ()
    elif isinstance(comments, (str, bytes)):
        cc = (comments,)
    else:
        cc = tuple(comments)
    for c in cc:
        cs = c.strip()
        if not cs or not c.startswith(cs):
            raise ValueError(
                "Unable to deal with comments that start with whitespace, "
                "but comment string {!r} was requested.".format(c)
            )
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if ln.startswith(cc):
            continue
        yield ln


def show_param_cov_matrix(matrix, params, name="Covariance Matrix", switchRD=False):
    """function to print covariance matrices in a clean and easily readable way

    :param matrix: matrix to be printed, should be square, list of lists
    :param params: name of the parameters in the matrix, list
    :param name: title to be printed above, default Covariance Matrix
    :param switchRD: if True, switch the positions of RA and DEC to match setup of TEMPO cov. matrices

    :return string to be printed

    DEPRECATED
    """

    warn(
        "This method is deprecated. Use `parameter_covariance_matrix.prettyprint()` instead of `show_param_cov_matrix()`",
        category=DeprecationWarning,
    )

    output = StringIO()
    matrix = deepcopy(matrix)
    try:
        RAi = params.index("RAJ")
    except:
        RAi = None
        switchRD = False
    params1 = []
    for param in params:
        if len(param) < 3:
            while len(param) != 3:
                param = " " + param
            params1.append(param)
        elif len(param) > 3:
            while len(param) != 3:
                param = param[:-1]
            params1.append(param)
        else:
            params1.append(param)
    if switchRD:
        # switch RA and DEC so cov matrix matches TEMPO
        params1[RAi : RAi + 2] = [params1[RAi + 1], params1[RAi]]
        i = 0
        while i < 2:
            RA = deepcopy(matrix[RAi])
            matrix[RAi] = matrix[RAi + 1]
            matrix[RAi + 1] = RA
            matrix = matrix.T
            i += 1
    output.write(name + " switch RD = " + str(switchRD) + "\n")
    output.write(" ")
    for param in params1:
        output.write("         " + param)
    i = j = 0
    while i < len(matrix):
        output.write("\n" + params1[i] + " :: ")
        while j <= i:
            num = matrix[i][j]
            if num < 0.001 and num > -0.001:
                output.write("{0: 1.2e}".format(num) + "   : ")
            else:
                output.write("  " + "{0: 1.2f}".format(num) + "   : ")
            j += 1
        i += 1
        j = 0
    output.write("\b:\n")
    contents = output.getvalue()
    output.close()
    return contents


def pmtot(model):
    """Compute and return the total proper motion from a model object

    Calculates total proper motion from the parameters of the model, in either
    equatorial or ecliptic coordinates.  Note that in both cases, pulsar timing
    codes define the proper motion in the longitude coordinate to be the
    the actual angular rate of change of position on the sky rather than the change in coordinate value,
    so PMRA = (d(RAJ)/dt)*cos(DECJ). This is different from the astrometry community where mu_alpha = d(alpha)/dt.
    Thus, we don't need to include cos(DECJ) or cos(ELAT) in our calculation.

    Returns
    -------
    pmtot : Quantity
        Returns total proper motion with units of u.mas/u.yr

    Raises
    ------
        AttributeError if no Astrometry component is found in the model
    """

    if "AstrometryEcliptic" in model.components.keys():
        return np.sqrt(model.PMELONG.quantity ** 2 + model.PMELAT.quantity ** 2).to(
            u.mas / u.yr
        )
    elif "AstrometryEquatorial" in model.components.keys():
        return np.sqrt(model.PMRA.quantity ** 2 + model.PMDEC.quantity ** 2).to(
            u.mas / u.yr
        )
    else:
        raise AttributeError("No Astrometry component found")


class dmxrange:
    """Internal class for building DMX ranges"""

    def __init__(self, lofreqs, hifreqs):
        """lofreqs and hifreqs are lists of MJDs that are in the low or high band respectively"""
        self.los = lofreqs
        self.his = hifreqs
        self.min = min(lofreqs + hifreqs) - 0.001 * u.d
        self.max = max(lofreqs + hifreqs) + 0.001 * u.d

    def sum_print(self):
        print(
            "{:8.2f}-{:8.2f} ({:8.2f}): NLO={:5d} NHI={:5d}".format(
                self.min.value,
                self.max.value,
                self.max - self.min,
                len(self.los),
                len(self.his),
            )
        )


def dmx_ranges_old(
    toas,
    divide_freq=1000.0 * u.MHz,
    offset=0.01 * u.d,
    max_diff=15.0 * u.d,
    verbose=False,
):
    """Compute initial DMX ranges for a set of TOAs

    This is a rudimentary translation of $TEMPO/utils/dmx_ranges/DMX_ranges2.py

    Parameters
    ----------
    divide_freq : Quantity, MHz
        Requires TOAs above and below this freq for a good DMX range
    offset : Quantity, days
        The buffer to include around each DMX range. Warning, may cause bins to overlap?!?
    max_diff : Quantity, days
        Maximum duration of a DMX bin
    verbose : bool
        If True, print out verbose information about the DMX ranges including par file lines.

    Returns
    -------
    mask : bool array
        Array with True for all TOAs that got assigned to a DMX bin
    component : TimingModel.Component object
        A DMX Component class with the DMX ranges included
    """
    import pint.models.parameter
    from pint.models.timing_model import Component

    MJDs = toas.get_mjds()
    freqs = toas.table["freq"]

    loMJDs = MJDs[freqs < divide_freq]
    hiMJDs = MJDs[freqs > divide_freq]
    # Round off the dates to 0.1 days and only keep unique values so we ignore closely spaced TOAs
    loMJDs = np.unique(loMJDs.round(1))
    hiMJDs = np.unique(hiMJDs.round(1))
    log.info("There are {} dates with freqs > {} MHz".format(len(hiMJDs), divide_freq))
    log.info(
        "There are {} dates with freqs < {} MHz\n".format(len(loMJDs), divide_freq)
    )

    DMXs = []

    good_his = set([])
    bad_los = []
    # Walk through all of the low freq obs
    for ii, loMJD in enumerate(loMJDs):
        # find all the high freq obs within max_diff days
        # of the low freq obs
        hi_close = hiMJDs[np.fabs(hiMJDs - loMJD) < max_diff]
        # and where they are closer to this loMJD compared to the
        # other nearby ones
        if ii > 0:
            diffs = np.fabs(hi_close - loMJD)
            lodiffs = np.fabs(hi_close - loMJDs[ii - 1])
            hi_close = hi_close[diffs < lodiffs]
        if ii < len(loMJDs) - 1:
            diffs = np.fabs(hi_close - loMJD)
            hidiffs = np.fabs(hi_close - loMJDs[ii + 1])
            hi_close = hi_close[diffs < hidiffs]
        if len(hi_close):  # add a DMXrange
            DMXs.append(dmxrange([loMJD], list(hi_close)))
            good_his = good_his.union(set(hi_close))
        else:
            bad_los.append(loMJD)

    bad_los = set(bad_los)
    saved_los = []
    # print bad_los
    # Now walk through the DMXs and see if we can't fit a bad_lo freq in
    for bad_lo in bad_los:
        absmindiff = 2 * max_diff
        ind = 0
        for ii, DMX in enumerate(DMXs):
            if (
                np.fabs(bad_lo - DMX.min) < max_diff
                and np.fabs(bad_lo - DMX.max) < max_diff
            ):
                mindiff = min(np.fabs(bad_lo - DMX.min), np.fabs(bad_lo - DMX.max))
                if mindiff < absmindiff:
                    absmindiff = mindiff
                    ind = ii
        if absmindiff < max_diff:
            # print DMXs[ind].min, DMXs[ind].max, bad_lo
            DMXs[ind].los.append(bad_lo)
            # update the min and max vals
            DMXs[ind].min = min(DMXs[ind].los + DMXs[ind].his)
            DMXs[ind].max = max(DMXs[ind].los + DMXs[ind].his)
            saved_los.append(bad_lo)

    # These are the low-freq obs we can't save
    bad_los -= set(saved_los)
    bad_los = sorted(list(bad_los))

    # These are the high-freq obs we can't save
    bad_his = set(hiMJDs) - good_his
    bad_his = sorted(list(bad_his))

    if verbose:
        print("\n These are the 'good' ranges for DMX and days are low/high freq:")
        for DMX in DMXs:
            DMX.sum_print()

        print("\nRemove high-frequency data from these days:")
        for hibad in bad_his:
            print("{:8.2f}".format(hibad.value))
        print("\nRemove low-frequency data from these days:")
        for lobad in bad_los:
            print("{:8.2f}".format(lobad.value))

        print("\n Enter the following in your parfile")
        print("-------------------------------------")
        print("DMX         {:.2f}".format(max_diff.value))
        oldmax = 0.0
        for ii, DMX in enumerate(DMXs):
            print("DMX_{:04d}      0.0       {}".format(ii + 1, 1))
            print("DMXR1_{:04d}      {:10.4f}".format(ii + 1, (DMX.min - offset).value))
            print("DMXR2_{:04d}      {:10.4f}".format(ii + 1, (DMX.max + offset).value))
            if DMX.min < oldmax:
                print("Ack!  This shouldn't be happening!")
            oldmax = DMX.max
    # Init mask to all False
    mask = np.zeros_like(MJDs.value, dtype=bool)
    # Mark TOAs as True if they are in any DMX bin
    for DMX in DMXs:
        mask[np.logical_and(MJDs > DMX.min - offset, MJDs < DMX.max + offset)] = True
    log.info("{} out of {} TOAs are in a DMX bin".format(mask.sum(), len(mask)))
    # Instantiate a DMX component
    dmx_class = Component.component_types["DispersionDMX"]
    dmx_comp = dmx_class()
    # Add parameters
    for ii, DMX in enumerate(DMXs):
        if ii == 0:
            # Already have DMX_0001 in component, so just set parameters
            dmx_comp.DMX_0001.value = 0.0
            dmx_comp.DMX_0001.frozen = False
            dmx_comp.DMXR1_0001.value = (DMX.min - offset).value
            dmx_comp.DMXR2_0001.value = (DMX.max + offset).value

        else:
            # Add the DMX parameters
            dmx_par = pint.models.parameter.prefixParameter(
                parameter_type="float",
                name="DMX_{:04d}".format(ii + 1),
                value=0.0,
                units=u.pc / u.cm ** 3,
                frozen=False,
            )
            dmx_comp.add_param(dmx_par, setup=True)

            dmxr1_par = pint.models.parameter.prefixParameter(
                parameter_type="mjd",
                name="DMXR1_{:04d}".format(ii + 1),
                value=(DMX.min - offset).value,
                units=u.d,
            )
            dmx_comp.add_param(dmxr1_par, setup=True)

            dmxr2_par = pint.models.parameter.prefixParameter(
                parameter_type="mjd",
                name="DMXR2_{:04d}".format(ii + 1),
                value=(DMX.max + offset).value,
                units=u.d,
            )
            dmx_comp.add_param(dmxr2_par, setup=True)
    # Validate component
    dmx_comp.validate()

    return mask, dmx_comp


def dmx_ranges(toas, divide_freq=1000.0 * u.MHz, binwidth=15.0 * u.d, verbose=False):
    """Compute initial DMX ranges for a set of TOAs

    This is an alternative algorithm for computing DMX ranges

    Parameters
    ----------
    divide_freq : Quantity, MHz
        Requires TOAs above and below this freq for a good DMX range
    offset : Quantity, days
        The buffer to include around each DMX range. Warning, may cause bins to overlap?!?
    max_diff : Quantity, days
        Maximum duration of a DMX bin
    verbose : bool
        If True, print out verbose information about the DMX ranges including par file lines.

    Returns
    -------
    mask : bool array
        Array with True for all TOAs that got assigned to a DMX bin
    component : TimingModel.Component object
        A DMX Component class with the DMX ranges included
    """
    import pint.models.parameter
    from pint.models.timing_model import Component

    MJDs = toas.get_mjds()
    freqs = toas.table["freq"].quantity

    DMXs = []

    prevbinR2 = MJDs[0] - 0.001 * u.d
    while True:
        # Consider all TOAs with times after the last bin up through a total span of binwidth
        # Get indexes that should be in this bin
        # If there are no more MJDs to process, we are done.
        if not np.any(MJDs > prevbinR2):
            break
        startMJD = MJDs[MJDs > prevbinR2][0]
        binidx = np.logical_and(MJDs > prevbinR2, MJDs <= startMJD + binwidth)
        if not np.any(binidx):
            break
        binMJDs = MJDs[binidx]
        binfreqs = freqs[binidx]
        loMJDs = binMJDs[binfreqs < divide_freq]
        hiMJDs = binMJDs[binfreqs >= divide_freq]
        # If we have freqs below and above the divide, this is a good bin
        if np.any(binfreqs < divide_freq) and np.any(binfreqs > divide_freq):
            DMXs.append(dmxrange(list(loMJDs), list(hiMJDs)))
        else:
            # These TOAs cannot be used
            pass
        prevbinR2 = binMJDs.max()

    if verbose:
        print(
            "\n These are the good DMX ranges with number of TOAs above/below the dividing freq:"
        )
        for DMX in DMXs:
            DMX.sum_print()

    # Init mask to all False
    mask = np.zeros_like(MJDs.value, dtype=bool)
    # Mark TOAs as True if they are in any DMX bin
    for DMX in DMXs:
        mask[np.logical_and(MJDs >= DMX.min, MJDs <= DMX.max)] = True
    log.info("{} out of {} TOAs are in a DMX bin".format(mask.sum(), len(mask)))
    # Instantiate a DMX component
    dmx_class = Component.component_types["DispersionDMX"]
    dmx_comp = dmx_class()
    # Add parameters
    for ii, DMX in enumerate(DMXs):
        if ii == 0:
            # Already have DMX_0001 in component, so just set parameters
            dmx_comp.DMX_0001.value = 0.0
            dmx_comp.DMX_0001.frozen = False
            dmx_comp.DMXR1_0001.value = DMX.min.value
            dmx_comp.DMXR2_0001.value = DMX.max.value

        else:
            # Add the DMX parameters
            dmx_par = pint.models.parameter.prefixParameter(
                parameter_type="float",
                name="DMX_{:04d}".format(ii + 1),
                value=0.0,
                units=u.pc / u.cm ** 3,
                frozen=False,
            )
            dmx_comp.add_param(dmx_par, setup=True)

            dmxr1_par = pint.models.parameter.prefixParameter(
                parameter_type="mjd",
                name="DMXR1_{:04d}".format(ii + 1),
                value=DMX.min.value,
                units=u.d,
            )
            dmx_comp.add_param(dmxr1_par, setup=True)

            dmxr2_par = pint.models.parameter.prefixParameter(
                parameter_type="mjd",
                name="DMXR2_{:04d}".format(ii + 1),
                value=DMX.max.value,
                units=u.d,
            )
            dmx_comp.add_param(dmxr2_par, setup=True)
    # Validate component
    dmx_comp.validate()

    return mask, dmx_comp


def dmxstats(fitter):
    """Run dmxparse in python using PINT objects and results.

    Based off dmxparse by P. Demorest (https://github.com/nanograv/tempo/tree/master/util/dmxparse)

    Parameters
    ----------
    fitter
        PINT fitter used to get timing residuals, must have already run GLS fit
    """

    model = fitter.model
    mjds = fitter.toas.get_mjds()
    freqs = fitter.toas.table["freq"]
    ii = 1
    while hasattr(model, "DMX_{:04d}".format(ii)):
        mmask = np.logical_and(
            mjds.value > getattr(model, "DMXR1_{:04d}".format(ii)).value,
            mjds.value < getattr(model, "DMXR2_{:04d}".format(ii)).value,
        )
        if np.any(mmask):
            mjds_in_bin = mjds[mmask]
            freqs_in_bin = freqs[mmask]
            span = (mjds_in_bin.max() - mjds_in_bin.min()).to(u.d)
            # Warning: min() and max() seem to strip the units
            freqspan = freqs_in_bin.max() - freqs_in_bin.min()
            print(
                "DMX_{:04d}: NTOAS={:5d}, MJDSpan={:14.4f}, FreqSpan={:8.3f}-{:8.3f}".format(
                    ii, mmask.sum(), span, freqs_in_bin.min(), freqs_in_bin.max()
                )
            )
        else:
            print(
                "DMX_{:04d}: NTOAS={:5d}, MJDSpan={:14.4f}, FreqSpan={:8.3f}-{:8.3f}".format(
                    ii, mmask.sum(), 0 * u.d, 0 * u.MHz, 0 * u.MHz
                )
            )
        ii += 1

    return


def dmxparse(fitter, save=False):
    """Run dmxparse in python using PINT objects and results.

    Based off dmxparse by P. Demorest (https://github.com/nanograv/tempo/tree/master/util/dmxparse)

    Parameters
    ----------
    fitter
        PINT fitter used to get timing residuals, must have already run GLS fit
    save : bool
        if True saves output to text file in the format of the TEMPO version.
        If not output save file is desired, save = False (which is the default)
        Output file name is dmxparse.out

    Returns
    -------
    dictionary

        dmxs : mean-subtraced dmx values

        dmx_verrs : dmx variance errors

        dmxeps : center mjds of the dmx bins

        r1s : lower mjd bounds on the dmx bins

        r2s : upper mjd bounds on the dmx bins

        bins : dmx bins

        mean_dmx : mean dmx value

        avg_dm_err : uncertainty in average dmx

    Raises
    ------
    RuntimeError : If the model has no DMX parameters, or if there is a parsing problem

    """
    # We get the DMX values, errors, and mjds (same as in getting the DMX values for DMX v. time)
    # Get number of DMX epochs
    dmx_epochs = []
    for p in fitter.model.params:
        if "DMX_" in p:
            dmx_epochs.append(p.split("_")[-1])
    # Check to make sure that there are DMX values in the model
    if not dmx_epochs:
        raise RuntimeError("No DMX values in model!")
    # Get DMX values (will be in units of 10^-3 pc cm^-3)
    DMX_keys = []
    DMXs = []
    DMX_Errs = []
    DMX_R1 = []
    DMX_R2 = []
    DMX_center_MJD = []
    mask_idxs = []
    for ii in dmx_epochs:
        DMX_keys.append("DMX_{:}".format(ii))
        DMXs.append(getattr(fitter.model, "DMX_{:}".format(ii)).value)
        mask_idxs.append(getattr(fitter.model, "DMX_{:}".format(ii)).frozen)
        DMX_Errs.append(getattr(fitter.model, "DMX_{:}".format(ii)).uncertainty_value)
        dmxr1 = getattr(fitter.model, "DMXR1_{:}".format(ii)).value
        dmxr2 = getattr(fitter.model, "DMXR2_{:}".format(ii)).value
        DMX_R1.append(dmxr1)
        DMX_R2.append(dmxr2)
        DMX_center_MJD.append((dmxr1 + dmxr2) / 2)
    DMXs = np.array(DMXs)
    DMX_Errs = np.array(DMX_Errs)
    DMX_R1 = np.array(DMX_R1)
    DMX_R2 = np.array(DMX_R2)
    DMX_center_MJD = np.array(DMX_center_MJD)
    # If any value need to be masked, do it
    if True in mask_idxs:
        log.warning(
            "Some DMX bins were not fit for, masking these bins for computation."
        )
        DMX_Errs = np.ma.array(DMX_Errs, mask=mask_idxs)
        DMX_keys_ma = np.ma.array(DMX_keys, mask=mask_idxs)
    else:
        DMX_keys_ma = None

    # Make sure that the fitter has a covariance matrix, otherwise return the initial values
    if hasattr(fitter, "parameter_covariance_matrix"):
        # now get the full parameter covariance matrix from pint
        # access by label name to make sure we get the right values
        # make sure they are sorted in ascending order
        cc = fitter.parameter_covariance_matrix.get_label_matrix(
            sorted(["DMX_" + x for x in dmx_epochs])
        )
        n = len(DMX_Errs) - np.sum(mask_idxs)
        # Find error in mean DM
        DMX_mean = np.mean(DMXs)
        DMX_mean_err = np.sqrt(cc.matrix.sum()) / float(n)
        # Do the correction for varying DM
        m = np.identity(n) - np.ones((n, n)) / float(n)
        cc = np.dot(np.dot(m, cc.matrix), m)
        DMX_vErrs = np.zeros(n)
        # We also need to correct for the units here
        for i in range(n):
            DMX_vErrs[i] = np.sqrt(cc[i, i])
        # If array was masked, we need to add values back in where they were masked
        if DMX_keys_ma is not None:
            # Only need to add value to DMX_vErrs
            DMX_vErrs = np.insert(DMX_vErrs, np.where(mask_idxs)[0], None)
    else:
        log.warning(
            "Fitter does not have covariance matrix, returning values from model"
        )
        DMX_mean = np.mean(DMXs)
        DMX_mean_err = np.mean(DMX_Errs)
        DMX_vErrs = DMX_Errs
    # Check we have the right number of params
    if len(DMXs) != len(DMX_Errs) or len(DMXs) != len(DMX_vErrs):
        log.error("ERROR! Number of DMX entries do not match!")
        raise RuntimeError("Number of DMX entries do not match!")

    # Output the results'
    if save:
        DMX = "DMX"
        lines = []
        lines.append("# Mean %s value = %+.6e \n" % (DMX, DMX_mean))
        lines.append("# Uncertainty in average %s = %.5e \n" % ("DM", DMX_mean_err))
        lines.append(
            "# Columns: %sEP %s_value %s_var_err %sR1 %sR2 %s_bin \n"
            % (DMX, DMX, DMX, DMX, DMX, DMX)
        )
        for k in range(len(dmx_epochs)):
            lines.append(
                "%.4f %+.7e %.3e %.4f %.4f %s \n"
                % (
                    DMX_center_MJD[k],
                    DMXs[k] - DMX_mean,
                    DMX_vErrs[k],
                    DMX_R1[k],
                    DMX_R2[k],
                    DMX_keys[k],
                )
            )
        with open("dmxparse.out", "w") as dmxout:
            dmxout.writelines(lines)
            dmxout.close()
    # return the new mean subtracted values
    mean_sub_DMXs = DMXs - DMX_mean

    # Get units to multiply returned arrays by
    DMX_units = getattr(fitter.model, "DMX_{:}".format(dmx_epochs[0])).units
    DMXR_units = getattr(fitter.model, "DMXR1_{:}".format(dmx_epochs[0])).units

    # define the output dictionary
    dmx = {}
    dmx["dmxs"] = mean_sub_DMXs * DMX_units
    dmx["dmx_verrs"] = DMX_vErrs * DMX_units
    dmx["dmxeps"] = DMX_center_MJD * DMXR_units
    dmx["r1s"] = DMX_R1 * DMXR_units
    dmx["r2s"] = DMX_R2 * DMXR_units
    dmx["bins"] = DMX_keys
    dmx["mean_dmx"] = DMX_mean * DMX_units
    dmx["avg_dm_err"] = DMX_mean_err * DMX_units

    return dmx


def weighted_mean(arrin, weights_in, inputmean=None, calcerr=False, sdev=False):
    """Compute weighted mean of input values

    Calculate the weighted mean, error, and optionally standard deviation of
    an input array.  By default error is calculated assuming the weights are
    1/err^2, but if you send calcerr=True this assumption is dropped and the
    error is determined from the weighted scatter.

    Parameters
    ----------
    arrin : array
    Array containing the numbers whose weighted mean is desired.
    weights: array
    A set of weights for each element in array. For measurements with
    uncertainties, these should be 1/sigma^2.
    inputmean: float, optional
        An input mean value, around which the mean is calculated.
    calcerr : bool, optional
        Calculate the weighted error.  By default the error is calculated as
        1/sqrt( weights.sum() ).  If calcerr=True it is calculated as
        sqrt((w**2 * (arr-mean)**2).sum() )/weights.sum().
    sdev : bool, optional
        If True, also return the weighted standard deviation as a third
        element in the tuple. Defaults to False.

    Returns
    -------
    wmean, werr: tuple
    A tuple of the weighted mean and error. If sdev=True the
    tuple will also contain sdev: wmean,werr,wsdev

    Notes
    -----
    Converted from IDL: 2006-10-23. Erin Sheldon, NYU
    Copied from PRESTO to PINT : 2020-04-18

    """
    arr = arrin
    weights = weights_in
    wtot = weights.sum()
    # user has input a mean value
    if inputmean is None:
        wmean = (weights * arr).sum() / wtot
    else:
        wmean = float(inputmean)
    # how should error be calculated?
    if calcerr:
        werr2 = (weights ** 2 * (arr - wmean) ** 2).sum()
        werr = np.sqrt(werr2) / wtot
    else:
        werr = 1.0 / np.sqrt(wtot)
    # should output include the weighted standard deviation?
    if sdev:
        wvar = (weights * (arr - wmean) ** 2).sum() / wtot
        wsdev = np.sqrt(wvar)
        return wmean, werr, wsdev
    else:
        return wmean, werr


def p_to_f(p, pd, pdd=None):
    """Converts P, Pdot to F, Fdot (or vice versa)

    Convert period, period derivative and period second
    derivative to the equivalent frequency counterparts.
    Will also convert from f to p.

    Parameters
    ----------
    p : astropy.units.Quantity
        pulsar period (or frequency)
    pd : astropy.units.Quantity
        period derivative (or frequency derivative)        
    pdd : astropy.units.Quantity, optional
        period second derivative (or frequency second derivative)

    Returns
    -------
    f : astropy.units.Quantity
        pulsar frequency (or period)
    fd : astropy.units.Quantity
        pulsar frequency derivative (or period derivative)
    fdd : astropy.units.Quantity
        frequency second derivative (or period second derivative) if pdd is supplied        
    """
    f = 1.0 / p
    fd = -pd / (p * p)
    if pdd is None:
        return [f, fd]
    else:
        if pdd == 0.0:
            fdd = 0.0
        else:
            fdd = 2.0 * pd * pd / (p ** 3.0) - pdd / (p * p)
        return [f, fd, fdd]


def pferrs(porf, porferr, pdorfd=None, pdorfderr=None):
    """Convert P, Pdot to F, Fdot with uncertainties.

    Calculate the period or frequency errors and
    the pdot or fdot errors from the opposite one.

    Parameters
    ----------
    porf : astropy.units.Quantity
        pulsar period (or frequency)
    porferr : astropy.units.Quantity
        pulsar period uncertainty (or frequency uncertainty)
    pdorfd : astropy.units.Quantity, optional
        pulsar period derivative (or frequency derivative)
    pdorfderr : astropy.units.Quantity, optional
        pulsar period derivative uncertainty (or frequency derivative uncertainty)

    Returns
    -------
    forp : astropy.units.Quantity
        pulsar frequency (or period)
    forperr : astropy.units.Quantity
        pulsar frequency uncertainty (or period uncertainty)
    fdorpd : astropy.units.Quantity
        pulsar frequency derivative (or period derivative) if pdorfd supplied
    fdorpderr : astropy.units.Quantity if pdorfd supplied
        pulsar frequency derivative uncertainty (or period derivative uncertainty)
    """
    if pdorfd is None:
        return [1.0 / porf, porferr / porf ** 2.0]
    else:
        forperr = porferr / porf ** 2.0
        fdorpderr = np.sqrt(
            (4.0 * pdorfd ** 2.0 * porferr ** 2.0) / porf ** 6.0
            + pdorfderr ** 2.0 / porf ** 4.0
        )
        [forp, fdorpd] = p_to_f(porf, pdorfd)
        return [forp, forperr, fdorpd, fdorpderr]


def pulsar_age(f, fdot, n=3, fo=1e99 * u.Hz):
    """Compute pulsar characteristic age

    Return the age of a pulsar given the spin frequency
    and frequency derivative.  By default, the characteristic age
    is returned (assuming a braking index `n` =3 and an initial
    spin frequency `fo` >> `f`).  But `n` and `fo` can be set.

    Parameters
    ----------
    f : astropy.units.Quantity
        pulsar frequency
    fdot : astropy.units.Quantity
        frequency derivative
    n : int, optional
        braking index (default = 3)
    fo : astropy.units.Quantity, optional
        initial frequency

    Returns
    -------
    age : astropy.units.Quantity
        pulsar age in ``u.yr``

    Notes
    -----
    Calculates ``(-f/(n-1)/fdot) * (1 - (f/fo)**(n-1))``
    """
    return (-f / ((n - 1.0) * fdot) * (1.0 - (f / fo) ** (n - 1.0))).to(u.yr)


def pulsar_edot(f, fdot, I=1.0e45 * u.g * u.cm ** 2):
    """Compute pulsar spindown energy loss rate

    Return the pulsar Edot (in erg/s) given the spin frequency `f` and
    frequency derivative `fdot`. The NS moment of inertia is assumed to be
    `I` = 1.0e45 g cm^2 by default.

    Parameters
    ----------
    f : astropy.units.Quantity
        pulsar frequency
    fdot : astropy.units.Quantity
        frequency derivative
    I : astropy.units.Quantity, optional
        pulsar moment of inertia, default of 1e45 g*cm**2

    Returns
    -------
    Edot : astropy.units.Quantity
        pulsar spin-down luminosity in ``u.erg/u.s``

    Notes
    -----
    Calculates ``-4 * np.pi**2 * I * f * fdot``
    """
    return (-4.0 * np.pi ** 2 * I * f * fdot).to(u.erg / u.s)


def pulsar_B(f, fdot):
    """Compute pulsar surface magnetic field

    Return the estimated pulsar surface magnetic field strength
    given the spin frequency and frequency derivative.

    Parameters
    ----------
    f : astropy.units.Quantity
        pulsar frequency
    fdot : astropy.units.Quantity
        frequency derivative

    Returns
    -------
    B : astropy.units.Quantity
        pulsar dipole magnetic field in ``u.G``

    Notes
    -----
    Calculates ``B=3.2e19 G * np.sqrt(-f/fdot**3)``
    """
    # This is a hack to use the traditional formula by stripping the units.
    # It would be nice to improve this to a  proper formula with units
    return 3.2e19 * u.G * np.sqrt(-fdot.to_value(u.Hz / u.s) / f.to_value(u.Hz) ** 3.0)


def pulsar_B_lightcyl(f, fdot):
    """Compute pulsar magnetic field at the light cylinder

    Return the estimated pulsar magnetic field strength at the
    light cylinder given the spin frequency and
    frequency derivative.

    Parameters
    ----------
    f : :class:`astropy.units.Quantity`
        pulsar frequency
    fdot : :class:`astropy.units.Quantity`
        frequency derivative

    Returns
    -------
    Blc : :class:`astropy.units.Quantity`
        pulsar dipole magnetic field at the light cylinder in ``u.G``
    """
    p, pd = p_to_f(f, fdot)
    # This is a hack to use the traditional formula by stripping the units.
    # It would be nice to improve this to a  proper formula with units
    return (
        2.9e8
        * u.G
        * p.to_value(u.s) ** (-5.0 / 2.0)
        * np.sqrt(pd.to(u.dimensionless_unscaled).value)
    )


def mass_funct(pb, x):
    """Compute binary mass function from period and semi-major axis

    Parameters
    ----------
    pb : :class:`astropy.units.Quantity`
        Binary period
    x : :class:`astropy.units.Quantity` in ``pint.ls``
        Semi-major axis, A1SINI, in units of ls

    Returns
    -------
    f_m : Quantity
        Mass function in `u.solMass`

    Raises
    ------
    ValueError
        If the input data are not appropriate quantities

    Notes
    -----
    Calculates ``4*np.pi**2 * x**3 / G / Pb**2``
    """
    if not isinstance(x, u.quantity.Quantity):
        raise ValueError(
            f"The projected semi-major axis x should be a Quantity but is {x}."
        )
    if not isinstance(pb, u.quantity.Quantity):
        raise ValueError(f"The binary period pb should be a Quantity but is {pb}.")
    fm = 4.0 * np.pi ** 2 * x ** 3 / (const.G * pb ** 2)
    return fm.to(u.solMass)


def mass_funct2(mp, mc, i):
    """Compute binary mass function from masses and inclination

    Parameters
    ----------
    mp : astropy.units.Quantity
        Pulsar mass, typically in ``u.solMass``
    mc : astropy.units.Quantity
        Companion mass, typically in ``u.solMass``
    i : astropy.coordinates.Angle
        Inclination angle, in ``u.deg`` or ``u.rad``

    Returns
    -------
    f_m : astropy.units.Quantity
        Mass function in ``u.solMass``

    Raises
    ------
    ValueError
        If the input data are not appropriate quantities

    Notes
    -----
    Inclination is such that edge on is ``i = 90*u.deg``
    An 'average' orbit has cos(i) = 0.5, or ``i = 60*u.deg``

    Calculates ``(mc*sin(i))**3 / (mc + mp)**2``
    """
    if not (isinstance(i, angles.Angle) or isinstance(i, u.quantity.Quantity)):
        raise ValueError(f"The inclination should be an Angle but is {i}.")
    if not isinstance(mc, u.quantity.Quantity):
        raise ValueError(f"The companion mass should be a Quantity but is {mc}.")
    if not isinstance(mp, u.quantity.Quantity):
        raise ValueError(f"The pulsar mass should be a Quantity but is {mp}.")

    return (mc * np.sin(i)) ** 3.0 / (mc + mp) ** 2.0


def pulsar_mass(pb, x, mc, inc):
    """Compute pulsar mass from orbital parameters

    Return the pulsar mass (in solar mass units) for a binary.

    Parameters
    ----------
    pb : astropy.units.Quantity
        Binary orbital period
    x : astropy.units.Quantity
        Projected pulsar semi-major axis (aka ASINI) in ``pint.ls``
    mc : astropy.units.Quantity
        Companion mass in ``u.solMass``
    inc : astropy.coordinates.Angle
        Inclination angle, in ``u.deg`` or ``u.rad``

    Returns
    -------
    mass : astropy.units.Quantity in ``u.solMass``

    Raises
    ------
    ValueError
        If the input data are not appropriate quantities

    Example
    -------
    >>> import pint
    >>> import pint.utils
    >>> from astropy import units as u
    >>> print(pint.utils.pulsar_mass(2*u.hr, .2*pint.ls, 0.5*u.Msun, 60*u.deg))
    7.6018341985817885 solMass


    Notes
    -------
    This forms a quadratic equation of the form:
    ca*Mp**2 + cb*Mp + cc = 0

    with:
    ca = massfunct
    cb = 2 * massfunct * mc
    cc = massfunct * mc ** 2 - (mc * sini) ** 3

    except the discriminant simplifies to:
    4 * massfunct * mc**3 * sini**3

    solve it directly
    this has to be the positive branch of the quadratic
    because the vertex is at -mc, so the negative branch will always be < 0

    """
    if not (isinstance(inc, angles.Angle) or isinstance(inc, u.quantity.Quantity)):
        raise ValueError(f"The inclination should be an Angle but is {inc}.")
    if not isinstance(x, u.quantity.Quantity):
        raise ValueError(
            f"The projected semi-major axis x should be a Quantity but is {x}."
        )
    if not isinstance(pb, u.quantity.Quantity):
        raise ValueError(f"The binary period pb should be a Quantity but is {pb}.")
    if not isinstance(mc, u.quantity.Quantity):
        raise ValueError(f"The companion mass should be a Quantity but is {mc}.")

    massfunct = mass_funct(pb, x)

    sini = np.sin(inc)
    ca = massfunct
    cb = 2 * massfunct * mc

    return ((-cb + np.sqrt(4 * massfunct * mc ** 3 * sini ** 3)) / (2 * ca)).to(u.Msun)


def companion_mass(pb, x, inc=60.0 * u.deg, mpsr=1.4 * u.solMass):
    """Commpute the companion mass from the orbital parameters

    Compute companion mass for a binary system from orbital mechanics,
    not Shapiro delay.

    Parameters
    ----------
    pb : astropy.units.Quantity
        Binary orbital period
    x : astropy.units.Quantity`
        Projected pulsar semi-major axis (aka ASINI) in ``pint.ls``
    inc : astropy.coordinates.Angle, optional
        Inclination angle, in ``u.deg`` or ``u.rad.`` Default is 60 deg.
    mpsr : astropy.units.Quantity, optional
        Pulsar mass in ``u.solMass``. Default is 1.4 Msun

    Returns
    -------
    mass : astropy.units.Quantity
        In ``u.solMass``

    Raises
    ------
    ValueError
        If the input data are not appropriate quantities

    Example
    -------
    >>> import pint
    >>> import pint.utils
    >>> from astropy import units as u
    >>> print(pint.utils.companion_mass(1*u.d, 2*pint.ls, inc=30*u.deg, mpsr=1.3*u.Msun))
    0.6363138973397279 solMass

    Notes
    -----
    This ends up as a a cubic equation of the form:
    a*Mc**3 + b*Mc**2 + c*Mc + d = 0

    a = np.sin(inc) ** 3
    b = -massfunct
    c = -2 * mpsr * massfunct
    d = -massfunct * mpsr ** 2

    To solve it we can use a direct calculation of the cubic roots:
    https://en.wikipedia.org/wiki/Cubic_equation#General_cubic_formula

    It's useful to look at the discriminant to understand the nature of the roots
    and make sure we get the right one

    https://en.wikipedia.org/wiki/Discriminant#Degree_3
    delta = (
        cb ** 2 * cc ** 2
        - 4 * ca * cc ** 3
        - 4 * cb ** 3 * cd
        - 27 * ca ** 2 * cd ** 2
        + 18 * ca * cb * cc * cd
    )
    if delta is < 0 then there is only 1 real root, and I think we do it correctly below
    and this should be < 0
    since this reduces to -27 * sin(i)**6 * massfunct**2 * mp**4 -4 * sin(i)**3 * massfunct**3 * mp**3
    so there is just 1 real root and we compute it below

    """
    if not (isinstance(inc, angles.Angle) or isinstance(inc, u.quantity.Quantity)):
        raise ValueError(f"The inclination should be an Angle but is {inc}.")
    if not isinstance(x, u.quantity.Quantity):
        raise ValueError(
            f"The projected semi-major axis x should be a Quantity but is {x}."
        )
    if not isinstance(pb, u.quantity.Quantity):
        raise ValueError(f"The binary period pb should be a Quantity but is {pb}.")
    if not isinstance(mpsr, u.quantity.Quantity):
        raise ValueError(f"The pulsar mass should be a Quantity but is {mpsr}.")

    massfunct = mass_funct(pb, x)

    # solution
    sini = np.sin(inc)
    a = sini ** 3
    # delta0 = b ** 2 - 3 * a * c
    # delta0 is always > 0
    delta0 = massfunct ** 2 + 6 * mpsr * massfunct * a
    # delta1 is always <0
    # delta1 = 2 * b ** 3 - 9 * a * b * c + 27 * a ** 2 * d
    delta1 = (
        -2 * massfunct ** 3
        - 18 * a * mpsr * massfunct ** 2
        - 27 * a ** 2 * massfunct * mpsr ** 2
    )
    # Q**2 is always > 0, so this is never a problem
    # in terms of complex numbers
    # Q = np.sqrt(delta1**2 - 4*delta0**3)
    Q = np.sqrt(
        108 * a ** 3 * mpsr ** 3 * massfunct ** 3
        + 729 * a ** 4 * mpsr ** 4 * massfunct ** 2
    )
    # this could be + or - Q
    # pick the - branch since delta1 is <0 so that delta1 - Q is never near 0
    Ccubed = 0.5 * (delta1 + Q)
    # try to get the real root
    C = np.sign(Ccubed) * np.fabs(Ccubed) ** (1.0 / 3)
    # note that the difference b**2 - 3*a*c should be strictly positive
    # so those shouldn't cancel
    # and then all three terms should have the same signs
    # since a>0, b<0, C<0, and delta0>0
    # the denominator will be near 0 only when sin(i) is ~0, but that's already a known problem
    x1 = massfunct / 3.0 / a - C / 3.0 / a - delta0 / 3.0 / a / C
    return x1.to(u.Msun)


def ELL1_check(A1, E, TRES, NTOA, outstring=True):
    """Check for validity of assumptions in ELL1 binary model

    Checks whether the assumptions that allow ELL1 to be safely used are
    satisfied. To work properly, we should have:
    asini/c * ecc**2 << timing precision / sqrt(# TOAs)
    or A1 * E**2 << TRES / sqrt(NTOA)

    Parameters
    ----------
    A1 : Quantity
        Projected semi-major axis (aka ASINI) in `pint.ls`
    E : Quantity (dimensionless)
        Eccentricity
    TRES : Quantity
        RMS TOA uncertainty
    NTOA : int
        Number of TOAs in the fit
    outstring : bool, optional

    Returns
    -------
    bool or str
        Returns True if ELL1 is safe to use, otherwise False.
        If outstring is True then returns a string summary instead.

    """
    lhs = A1 / const.c * E ** 2.0
    rhs = TRES / np.sqrt(NTOA)
    if outstring:
        s = "Checking applicability of ELL1 model -- \n"
        s += "    Condition is asini/c * ecc**2 << timing precision / sqrt(# TOAs) to use ELL1\n"
        s += "    asini/c * ecc**2    = {:.3g} \n".format(lhs.to(u.us))
        s += "    TRES / sqrt(# TOAs) = {:.3g} \n".format(rhs.to(u.us))
    if lhs * 50.0 < rhs:
        if outstring:
            s += "    Should be fine.\n"
            return s
        return True
    elif lhs * 5.0 < rhs:
        if outstring:
            s += "    Should be OK, but not optimal.\n"
            return s
        return True
    else:
        if outstring:
            s += "    *** WARNING*** Should probably use BT or DD instead!\n"
            return s
        return False


def shklovskii_factor(pmtot, D):
    """Compute magnitude of Shklovskii correction factor.

    Computes the Shklovskii correction factor, as defined in Eq 8.12 of Lorimer & Kramer (2005)
    This is the factor by which Pdot/P is increased due to the transverse velocity.
    Note that this affects both the measured spin period and the orbital period.
    If we call this Shklovskii acceleration a_s, then
        Pdot_intrinsic = Pdot_observed - a_s*P

    Parameters
    ----------
    pmtot : Quantity, typically units of u.mas/u.yr
        Total proper motion of the pulsar (system)
    D : Quantity, typically in units of u.kpc or u.pc
        Distance to the pulsar
    """
    # This uses the small angle approximation that sin(x) = x, so we need to
    # make our angle dimensionless.
    with u.set_enabled_equivalencies(u.dimensionless_angles()):
        a_s = (D * pmtot ** 2 / const.c).to(u.s ** -1)
    return a_s


def FTest(chi2_1, dof_1, chi2_2, dof_2):
    """Run F-test.

    Compute an F-test to see if a model with extra parameters is
    significant compared to a simpler model.  The input values are the
    (non-reduced) chi^2 values and the numbers of DOF for '1' the
    original model and '2' for the new model (with more fit params).
    The probability is computed exactly like Sherpa's F-test routine
    (in Ciao) and is also described in the Wikipedia article on the
    F-test:  http://en.wikipedia.org/wiki/F-test
    The returned value is the probability that the improvement in
    chi2 is due to chance (i.e. a low probability means that the
    new fit is quantitatively better, while a value near 1 means
    that the new model should likely be rejected).

    Parameters
    -----------
    chi2_1 : Float
        Chi-squared value of model with fewer parameters
    dof_1 : Int
        Degrees of freedom of model with fewer parameters
    chi2_2 : Float
        Chi-squared value of model with more parameters
    dof_2 : Int
        Degrees of freedom of model with more parameters

    Returns
    --------
    ft : Float
        F-test significance value for the model with the larger number of
        components over the other.
    """
    delta_chi2 = chi2_1 - chi2_2
    if delta_chi2 > 0 and dof_1 != dof_2:
        delta_dof = dof_1 - dof_2
        new_redchi2 = chi2_2 / dof_2
        F = float((delta_chi2 / delta_dof) / new_redchi2)  # fdtr doesn't like float128
        ft = fdtrc(delta_dof, dof_2, F)
    elif dof_1 == dof_2:
        log.warning("Models have equal degrees of freedom, cannot perform F-test.")
        ft = np.nan
    elif delta_chi2 <= 0:
        log.warning(
            "Chi^2 for Model 2 is larger than Chi^2 for Model 1, cannot perform F-test."
        )
        ft = 1.0
    else:
        raise ValueError(
            f"Mystery problem in Ftest - maybe NaN? {chi2_1} {dof_1} {chi2_2} {dof_2}"
        )
    return ft


def add_dummy_distance(c, distance=1 * u.kpc):
    """Adds a dummy distance to a SkyCoord object for applying proper motion

    Parameters
    ----------
    c: astropy.coordinates.sky_coordinate.SkyCoord
        current SkyCoord object without distance but with proper motion and obstime
    distance: astropy.units.Quantity, optional
        distance to supply

    Returns
    -------
    cnew : astropy.coordinates.sky_coordinate.SkyCoord
        new SkyCoord object with a distance attached
    """

    if c.frame.data.differentials == {}:
        log.warning(
            "No proper motions available for %r: returning coordinates unchanged" % c
        )
        return c
    if c.obstime is None:
        log.warning("No obstime available for %r: returning coordinates unchanged" % c)
        return c

    if isinstance(c.frame, coords.builtin_frames.icrs.ICRS):
        if hasattr(c, "pm_ra_cosdec"):
            cnew = coords.SkyCoord(
                ra=c.ra,
                dec=c.dec,
                pm_ra_cosdec=c.pm_ra_cosdec,
                pm_dec=c.pm_dec,
                obstime=c.obstime,
                distance=distance,
                frame=coords.ICRS,
            )
        else:
            # it seems that after applying proper motions
            # it changes the RA pm to pm_ra instead of pm_ra_cosdec
            # although the value seems the same
            cnew = coords.SkyCoord(
                ra=c.ra,
                dec=c.dec,
                pm_ra_cosdec=c.pm_ra,
                pm_dec=c.pm_dec,
                obstime=c.obstime,
                distance=distance,
                frame=coords.ICRS,
            )

        return cnew
    elif isinstance(c.frame, coords.builtin_frames.galactic.Galactic):
        cnew = coords.SkyCoord(
            l=c.l,
            b=c.b,
            pm_l_cosb=c.pm_l_cosb,
            pm_b=c.pm_b,
            obstime=c.obstime,
            distance=distance,
            frame=coords.Galactic,
        )
        return cnew
    elif isinstance(c.frame, pint.pulsar_ecliptic.PulsarEcliptic):
        cnew = coords.SkyCoord(
            lon=c.lon,
            lat=c.lat,
            pm_lon_coslat=c.pm_lon_coslat,
            pm_lat=c.pm_lat,
            obstime=c.obstime,
            distance=distance,
            obliquity=c.obliquity,
            frame=pint.pulsar_ecliptic.PulsarEcliptic,
        )
        return cnew
    else:
        log.warning(
            "Do not know coordinate frame for %r: returning coordinates unchanged" % c
        )
        return c


def remove_dummy_distance(c):
    """Removes a dummy distance from a SkyCoord object after applying proper motion

    Parameters
    ----------
    c: astropy.coordinates.sky_coordinate.SkyCoord
        current SkyCoord object with distance and with proper motion and obstime

    Returns
    -------
    cnew : astropy.coordinates.sky_coordinate.SkyCoord
        new SkyCoord object with a distance removed
    """

    if c.frame.data.differentials == {}:
        log.warning(
            "No proper motions available for %r: returning coordinates unchanged" % c
        )
        return c
    if c.obstime is None:
        log.warning("No obstime available for %r: returning coordinates unchanged" % c)
        return c

    if isinstance(c.frame, coords.builtin_frames.icrs.ICRS):
        if hasattr(c, "pm_ra_cosdec"):

            cnew = coords.SkyCoord(
                ra=c.ra,
                dec=c.dec,
                pm_ra_cosdec=c.pm_ra_cosdec,
                pm_dec=c.pm_dec,
                obstime=c.obstime,
                frame=coords.ICRS,
            )
        else:
            # it seems that after applying proper motions
            # it changes the RA pm to pm_ra instead of pm_ra_cosdec
            # although the value seems the same
            cnew = coords.SkyCoord(
                ra=c.ra,
                dec=c.dec,
                pm_ra_cosdec=c.pm_ra,
                pm_dec=c.pm_dec,
                obstime=c.obstime,
                frame=coords.ICRS,
            )
        return cnew
    elif isinstance(c.frame, coords.builtin_frames.galactic.Galactic):
        cnew = coords.SkyCoord(
            l=c.l,
            b=c.b,
            pm_l_cosb=c.pm_l_cosb,
            pm_b=c.pm_b,
            obstime=c.obstime,
            frame=coords.Galactic,
        )
        return cnew
    elif isinstance(c.frame, pint.pulsar_ecliptic.PulsarEcliptic):
        cnew = coords.SkyCoord(
            lon=c.lon,
            lat=c.lat,
            pm_lon_coslat=c.pm_lon_coslat,
            pm_lat=c.pm_lat,
            obstime=c.obstime,
            obliquity=c.obliquity,
            frame=pint.pulsar_ecliptic.PulsarEcliptic,
        )
        return cnew
    else:
        log.warning(
            "Do not know coordinate frame for %r: returning coordinates unchanged" % c
        )
        return c


def calculate_random_models(fitter, toas, Nmodels=100, keep_models=True, params="all"):
    """
    Calculates random models based on the covariance matrix of the `fitter` object.

    returns the new phase differences compared to the original model
    optionally returns all of the random models

    Parameters
    ----------
    fitter: pint.fitter.Fitter
        current fitter object containing a model and parameter covariance matrix
    toas: pint.toa.TOAs
        TOAs to calculate models
    Nmodels: int, optional
        number of random models to calculate
    keep_models: bool, optional
        whether to keep and return the individual random models (slower)
    params: list, optional
        if specified, selects only those parameters to vary.  Default ('all') is to use all parameters other than Offset

    Returns
    -------
    dphase : np.ndarray
        phase difference with respect to input model, size is [Nmodels, len(toas)]
    random_models : list, optional
        list of random models (each is a :class:`pint.models.timing_model.TimingModel`)

    Note
    ----
    To calculate new TOAs, you can do:
        ``tnew = :func:~pint.toa.make_fake_toas(MJDmin, MJDmax, Ntoa, model=fitter.model)``
    or similar
    """
    Nmjd = len(toas)
    phases_i = np.zeros((Nmodels, Nmjd))
    phases_f = np.zeros((Nmodels, Nmjd))

    cov_matrix = fitter.parameter_covariance_matrix
    # this is a list of the parameter names in the order they appear in the coviarance matrix
    param_names = cov_matrix.get_label_names(axis=0)
    # this is a dictionary with the parameter values, but it might not be in the same order
    # and it leaves out the Offset parameter
    param_values = fitter.model.get_params_dict("free", "value")
    mean_vector = np.array([param_values[x] for x in param_names if not x == "Offset"])
    if params == "all":
        # remove the first column and row (absolute phase)
        if param_names[0] == "Offset":
            cov_matrix = cov_matrix.get_label_matrix(param_names[1:])
            fac = fitter.fac[1:]
            param_names = param_names[1:]
        else:
            fac = fitter.fac
    else:
        # only select some parameters
        # need to also select from the fac array and the mean_vector array
        idx, labels = cov_matrix.get_label_slice(params)
        cov_matrix = cov_matrix.get_label_matrix(params)
        index = idx[0].flatten()
        fac = fitter.fac[index]
        # except mean_vector does not have the 'Offset' entry
        # so may need to subtract 1
        if param_names[0] == "Offset":
            mean_vector = mean_vector[index - 1]
        else:
            mean_vector = mean_vector[index]
        param_names = cov_matrix.get_label_names(axis=0)

    f_rand = deepcopy(fitter)

    # scale by fac
    mean_vector = mean_vector * fac
    scaled_cov_matrix = ((cov_matrix.matrix * fac).T * fac).T
    random_models = []
    for imodel in range(Nmodels):
        # create a set of randomized parameters based on mean vector and covariance matrix
        rparams_num = np.random.multivariate_normal(mean_vector, scaled_cov_matrix)
        # scale params back to real units
        for j in range(len(mean_vector)):
            rparams_num[j] /= fac[j]
        rparams = OrderedDict(zip(param_names, rparams_num))
        f_rand.set_params(rparams)
        phase = f_rand.model.phase(toas, abs_phase=True)
        phases_i[imodel] = phase.int
        phases_f[imodel] = phase.frac
        if keep_models:
            random_models.append(f_rand.model)
            f_rand = deepcopy(fitter)
    phases = phases_i + phases_f
    phases0 = fitter.model.phase(toas, abs_phase=True)
    dphase = phases - (phases0.int + phases0.frac)
    if keep_models:
        return dphase, random_models
    else:
        return dphase


def list_parameters(class_=None):
    """List parameters understood by PINT.

    Parameters
    ----------
    class_: type, optional
        If provided, produce a list of parameters understood by the Component type; if None,
        return a list of parameters understood by all Components known to PINT.

    Returns
    -------
    list of dict
        Each entry is a dictionary describing one parameter. Dictionary values are all strings
        or lists of strings, and will include at least "name", "classes", and "description".
    """
    if class_ is not None:
        from pint.models.parameter import (
            boolParameter,
            strParameter,
            intParameter,
            prefixParameter,
            maskParameter,
        )

        result = []
        inst = class_()
        for p in inst.params:
            pm = getattr(inst, p)
            d = dict(
                name=pm.name,
                class_=f"{class_.__module__}.{class_.__name__}",
                description=pm.description,
            )
            if pm.aliases:
                d["aliases"] = [a for a in pm.aliases if a != pm.name]
            if pm.units:
                d["kind"] = pm.units.to_string()
                if not d["kind"]:
                    d["kind"] = "number"
            elif isinstance(pm, boolParameter):
                d["kind"] = "boolean"
            elif isinstance(pm, strParameter):
                d["kind"] = "string"
            elif isinstance(pm, intParameter):
                d["kind"] = "integer"
            if isinstance(pm, prefixParameter):
                d["name"] = pm.prefix + "{number}"
                d["aliases"] = [a + "{number}" for a in pm.prefix_aliases]
            if isinstance(pm, maskParameter):
                d["name"] = pm.origin_name + " {flag} {value}"
                d["aliases"] = [a + " {flag} {value}" for a in pm.prefix_aliases]
            if "aliases" in d and not d["aliases"]:
                del d["aliases"]
            result.append(d)
        return result
    else:
        import pint.models.timing_model

        results = {}
        ct = pint.models.timing_model.Component.component_types.copy()
        ct["TimingModel"] = pint.models.timing_model.TimingModel
        for v in ct.values():
            for d in list_parameters(v):
                n = d["name"]
                class_ = d.pop("class_")
                if n not in results:
                    d["classes"] = [class_]
                    results[n] = d
                else:
                    r = results[n].copy()
                    r.pop("classes")
                    if r != d:
                        raise ValueError(
                            f"Parameter {d} in class {class_} does not match {results[n]}"
                        )
                    results[n]["classes"].append(class_)
        return sorted(results.values(), key=lambda d: d["name"])
