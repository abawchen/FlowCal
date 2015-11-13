#!/usr/bin/python
#
# gate.py - Module containing flow cytometry gate functions.
#
# All gate functions should be of the following form:
#
#     gated_data = gate(data, channels, parameters)
#     (gated_data, mask, contour, ...) = gate(data, channels, parameters,
#                                             full_output=True)
#
# where data is a NxD numpy array describing N cytometry events observing D
# data dimensions, channels specifies the channels in which to perform gating,
# and parameters are gate-specific parameters. gated_data is the gated result,
# mask is a Boolean array specifying the gate mask, and contour is an
# optional 2D numpy array of x-y coordinates tracing out line(s) which
# represent the gate (useful for plotting).
#
# Authors: John T. Sexton (john.t.sexton@rice.edu)
#          Sebastian M. Castillo-Hair (smc9@rice.edu)
# Date: 10/27/2015
#
# Requires:
#   * numpy
#   * scipy
#   * matplotlib

import numpy as np
import scipy.ndimage.filters
import matplotlib._cntr         # matplotlib contour, implemented in C
import collections

###
# namedtuple Gate Function Output Classes
###

StartEndGateOutput = collections.namedtuple(
    'StartEndGateOutput',
    ['gated_data', 'mask'])
HighLowGateOutput = collections.namedtuple(
    'HighLowGateOutput',
    ['gated_data', 'mask'])
EllipseGateOutput = collections.namedtuple(
    'EllipseGateOutput',
    ['gated_data', 'mask', 'contour'])
Density2dGateOutput = collections.namedtuple(
    'Density2dGateOutput',
    ['gated_data', 'mask', 'contour'])

###
# Gate Functions
###

def start_end(data, num_start = 250, num_end = 100, full_output=False):
    '''Gate out num_start first and num_end last events collected.

    data        - NxD FCSData object or numpy array
    num_start   - number of points to discard from the beginning of data
    num_end     - number of points to discard from the end of data
    full_output - flag specifying to return namedtuple with additional outputs

    returns     - (Gated MxD FCSData object or numpy array) or namedtuple with
                  (gated_data, mask)
    '''
    
    if data.shape[0] < (num_start + num_end):
        raise ValueError('Number of events to discard greater than total' + 
            ' number.')
    
    mask = np.ones(shape=data.shape[0],dtype=bool)
    mask[:num_start] = False
    mask[-num_end:] = False
    gated_data = data[mask]

    if full_output:
        return StartEndGateOutput(gated_data=gated_data, mask=mask)
    else:
        return gated_data

def high_low(data, channels=None, high=None, low=None, full_output=False):
    '''Gate out high and low values across all specified dimensions.

    For every i, if any value of data[i,channels] is less or equal than low, 
    or greater or equal than high, it will not be included in the final result.

    data        - NxD FCSData object or numpy array
    channels    - channels on which to perform gating
    high        - high value to discard (default = np.Inf if unable to extract
                  from input data)
    low         - low value to discard (default = -np.Inf if unable to extract
                  from input data)
    full_output - flag specifying to return namedtuple with additional outputs

    returns     - (Gated MxD FCSData object or numpy array) or namedtuple with
                  (gated_data, mask)
    '''
    
    # Extract channels in which to gate
    if channels is None:
        data_ch = data
    else:
        data_ch = data[:,channels]
        if data_ch.ndim == 1:
            data_ch = data_ch.reshape((-1,1))

    # Default values for high and low
    if high is None:
        if hasattr(data_ch, 'channel_info'):
            high = [data_ch[:, channel].channel_info[0]['bin_vals'][-1] 
                    for channel in data_ch.channels]
            high = np.array(high)
        else:
            high = np.Inf
    if low is None:
        if hasattr(data_ch, 'channel_info'):
            low = [data_ch[:, channel].channel_info[0]['bin_vals'][0]
                   for channel in data_ch.channels]
            low = np.array(low)
        else:
            low = -np.Inf

    # Gate
    mask = np.all((data_ch < high) & (data_ch > low), axis = 1)
    gated_data = data[mask]

    if full_output:
        return HighLowGateOutput(gated_data=gated_data, mask=mask)
    else:
        return gated_data

