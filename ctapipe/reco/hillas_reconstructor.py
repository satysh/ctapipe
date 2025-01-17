"""
Line-intersection-based fitting.

Contact: Tino Michael <Tino.Michael@cea.fr>
"""


from ctapipe.reco.reco_algorithms import (
    Reconstructor,
    InvalidWidthException,
    TooFewTelescopesException,
)
from ctapipe.containers import ReconstructedGeometryContainer
from itertools import combinations

from ctapipe.coordinates import (
    CameraFrame,
    GroundFrame,
    TiltedGroundFrame,
    project_to_ground,
    MissingFrameAttributeWarning,
)
from astropy.coordinates import SkyCoord, spherical_to_cartesian, cartesian_to_spherical
import warnings

import numpy as np

from astropy import units as u

__all__ = ["HillasPlane", "HillasReconstructor"]


def angle(v1, v2):
    """ computes the angle between two vectors
        assuming cartesian coordinates

    Parameters
    ----------
    v1 : numpy array
    v2 : numpy array

    Returns
    -------
    the angle between v1 and v2 as a dimensioned astropy quantity
    """
    norm = np.linalg.norm(v1) * np.linalg.norm(v2)
    return np.arccos(np.clip(v1.dot(v2) / norm, -1.0, 1.0))


def normalise(vec):
    """ Sets the length of the vector to 1
        without changing its direction

    Parameters
    ----------
    vec : numpy array

    Returns
    -------
    numpy array with the same direction but length of 1
    """
    try:
        return vec / np.linalg.norm(vec)
    except ZeroDivisionError:
        return vec


def line_line_intersection_3d(uvw_vectors, origins):
    """
    Intersection of many lines in 3d.
    See https://en.wikipedia.org/wiki/Line%E2%80%93line_intersection
    """
    C = []
    S = []
    for n, pos in zip(uvw_vectors, origins):
        n = n.reshape((3, 1))
        norm_matrix = (n @ n.T) - np.eye(3)
        C.append(norm_matrix @ pos)
        S.append(norm_matrix)

    S = np.array(S).sum(axis=0)
    C = np.array(C).sum(axis=0)
    return np.linalg.inv(S) @ C


class HillasPlane:
    """
    a tiny helper class to collect some parameters for each great great
    circle

    Stores some vectors a, b, and c

    These vectors are euclidean [x, y, z] where positive z values point towards the sky
    and x and y are parallel to the ground.
    """

    def __init__(self, p1, p2, telescope_position, weight=1):
        r"""The constructor takes two coordinates in the horizontal
        frame (alt, az) which define a plane perpendicular
        to the camera.

        Parameters
        -----------
        p1: astropy.coordinates.SkyCoord
            One of two direction vectors which define the plane.
            This coordinate has to be defined in the ctapipe.coordinates.AltAz
        p2: astropy.coordinates.SkyCoord
            One of two direction vectors which define the plane.
            This coordinate has to be defined in the ctapipe.coordinates.AltAz
        telescope_position: np.array(3)
            Position of the telescope on the ground
        weight : float, optional
            weight of this plane for later use during the reconstruction

        Notes
        -----
        c: numpy.ndarray(3)
            :math:`\vec c = (\vec a \times \vec b) \times \vec a`
            :math:`\rightarrow` a and c form an orthogonal base for the
            great circle
            (only orthonormal if a and b are of unit-length)
        norm: numpy.ndarray(3)
            normal vector of the circle's plane,
            perpendicular to a, b and c
        """

        self.pos = telescope_position

        # astropy's coordinates system rotates counter clockwise. Apparently we assume it to
        # be clockwise
        self.a = np.array(spherical_to_cartesian(1, p1.alt, -p1.az)).ravel()
        self.b = np.array(spherical_to_cartesian(1, p2.alt, -p2.az)).ravel()

        # a and c form an orthogonal basis for the great circle
        # not really necessary since the norm can be calculated
        # with a and b just as well
        self.c = np.cross(np.cross(self.a, self.b), self.a)
        # normal vector for the plane defined by the great circle
        self.norm = normalise(np.cross(self.a, self.c))
        # some weight for this circle
        # (put e.g. uncertainty on the Hillas parameters
        # or number of PE in here)
        self.weight = weight


