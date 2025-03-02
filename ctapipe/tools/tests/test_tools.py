"""
Test individual tool functionality
"""
import sys

import matplotlib as mpl

import pandas as pd
import tables

from ctapipe.utils import get_dataset_path
from ctapipe.core import run_tool
from ctapipe.io import DataLevel
import numpy as np
from pathlib import Path


GAMMA_TEST_LARGE = get_dataset_path("gamma_test_large.simtel.gz")
LST_MUONS = get_dataset_path("lst_muons.simtel.zst")


def test_stage_1_dl1(tmp_path, dl1_image_file, dl1_parameters_file):
    from ctapipe.tools.stage1 import Stage1Tool

    config = Path("./examples/stage1_config.json").absolute()
    # DL1A file as input
    dl1b_from_dl1a_file = tmp_path / "dl1b_from dl1a.dl1.h5"
    assert (
        run_tool(
            Stage1Tool(),
            argv=[
                f"--config={config}",
                f"--input={dl1_image_file}",
                f"--output={dl1b_from_dl1a_file}",
                "--write-parameters",
                "--overwrite",
            ],
            cwd=tmp_path,
        )
        == 0
    )

    # check tables were written
    with tables.open_file(dl1b_from_dl1a_file, mode="r") as tf:
        assert tf.root.dl1
        assert tf.root.dl1.event.telescope
        assert tf.root.dl1.event.subarray
        assert tf.root.configuration.instrument.subarray.layout
        assert tf.root.configuration.instrument.telescope.optics
        assert tf.root.configuration.instrument.telescope.camera.geometry_LSTCam
        assert tf.root.configuration.instrument.telescope.camera.readout_LSTCam

        assert tf.root.dl1.monitoring.subarray.pointing.dtype.names == (
            "time",
            "array_azimuth",
            "array_altitude",
            "array_ra",
            "array_dec",
        )

    # check we can read telescope parameters
    dl1_features = pd.read_hdf(
        dl1b_from_dl1a_file, "/dl1/event/telescope/parameters/tel_001"
    )
    features = (
        "obs_id",
        "event_id",
        "tel_id",
        "hillas_intensity",
        "concentration_cog",
        "leakage_pixels_width_1",
    )
    for feature in features:
        assert feature in dl1_features.columns

    # DL1B file as input
    assert (
        run_tool(
            Stage1Tool(),
            argv=[
                f"--config={config}",
                f"--input={dl1_parameters_file}",
                f"--output={tmp_path}/dl1b_from_dl1b.dl1.h5",
                "--write-parameters",
                "--overwrite",
            ],
            cwd=tmp_path,
        )
        == 1
    )


def test_stage1_datalevels(tmp_path):
    """test the dl1 tool on a file not providing r1, dl0 or dl1a"""
    from ctapipe.io import EventSource
    from ctapipe.tools.stage1 import Stage1Tool

    class DummyEventSource(EventSource):
        @classmethod
        def is_compatible(cls, path):
            with open(path, "rb") as f:
                dummy = f.read(5)
                return dummy == b"dummy"

        @property
        def datalevels(self):
            return (DataLevel.R0,)

        @property
        def is_simulation(self):
            return True

        @property
        def obs_ids(self):
            return [1]

        @property
        def subarray(self):
            return None

        def _generator(self):
            return None

    dummy_file = tmp_path / "datalevels_dummy.h5"
    out_file = tmp_path / "datalevels_dummy_stage1_output.h5"
    with open(dummy_file, "wb") as f:
        f.write(b"dummy")
        f.flush()

    config = Path("./examples/stage1_config.json").absolute()
    tool = Stage1Tool()

    assert (
        run_tool(
            tool,
            argv=[
                f"--config={config}",
                f"--input={dummy_file}",
                f"--output={out_file}",
                "--write-images",
                "--overwrite",
            ],
            cwd=tmp_path,
        )
        == 1
    )
    # make sure the dummy event source was really used
    assert isinstance(tool.event_source, DummyEventSource)


def test_muon_reconstruction_simtel(tmp_path):
    from ctapipe.tools.muon_reconstruction import MuonAnalysis

    muon_simtel_output_file = tmp_path / "muon_reco_on_simtel.h5"
    assert (
        run_tool(
            MuonAnalysis(),
            argv=[
                f"--input={LST_MUONS}",
                f"--output={muon_simtel_output_file}",
                "--overwrite",
            ],
            cwd=tmp_path,
        )
        == 0
    )

    with tables.open_file(muon_simtel_output_file) as t:
        table = t.root.dl1.event.telescope.parameters.muons[:]
        assert len(table) > 20
        assert np.count_nonzero(np.isnan(table["muonring_radius"])) == 0