def ellipse(data, channels,
            center, a, b, theta=0,
            log=False, full_output=False):
    '''Gate that preserves events inside an ellipse-shaped region.

    Events are kept if they satisfy the following relationship:
        (x/a)**2 + (y/b)**2 <= 1
    where x and y are the coordinates of the event list, after substracting
    center and rotating by -theta. This is mathematically equivalent to
    maintaining the events inside an ellipse with major axis a, minor axis b,
    center at center, and tilted by theta.

    data        - NxD FCSData object or numpy array
    channels    - Channels on which to perform gating
    center      - Coordinates of the center of the ellipse
    a           - Major axis of the ellipse
    b           - Minor axis of the ellipse
    theta       - Angle of the ellipse
    log         - If True, apply log10 to the event list before gating.
    full_output - flag specifying to return namedtuple with additional outputs

    returns     - (Gated MxD FCSData object or numpy array) or namedtuple with
                  (gated_data, mask, contour)
    '''
    # Extract channels in which to gate
    assert len(channels) == 2, '2 channels should be specified.'
    data_ch = data[:,channels].view(np.ndarray)

    # Log if necessary
    if log:
        data_ch = np.log10(data_ch)

    # Center
    center = np.array(center)
    data_centered = data_ch - center

    # Rotate
    R = np.array([[np.cos(theta), np.sin(theta)],
                    [-np.sin(theta), np.cos(theta)]])
    data_rotated = np.dot(data_centered, R.T)

    # Generate mask
    mask = ((data_rotated[:,0]/a)**2 + (data_rotated[:,1]/b)**2 <= 1)

    # Gate
    data_gated = data[mask]

    if full_output:
        # Calculate contour
        t = np.linspace(0,1,100)*2*np.pi
        ci = np.array([a*np.cos(t), b*np.sin(t)]).T
        ci = np.dot(ci, R) + center
        if log:
            ci = 10**ci
        cntr = [ci]

        return EllipseGateOutput(
            gated_data=data_gated, mask=mask, contour=cntr)
    else:
        return data_gated