class HillasReconstructor(Reconstructor):
    """
    class that reconstructs the direction of an atmospheric shower
    using a simple hillas parametrisation of the camera images it
    provides a direction estimate in two steps and an estimate for the
    shower's impact position on the ground.

    so far, it does neither provide an energy estimator nor an
    uncertainty on the reconstructed parameters

    """

    def __init__(self, config=None, parent=None, **kwargs):
        super().__init__(config=config, parent=parent, **kwargs)
        self.hillas_planes = {}
        self.divergent_mode = False
        self.corrected_angle_dict = {}

    def predict(self, hillas_dict, subarray, array_pointing, telescopes_pointings=None):
        """
        The function you want to call for the reconstruction of the
        event. It takes care of setting up the event and consecutively
        calls the functions for the direction and core position
        reconstruction.  Shower parameters not reconstructed by this
        class are set to np.nan

        Parameters
        -----------
        hillas_dict: dict
            dictionary with telescope IDs as key and
            HillasParametersContainer instances as values
        inst : ctapipe.io.InstrumentContainer
            instrumental description
        array_pointing: SkyCoord[AltAz]
            pointing direction of the array
        telescopes_pointings: dict[SkyCoord[AltAz]]
            dictionary of pointing direction per each telescope

        Raises
        ------
        TooFewTelescopesException
            if len(hillas_dict) < 2
        InvalidWidthException
            if any width is np.nan or 0
        """

        # filter warnings for missing obs time. this is needed because MC data has no obs time
        warnings.filterwarnings(action="ignore", category=MissingFrameAttributeWarning)

        # stereoscopy needs at least two telescopes
        if len(hillas_dict) < 2:
            raise TooFewTelescopesException(
                "need at least two telescopes, have {}".format(len(hillas_dict))
            )

        # check for np.nan or 0 width's as these screw up weights
        if any([np.isnan(hillas_dict[tel]["width"].value) for tel in hillas_dict]):
            raise InvalidWidthException(
                "A HillasContainer contains an ellipse of width==np.nan"
            )

        if any([hillas_dict[tel]["width"].value == 0 for tel in hillas_dict]):
            raise InvalidWidthException(
                "A HillasContainer contains an ellipse of width==0"
            )

        # use the single telescope pointing also for parallel pointing: code is more general
        if telescopes_pointings is None:
            telescopes_pointings = {
                tel_id: array_pointing for tel_id in hillas_dict.keys()
            }
        else:
            self.divergent_mode = True
            self.corrected_angle_dict = {}

        self.initialize_hillas_planes(
            hillas_dict, subarray, telescopes_pointings, array_pointing
        )

        # algebraic direction estimate
        direction, err_est_dir = self.estimate_direction()

        # array pointing is needed to define the tilted frame
        core_pos = self.estimate_core_position(hillas_dict, array_pointing)

        # container class for reconstructed showers
        _, lat, lon = cartesian_to_spherical(*direction)

        # estimate max height of shower
        h_max = self.estimate_h_max()

        # astropy's coordinates system rotates counter-clockwise.
        # Apparently we assume it to be clockwise.
        # that's why lon get's a sign
        result = ReconstructedGeometryContainer(
            alt=lat,
            az=-lon,
            core_x=core_pos[0],
            core_y=core_pos[1],
            tel_ids=[h for h in hillas_dict.keys()],
            average_intensity=np.mean([h.intensity for h in hillas_dict.values()]),
            is_valid=True,
            alt_uncert=err_est_dir,
            h_max=h_max,
        )

        return result

    def initialize_hillas_planes(
        self, hillas_dict, subarray, telescopes_pointings, array_pointing
    ):
        """
        Creates a dictionary of `.HillasPlane` from a dictionary of
        hillas parameters

        Parameters
        ----------
        hillas_dict : dictionary
            dictionary of hillas moments
        subarray : ctapipe.instrument.SubarrayDescription
            subarray information
        telescopes_pointings: dictionary
            dictionary of pointing direction per each telescope
        array_pointing: SkyCoord[AltAz]
            pointing direction of the array
        """

        self.hillas_planes = {}
        k = next(iter(telescopes_pointings))
        horizon_frame = telescopes_pointings[k].frame
        for tel_id, moments in hillas_dict.items():
            # we just need any point on the main shower axis a bit away from the cog
            p2_x = moments.x + 0.1 * u.m * np.cos(moments.psi)
            p2_y = moments.y + 0.1 * u.m * np.sin(moments.psi)
            focal_length = subarray.tel[tel_id].optics.equivalent_focal_length

            pointing = SkyCoord(
                alt=telescopes_pointings[tel_id].alt,
                az=telescopes_pointings[tel_id].az,
                frame=horizon_frame,
            )

            camera_frame = CameraFrame(
                focal_length=focal_length, telescope_pointing=pointing
            )

            cog_coord = SkyCoord(x=moments.x, y=moments.y, frame=camera_frame)
            cog_coord = cog_coord.transform_to(horizon_frame)

            p2_coord = SkyCoord(x=p2_x, y=p2_y, frame=camera_frame)
            p2_coord = p2_coord.transform_to(horizon_frame)

            # re-project from sky to a "fake"-parallel-pointing telescope
            # then recalculate the psi angle
            if self.divergent_mode:
                camera_frame_parallel = CameraFrame(
                    focal_length=focal_length, telescope_pointing=array_pointing
                )
                cog_sky_to_parallel = cog_coord.transform_to(camera_frame_parallel)
                p2_sky_to_parallel = p2_coord.transform_to(camera_frame_parallel)
                angle_psi_corr = np.arctan2(
                    cog_sky_to_parallel.y - p2_sky_to_parallel.y,
                    cog_sky_to_parallel.x - p2_sky_to_parallel.x,
                )
                self.corrected_angle_dict[tel_id] = angle_psi_corr

            circle = HillasPlane(
                p1=cog_coord,
                p2=p2_coord,
                telescope_position=subarray.positions[tel_id],
                weight=moments.intensity * (moments.length / moments.width),
            )
            self.hillas_planes[tel_id] = circle

    def estimate_direction(self):
        """calculates the origin of the gamma as the weighted average
        direction of the intersections of all hillas planes

        Returns
        -------
        gamma : shape (3) numpy array
            direction of origin of the reconstructed shower as a 3D vector
        crossings : shape (n,3) list
            an error estimate
        """

        crossings = []
        for perm in combinations(self.hillas_planes.values(), 2):
            n1, n2 = perm[0].norm, perm[1].norm
            # cross product automatically weighs in the angle between
            # the two vectors: narrower angles have less impact,
            # perpendicular vectors have the most
            crossing = np.cross(n1, n2)

            # two great circles cross each other twice (one would be
            # the origin, the other one the direction of the gamma) it
            # doesn't matter which we pick but it should at least be
            # consistent: make sure to always take the "upper" solution
            if crossing[2] < 0:
                crossing *= -1
            crossings.append(crossing * perm[0].weight * perm[1].weight)

        result = normalise(np.sum(crossings, axis=0))
        off_angles = [angle(result, cross) for cross in crossings] * u.rad

        err_est_dir = np.average(
            off_angles, weights=[len(cross) for cross in crossings]
        )

        return result, err_est_dir

    def estimate_core_position(self, hillas_dict, array_pointing):
        """
        Estimate the core position by intersection the major ellipse lines of each telescope.

        Parameters
        -----------
        hillas_dict: dict[HillasContainer]
            dictionary of hillas moments
        array_pointing: SkyCoord[HorizonFrame]
            Pointing direction of the array

        Returns
        -----------
        core_x: u.Quantity
            estimated x position of impact
        core_y: u.Quantity
            estimated y position of impact

        """
        if self.divergent_mode:
            psi = u.Quantity(list(self.corrected_angle_dict.values()))
        else:
            psi = u.Quantity([h.psi for h in hillas_dict.values()])

        z = np.zeros(len(psi))
        uvw_vectors = np.column_stack([np.cos(psi).value, np.sin(psi).value, z])

        tilted_frame = TiltedGroundFrame(pointing_direction=array_pointing)
        ground_frame = GroundFrame()

        positions = [
            (
                SkyCoord(*plane.pos, frame=ground_frame)
                .transform_to(tilted_frame)
                .cartesian.xyz
            )
            for plane in self.hillas_planes.values()
        ]

        core_position = line_line_intersection_3d(uvw_vectors, positions)

        core_pos_tilted = SkyCoord(
            x=core_position[0] * u.m, y=core_position[1] * u.m, frame=tilted_frame
        )

        core_pos = project_to_ground(core_pos_tilted)

        return core_pos.x, core_pos.y

    def estimate_h_max(self):
        """
        Estimate the max height by intersecting the lines of the cog directions of each telescope.

        Returns
        -----------
        astropy.unit.Quantity
            the estimated max height
        """
        uvw_vectors = np.array([plane.a for plane in self.hillas_planes.values()])
        positions = [plane.pos for plane in self.hillas_planes.values()]

        # not sure if its better to return the length of the vector of the z component
        return np.linalg.norm(line_line_intersection_3d(uvw_vectors, positions)) * u.m