def test_muon_reconstruction_dl1(tmp_path, dl1_muon_file):
    from ctapipe.tools.muon_reconstruction import MuonAnalysis

    muon_dl1_output_file = tmp_path / "muon_reco_on_dl1a.h5"
    assert (
        run_tool(
            MuonAnalysis(),
            argv=[
                f"--input={dl1_muon_file}",
                f"--output={muon_dl1_output_file}",
                "--overwrite",
            ],
            cwd=tmp_path,
        )
        == 0
    )

    with tables.open_file(muon_dl1_output_file) as t:
        table = t.root.dl1.event.telescope.parameters.muons[:]
        assert len(table) > 20
        assert np.count_nonzero(np.isnan(table["muonring_radius"])) == 0

    assert run_tool(MuonAnalysis(), ["--help-all"]) == 0


def test_display_summed_images(tmp_path):
    from ctapipe.tools.display_summed_images import ImageSumDisplayerTool

    mpl.use("Agg")
    assert (
        run_tool(
            ImageSumDisplayerTool(),
            argv=[f"--infile={GAMMA_TEST_LARGE}", "--max-events=2"],
            cwd=tmp_path,
        )
        == 0
    )

    assert run_tool(ImageSumDisplayerTool(), ["--help-all"]) == 0


def test_display_integrator(tmp_path):
    from ctapipe.tools.display_integrator import DisplayIntegrator

    mpl.use("Agg")

    assert (
        run_tool(
            DisplayIntegrator(),
            argv=[f"--input={GAMMA_TEST_LARGE}", "--max-events=1"],
            cwd=tmp_path,
        )
        == 0
    )

    assert run_tool(DisplayIntegrator(), ["--help-all"]) == 0


def test_display_events_single_tel(tmp_path):
    from ctapipe.tools.display_events_single_tel import SingleTelEventDisplay

    mpl.use("Agg")

    assert (
        run_tool(
            SingleTelEventDisplay(),
            argv=[
                f"--input={GAMMA_TEST_LARGE}",
                "--tel=11",
                "--max-events=2",  # <--- inconsistent!!!
            ],
            cwd=tmp_path,
        )
        == 0
    )

    assert run_tool(SingleTelEventDisplay(), ["--help-all"]) == 0


def test_display_dl1(tmp_path, dl1_image_file, dl1_parameters_file):
    from ctapipe.tools.display_dl1 import DisplayDL1Calib

    mpl.use("Agg")

    # test simtel
    assert (
        run_tool(
            DisplayDL1Calib(), argv=["--max-events=1", "--telescope=11"], cwd=tmp_path
        )
        == 0
    )
    # test DL1A
    assert (
        run_tool(
            DisplayDL1Calib(),
            argv=[f"--input={dl1_image_file}", "--max-events=1", "--telescope=11"],
        )
        == 0
    )
    # test DL1B, should error since nothing to plot
    ret = run_tool(
        DisplayDL1Calib(),
        argv=[f"--input={dl1_parameters_file}", "--max-events=1", "--telescope=11"],
    )
    assert ret == 1
    assert run_tool(DisplayDL1Calib(), ["--help-all"]) == 0


def test_info():
    from ctapipe.tools.info import info

    info(show_all=True)


def test_dump_triggers(tmp_path):
    from ctapipe.tools.dump_triggers import DumpTriggersTool

    sys.argv = ["dump_triggers"]
    outfile = tmp_path / "triggers.fits"
    tool = DumpTriggersTool(infile=GAMMA_TEST_LARGE, outfile=str(outfile))

    assert run_tool(tool, cwd=tmp_path) == 0

    assert outfile.exists()
    assert run_tool(tool, ["--help-all"]) == 0


def test_dump_instrument(tmp_path):
    from ctapipe.tools.dump_instrument import DumpInstrumentTool

    sys.argv = ["dump_instrument"]
    tool = DumpInstrumentTool()

    assert run_tool(tool, [f"--input={GAMMA_TEST_LARGE}"], cwd=tmp_path) == 0
    assert (tmp_path / "FlashCam.camgeom.fits.gz").exists()

    assert (
        run_tool(tool, [f"--input={GAMMA_TEST_LARGE}", "--format=ecsv"], cwd=tmp_path)
        == 0
    )
    assert (tmp_path / "MonteCarloArray.optics.ecsv.txt").exists()

    assert (
        run_tool(tool, [f"--input={GAMMA_TEST_LARGE}", "--format=hdf5"], cwd=tmp_path)
        == 0
    )
    assert (tmp_path / "subarray.h5").exists()

    assert run_tool(tool, ["--help-all"], cwd=tmp_path) == 0


def test_camdemo(tmp_path):
    from ctapipe.tools.camdemo import CameraDemo

    sys.argv = ["camera_demo"]
    tool = CameraDemo()
    tool.num_events = 10
    tool.cleanframes = 2
    tool.display = False

    assert run_tool(tool, cwd=tmp_path) == 0
    assert run_tool(tool, ["--help-all"]) == 0


def test_bokeh_file_viewer(tmp_path):
    from ctapipe.tools.bokeh.file_viewer import BokehFileViewer

    sys.argv = ["bokeh_file_viewer"]
    tool = BokehFileViewer(disable_server=True)
    assert run_tool(tool, cwd=tmp_path) == 0
    assert tool.reader.input_url == get_dataset_path("gamma_test_large.simtel.gz")
    assert run_tool(tool, ["--help-all"]) == 0