def density2d(data, channels=[0,1],
              bins=None, gate_fraction=0.65, sigma=10.0,
              full_output=False):
    '''Gate that preserves the points in the region with highest density.

    First, obtain a 2D histogram and blur it using a 2D Gaussian filter. Then
    normalize the resulting blurred histogram to make it a valid probability 
    mass function. Finally, gate out all but the points in the densest region
    (points with the largest probability).

    data            - NxD FCSData object or numpy array
    channels        - channels on which to perform gating
    bins            - bins argument to numpy.histogram2d. Autogenerate if None.
    gate_fraction   - fraction of data points to keep
    sigma           - standard deviation for Gaussian kernel
    full_output     - flag specifying to return namedtuple with additional
                      outputs

    returns         - (Gated MxD FCSData object or numpy array) or namedtuple
                      with (gated_data, mask, contour)
    '''

    # Extract channels in which to gate
    assert len(channels) == 2, '2 channels should be specified.'
    data_ch = data[:,channels]
    if data_ch.ndim == 1:
        data_ch = data_ch.reshape((-1,1))

    # Check dimensions
    assert data_ch.ndim > 1, 'Data should have at least 2 dimensions'
    assert data_ch.shape[0] > 1, 'Data must have more than 1 event'

    # Extract default bins if necessary
    if bins is None and hasattr(data_ch, 'channel_info'):
        bins = np.array([data_ch.channel_info[0]['bin_edges'],
                            data_ch.channel_info[1]['bin_edges'],
                            ])

    # Make 2D histogram and get bins
    H,xe,ye = np.histogram2d(data_ch[:,0], data_ch[:,1], bins=bins)

    # Map each data point to its histogram bin.
    #
    # Note that the index returned by np.digitize is such that
    # bins[i-1] <= x < bins[i], whereas indexing the histogram will result in
    # the following: hist[i,j] = bin corresponding to
    # xedges[i] <= x < xedges[i+1] and yedges[i] <= y < yedges[i+1].
    # Therefore, we need to subtract 1 from the np.digitize result to be able
    # to index into the appropriate bin in the histogram.
    ix = np.digitize(data_ch[:,0], bins=xe) - 1
    iy = np.digitize(data_ch[:,1], bins=ye) - 1

    # In the current version of numpy, there exists a disparity in how
    # np.histogram and np.digitize treat the rightmost bin edge (np.digitize
    # is not the strict inverse of np.histogram). Specifically, np.histogram
    # treats the rightmost bin interval as fully closed (rightmost bin edge is
    # included in rightmost bin), whereas np.digitize treats all bins as
    # half-open (you can specify which side is closed and which side is open;
    # `right` parameter). The expected behavior for this gating function is to
    # mimic np.histogram behavior, so we must reconcile this disparity.
    ix[data_ch[:,0] == xe[-1]] = len(xe)-2
    iy[data_ch[:,1] == ye[-1]] = len(ye)-2

    # Create a 2D array of lists corresponding to the 2D histogram to
    # accumulate events associated with each bin.
    filler = np.frompyfunc(lambda x: list(), 1, 1)
    Hi = np.empty_like(H, dtype=np.object)
    filler(Hi, Hi)

    x_outliers = (-1, len(xe)-1)    # Ignore (gate out) points which exist
    y_outliers = (-1, len(ye)-1)    # outside specified bins.
                                    # `np.digitize()-1` will assign points
                                    # less than `bins` to bin "-1" and points
                                    # greater than `bins` to len(bins)-1.

    N = 0   # Keep track of total number of events after implicit gating

    for i, (xi, yi) in enumerate(zip(ix, iy)):
        if (xi not in x_outliers and yi not in y_outliers):
            Hi[xi, yi].append(i)
            N = N+1

    # Determine number of points to keep. Only consider points which have not
    # been thrown out as outliers.
    n = int(np.ceil(gate_fraction*float(N)))

    # Blur 2D histogram
    sH = scipy.ndimage.filters.gaussian_filter(
        H,
        sigma=sigma,
        order=0,
        mode='constant',
        cval=0.0,
        truncate=6.0)

    # Normalize filtered histogram to make it a valid probability mass function
    D = sH / np.sum(sH)

    # Sort each (x,y) point by density
    vD = D.ravel()
    vH = H.ravel()
    sidx = np.argsort(vD)[::-1]
    svH = vH[sidx]  # linearized counts array sorted by density

    # Find minimum number of accepted (x,y) points needed to reach specified
    # number of data points
    csvH = np.cumsum(svH)
    Nidx = np.nonzero(csvH >= n)[0][0]    # we want to include this index

    # Get indices of events to keep
    vHi = Hi.ravel()
    mask = vHi[sidx[:(Nidx+1)]]
    mask = np.array([item for sublist in mask for item in sublist])
    mask = np.sort(mask)
    gated_data = data[mask]

    if full_output:
        # Use matplotlib contour plotter (implemented in C) to generate contour(s)
        # at the probability associated with the last accepted point.
        x,y = np.meshgrid(xe[:-1], ye[:-1], indexing = 'ij')
        mpl_cntr = matplotlib._cntr.Cntr(x,y,D)
        tr = mpl_cntr.trace(vD[sidx[Nidx]])

        # trace returns a list of arrays which contain vertices and path codes
        # used in matplotlib Path objects (see http://stackoverflow.com/a/18309914
        # and the documentation for matplotlib.path.Path for more details). I'm
        # just going to make sure the path codes aren't unfamiliar and then extract
        # all of the vertices and pack them into a list of 2D contours.
        cntr = []
        num_cntrs = len(tr)/2
        for idx in xrange(num_cntrs):
            vertices = tr[idx]
            codes = tr[num_cntrs+idx]

            # I am only expecting codes 1 and 2 ('MOVETO' and 'LINETO' codes)
            if not np.all((codes==1)|(codes==2)):
                raise Exception('Contour error: unrecognized path code')

            cntr.append(vertices)

        return Density2dGateOutput(
            gated_data=gated_data, mask=mask, contour=cntr)
    else:
        return gated_data
