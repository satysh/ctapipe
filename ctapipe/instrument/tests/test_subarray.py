""" Tests for SubarrayDescriptions """
import tempfile
import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from ctapipe.coordinates import TelescopeFrame

from ctapipe.instrument import (
    CameraDescription,
    OpticsDescription,
    SubarrayDescription,
    TelescopeDescription,
)


def example_subarray(n_tels=10):
    """ generate a simple subarray for testing purposes """
    rng = np.random.default_rng(0)

    pos = {}
    tel = {}

    for tel_id in range(1, n_tels + 1):
        tel[tel_id] = TelescopeDescription.from_name(
            optics_name="MST", camera_name="NectarCam"
        )
        pos[tel_id] = rng.uniform(-100, 100, size=3) * u.m

    return SubarrayDescription("test array", tel_positions=pos, tel_descriptions=tel)


def test_subarray_description():
    """ Test SubarrayDescription functionality """
    n_tels = 10
    sub = example_subarray(n_tels)
    sub.peek()

    assert len(sub.telescope_types) == 1

    assert str(sub) == "test array"
    assert sub.num_tels == n_tels
    assert len(sub.tel_ids) == n_tels
    assert sub.tel_ids[0] == 1
    assert sub.tel[1].camera is not None
    assert 0 not in sub.tel  # check that there is no tel 0 (1 is first above)
    assert len(sub.camera_types) == 1  # only 1 camera type
    assert isinstance(sub.camera_types[0], CameraDescription)
    assert isinstance(sub.telescope_types[0], TelescopeDescription)
    assert isinstance(sub.optics_types[0], OpticsDescription)
    assert len(sub.telescope_types) == 1  # only have one type in this array
    assert len(sub.optics_types) == 1  # only have one type in this array
    assert len(sub.camera_types) == 1  # only have one type in this array
    assert sub.optics_types[0].equivalent_focal_length.to_value(u.m) == 16.0
    assert isinstance(sub.tel_coords, SkyCoord)
    assert len(sub.tel_coords) == n_tels

    subsub = sub.select_subarray([2, 3, 4, 6], name="newsub")
    assert subsub.num_tels == 4
    assert set(subsub.tels.keys()) == {2, 3, 4, 6}
    assert subsub.tel_indices[6] == 3
    assert subsub.tel_ids[3] == 6

    assert len(sub.to_table(kind="optics")) == 1
    assert sub.telescope_types[0] == sub.tel[1]


def test_to_table(example_subarray):
    """ Check that we can generate astropy Tables from the SubarrayDescription """
    sub = example_subarray
    assert len(sub.to_table(kind="subarray")) == sub.num_tels
    assert len(sub.to_table(kind="optics")) == len(sub.optics_types)


def test_tel_indexing(example_subarray):
    """ Check that we can convert between telescope_id and telescope_index """
    sub = example_subarray

    assert sub.tel_indices[1] == 0  # first tel_id is in slot 0
    for tel_id in sub.tel_ids:
        assert sub.tel_index_array[tel_id] == sub.tel_indices[tel_id]

    assert sub.tel_ids_to_indices(1) == 0
    assert np.all(sub.tel_ids_to_indices([1, 2, 3]) == np.array([0, 1, 2]))


def test_tel_ids_to_mask(example_subarray):
    lst = TelescopeDescription.from_name("LST", "LSTCam")
    subarray = SubarrayDescription(
        "someone_counted_in_binary",
        tel_positions={1: [0, 0, 0] * u.m, 10: [50, 0, 0] * u.m},
        tel_descriptions={1: lst, 10: lst},
    )

    assert np.all(subarray.tel_ids_to_mask([]) == [False, False])
    assert np.all(subarray.tel_ids_to_mask([1]) == [True, False])
    assert np.all(subarray.tel_ids_to_mask([10]) == [False, True])
    assert np.all(subarray.tel_ids_to_mask([1, 10]) == [True, True])


def test_get_tel_ids_for_type(example_subarray):
    """
    check that we can get a list of telescope ids by a telescope type, which can
    be passed by string or `TelescopeDescription` instance
    """
    sub = example_subarray
    types = sub.telescope_types

    for teltype in types:
        assert len(sub.get_tel_ids_for_type(teltype)) > 0
        assert len(sub.get_tel_ids_for_type(str(teltype))) > 0


def test_hdf(example_subarray):
    import tables

    with tempfile.NamedTemporaryFile(suffix=".hdf5") as f:

        example_subarray.to_hdf(f.name)
        read = SubarrayDescription.from_hdf(f.name)

        assert example_subarray == read

        # test we can write the read subarray
        read.to_hdf(f.name, overwrite=True)

        for tel_id, tel in read.tel.items():
            assert (
                tel.camera.geometry.frame.focal_length
                == tel.optics.equivalent_focal_length
            )

            # test if transforming works
            tel.camera.geometry.transform_to(TelescopeFrame())

        # test that subarrays without name (v0.8.0) work:
        with tables.open_file(f.name, "r+") as hdf:
            del hdf.root.configuration.instrument.subarray._v_attrs.name

        no_name = SubarrayDescription.from_hdf(f.name)
        assert no_name.name == "Unknown"

    # test with a subarray that has two different telescopes with the same
    # camera
    tel = {
        1: TelescopeDescription.from_name(optics_name="SST-ASTRI", camera_name="CHEC"),
        2: TelescopeDescription.from_name(optics_name="SST-GCT", camera_name="CHEC"),
    }
    pos = {1: [0, 0, 0] * u.m, 2: [50, 0, 0] * u.m}

    array = SubarrayDescription("test array", tel_positions=pos, tel_descriptions=tel)

    with tempfile.NamedTemporaryFile(suffix=".hdf5") as f:

        array.to_hdf(f.name)
        read = SubarrayDescription.from_hdf(f.name)

        assert array == read
