import sys
from bokeh.events import Tap
import numpy as np
from bokeh.io import output_notebook, push_notebook, show, output_file
from bokeh.plotting import figure
from bokeh.models import (
    ColumnDataSource,
    TapTool,
    Span,
    ColorBar,
    LinearColorMapper,
    LogColorMapper,
    ContinuousColorMapper,
    CategoricalColorMapper,
    HoverTool,
    BoxZoomTool,
    Ellipse,
    Label,
)
from bokeh.palettes import Viridis256, Magma256, Inferno256, Greys256, d3
import tempfile
from threading import Timer
from functools import wraps
import astropy.units as u

from ctapipe.instrument import CameraGeometry, PixelShape
from ctapipe.coordinates import GroundFrame


PLOTARGS = dict(tools="", toolbar_location=None, outline_line_color="#595959")


# mapper to mpl names
CMAPS = {
    "viridis": Viridis256,
    "magma": Magma256,
    "inferno": Inferno256,
    "grey": Greys256,
    "gray": Greys256,
}


def palette_from_mpl_name(name):
    if name in CMAPS:
        return CMAPS[name]

    # TODO: make optional if we decide to make one of the plotting
    # TODO: libraries optional
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_hex

    rgba = plt.get_cmap(name)(np.linspace(0, 1, 256))
    palette = [to_hex(color) for color in rgba]
    return palette


def is_notebook():
    """
    Returns True if currently running in a notebook session,
    see https://stackoverflow.com/a/37661854/3838691
    """
    return "ipykernel" in sys.modules


def generate_hex_vertices(geom):
    phi = np.arange(0, 2 * np.pi, np.pi / 3)

    # apply pixel rotation and conversion from flat top to pointy top
    phi += geom.pix_rotation.rad + np.deg2rad(30)

    # we need the circumcircle radius, pixel_width is incircle diameter
    r = 2 / np.sqrt(3) * geom.pixel_width.value / 2

    x = geom.pix_x.value
    y = geom.pix_y.value

    xs = x[:, np.newaxis] + r[:, np.newaxis] * np.cos(phi)[np.newaxis]
    ys = y[:, np.newaxis] + r[:, np.newaxis] * np.sin(phi)[np.newaxis]

    return xs, ys


def generate_square_vertices(geom):
    w = geom.pixel_width.value / 2
    x = geom.pix_x.value
    y = geom.pix_y.value

    x_offset = w[:, np.newaxis] * np.array([-1, -1, 1, 1])
    y_offset = w[:, np.newaxis] * np.array([1, -1, -1, 1])

    xs = x[:, np.newaxis] + x_offset
    ys = y[:, np.newaxis] + y_offset
    return xs, ys


class BokehPlot:
    def __init__(self, autoshow=True, use_notebook=None, **figure_kwargs):
        # only use autoshow / use_notebook by default if we are in a notebook
        self._use_notebook = use_notebook if use_notebook is not None else is_notebook()
        self._handle = None
        self.figure = figure(**figure_kwargs)
        self.autoshow = autoshow

        if figure_kwargs.get("match_aspect"):
            # Make sure the box zoom tool does not distort the camera display
            for tool in self.figure.toolbar.tools:
                if isinstance(tool, BoxZoomTool):
                    tool.match_aspect = True

    def show(self):
        if self._use_notebook:
            output_notebook()
        else:
            # this only sets the default name, created only when show is called
            output_file(tempfile.mktemp(prefix="ctapipe_bokeh_", suffix=".html"))

        self._handle = show(self.figure, notebook_handle=self._use_notebook)

    def update(self):
        if self._use_notebook and self._handle:
            push_notebook(handle=self._handle)


class CameraDisplay(BokehPlot):
    """
    CameraDisplay implementation in Bokeh
    """

    def __init__(
        # same options as MPL display
        self,
        geometry: CameraGeometry,
        image=None,
        cmap="inferno",
        norm="lin",
        autoscale=True,
        title=None,
        # bokeh specific options
        use_notebook=None,
        autoshow=True,
    ):
        super().__init__(
            autoshow=autoshow,
            use_notebook=use_notebook,
            title=title,
            match_aspect=True,
            aspect_scale=1,
        )

        self._geometry = geometry
        self._color_bar = None
        self._color_mapper = None
        self._pixels = None
        self._tap_tool = None

        self._annotations = []
        self._labels = []
        self.datasource = None

        self._init_datasource(image)

        if title is None:
            frame = (
                geometry.frame.__class__.__name__ if geometry.frame else "CameraFrame"
            )
            title = f"{geometry} ({frame})"

        self.figure.add_tools(HoverTool(tooltips=[("id", "@id"), ("value", "@image")]))

        # order is important because steps depend on each other
        self.cmap = cmap
        self.norm = norm
        self.autoscale = autoscale
        self.rescale()
        self._setup_camera()

    def _init_datasource(self, image=None):
        if image is None:
            image = np.zeros(self._geometry.n_pixels)

        data = dict(
            id=self._geometry.pix_id,
            image=image,
            line_width=np.zeros(self._geometry.n_pixels),
            line_color=["green"] * self._geometry.n_pixels,
            line_alpha=np.zeros(self._geometry.n_pixels),
        )

        if self._geometry.pix_type == PixelShape.HEXAGON:
            xs, ys = generate_hex_vertices(self._geometry)

        elif self._geometry.pix_type == PixelShape.SQUARE:
            xs, ys = generate_square_vertices(self._geometry)

        elif self._geometry.pix_type == PixelShape.CIRCLE:
            xs, ys = self._geometry.pix_x.value, self._geometry.pix_y.value
            data["radius"] = self._geometry.pixel_width / 2
        else:
            raise NotImplementedError(
                f"Unsupported pixel shape {self._geometry.pix_type}"
            )

        data["xs"], data["ys"] = xs.tolist(), ys.tolist()

        if self.datasource is None:
            self.datasource = ColumnDataSource(data=data)
        else:
            self.datasource.update(data=data)

    def _setup_camera(self):
        kwargs = dict(
            fill_color=dict(field="image", transform=self.norm),
            line_width="line_width",
            line_color="line_color",
            line_alpha="line_alpha",
            source=self.datasource,
        )
        if self._geometry.pix_type in (PixelShape.SQUARE, PixelShape.HEXAGON):
            self._pixels = self.figure.patches(xs="xs", ys="ys", **kwargs)
        elif self._geometry.pix_type == PixelShape.CIRCLE:
            self._pixels = self.figure.circle(x="xs", y="ys", radius="radius", **kwargs)

    def clear_overlays(self):
        while self._annotations:
            self.figure.renderers.remove(self._annotations.pop())

        while self._labels:
            self.figure.center.remove(self._labels.pop())

    def add_colorbar(self):
        self._color_bar = ColorBar(
            color_mapper=self._color_mapper,
            label_standoff=12,
            border_line_color=None,
            location=(0, 0),
        )
        self.figure.add_layout(self._color_bar, "right")
        self.update()

    def rescale(self):
        low = self.datasource.data["image"].min()
        high = self.datasource.data["image"].max()

        # force color to be at lower end of the colormap if
        # data is all equal
        if low == high:
            high += 1

        self.set_limits_minmax(low, high)

    def enable_pixel_picker(self, callback):
        if self._tap_tool is None:
            self.figure.add_tools(TapTool())
        self.datasource.selected.on_change("indices", callback)

    def set_limits_minmax(self, zmin, zmax):
        self._color_mapper.update(low=zmin, high=zmax)
        self.update()

    def set_limits_percent(self, percent=95):
        zmin = np.nanmin(self.image)
        zmax = np.nanmax(self.image)
        dz = zmax - zmin
        frac = percent / 100.0
        self.set_limits_minmax(zmin, zmax - (1.0 - frac) * dz)

    def highlight_pixels(self, pixels, color="g", linewidth=1, alpha=0.75):
        """
        Highlight the given pixels with a colored line around them

        Parameters
        ----------
        pixels : index-like
            The pixels to highlight.
            Can either be a list or array of integers or a
            boolean mask of length number of pixels
        color: a matplotlib conform color
            the color for the pixel highlighting
        linewidth: float
            linewidth of the highlighting in points
        alpha: 0 <= alpha <= 1
            The transparency
        """
        n_pixels = self._geometry.n_pixels
        pixels = np.asanyarray(pixels)

        if pixels.dtype != np.bool:
            selected = np.zeros(n_pixels, dtype=bool)
            selected[pixels] = True
            pixels = selected

        new_data = {"line_alpha": [(slice(None), pixels.astype(float) * alpha)]}
        if linewidth != self.datasource.data["line_width"][0]:
            new_data["line_width"] = [(slice(None), np.full(n_pixels, linewidth))]

        if color != self.datasource.data["line_color"][0]:
            new_data["line_color"] = [(slice(None), [color] * n_pixels)]

        self.datasource.patch(new_data)
        self.update()

    @property
    def cmap(self):
        return self._palette

    @cmap.setter
    def cmap(self, cmap):
        if isinstance(cmap, str):
            cmap = palette_from_mpl_name(cmap)

        self._palette = cmap
        # might be called in __init__ before color mapper is setup
        if self._color_mapper is not None:
            self._color_mapper.palette = cmap
            self._trigger_cm_update()
            self.update()

    def _trigger_cm_update(self):
        # it seems changing palette does not trigger a color change,
        # so we reassign limits
        low = self._color_mapper.low
        self._color_mapper.update(low=low)

    @property
    def geometry(self):
        return self._geometry

    @geometry.setter
    def geometry(self, new_geometry):
        self._geometry = new_geometry
        self.figure.renderers.remove(self._pixels)
        self._init_datasource()
        self._setup_camera()
        self.rescale()
        self.update()

    @property
    def image(self):
        return self.datasource.data["image"]

    @image.setter
    def image(self, new_image):
        self.datasource.patch({"image": [(slice(None), new_image)]})
        if self.autoscale:
            self.rescale()

    @property
    def norm(self):
        """
        The norm instance of the Display

        Possible values:

        - "lin": linear scale
        - "log": log scale (cannot have negative values)
        - "symlog": symmetric log scale (negative values are ok)
        """
        return self._color_mapper

    @norm.setter
    def norm(self, norm):
        if not isinstance(norm, ContinuousColorMapper):
            if norm == "lin":
                norm = LinearColorMapper
            elif norm == "log":
                norm = LogColorMapper
            else:
                raise ValueError(f"Unsupported norm {norm}")

        self._color_mapper = norm(self.cmap)
        if self._pixels is not None:
            self._pixels.glyph.fill_color.update(transform=self._color_mapper)

        if self._color_bar is not None:
            self._color_bar.update(color_mapper=self._color_mapper)

        self.update()

    def add_ellipse(self, centroid, length, width, angle, asymmetry=0.0, **kwargs):
        """
        plot an ellipse on top of the camera

        Parameters
        ----------
        centroid: (float, float)
            position of centroid
        length: float
            major axis
        width: float
            minor axis
        angle: float
            rotation angle wrt x-axis about the centroid, anticlockwise, in radians
        asymmetry: float
            3rd-order moment for directionality if known
        kwargs:
            any MatPlotLib style arguments to pass to the Ellipse patch

        """
        ellipse = Ellipse(
            x=centroid[0],
            y=centroid[1],
            width=length,
            height=width,
            angle=angle,
            fill_color=None,
            **kwargs,
        )
        glyph = self.figure.add_glyph(ellipse)
        self._annotations.append(glyph)
        self.update()
        return ellipse

    def overlay_moments(
        self, hillas_parameters, with_label=True, keep_old=False, **kwargs
    ):
        """helper to overlay ellipse from a `HillasParametersContainer` structure

        Parameters
        ----------
        hillas_parameters: `HillasParametersContainer`
            structuring containing Hillas-style parameterization
        with_label: bool
            If True, show coordinates of centroid and width and length
        keep_old: bool
            If True, to not remove old overlays
        kwargs: key=value
            any style keywords to pass to matplotlib (e.g. color='red'
            or linewidth=6)
        """
        if not keep_old:
            self.clear_overlays()

        # strip off any units
        cen_x = u.Quantity(hillas_parameters.x).value
        cen_y = u.Quantity(hillas_parameters.y).value
        length = u.Quantity(hillas_parameters.length).value
        width = u.Quantity(hillas_parameters.width).value

        el = self.add_ellipse(
            centroid=(cen_x, cen_y),
            length=length * 2,
            width=width * 2,
            angle=hillas_parameters.psi.to_value(u.rad),
            **kwargs,
        )

        if with_label:
            label = Label(
                x=cen_x,
                y=cen_y,
                text="({:.02f},{:.02f})\n[w={:.02f},l={:.02f}]".format(
                    hillas_parameters.x,
                    hillas_parameters.y,
                    hillas_parameters.width,
                    hillas_parameters.length,
                ),
                text_color=el.line_color,
            )
            self.figure.add_layout(label, "center")
            self._labels.append(label)


class WaveformDisplay:
    def __init__(self, waveform=np.zeros(1), fig=None):
        """
        Waveform display that utilises the bokeh visualisation library

        Parameters
        ----------
        waveform : ndarray
            1D array containing the waveform samples
        fig : bokeh.plotting.figure
            Figure to store the bokeh plot onto (optional)
        """
        self._waveform = None
        self._fig = None
        self._active_time = 0

        self.span = None

        cdsource_d = dict(t=[], samples=[])
        self.cdsource = ColumnDataSource(data=cdsource_d)

        self.waveform = waveform
        self.fig = fig

        self.layout = self.fig

    @property
    def fig(self):
        return self._fig

    @fig.setter
    def fig(self, val):
        if val is None:
            val = figure(plot_width=700, plot_height=180, **PLOTARGS)
        self._fig = val

        self._draw_waveform()

    @property
    def waveform(self):
        return self._waveform

    @waveform.setter
    def waveform(self, val):
        if val is None:
            val = np.full(1, np.nan)

        self._waveform = val

        if len(val) == len(self.cdsource.data["t"]):
            self.cdsource.data["samples"] = val
        else:
            cdsource_d = dict(t=np.arange(val.size), samples=val)
            self.cdsource.data = cdsource_d

    @property
    def active_time(self):
        return self._active_time

    @active_time.setter
    def active_time(self, val):
        max_t = self.cdsource.data["t"][-1]
        if val is None:
            val = 0
        if val < 0:
            val = 0
        if val > max_t:
            val = max_t
        self.span.location = val
        self._active_time = val

    def _draw_waveform(self):
        self.fig.line(x="t", y="samples", source=self.cdsource, name="line")

    def enable_time_picker(self):
        """
        Enables the selection of a time by clicking on the waveform
        """
        self.span = Span(
            location=0, dimension="height", line_color="red", line_dash="dashed"
        )
        self.fig.add_layout(self.span)

        taptool = TapTool()
        self.fig.add_tools(taptool)

        def wf_tap_response(event):
            time = event.x
            if time is not None:
                self.active_time = time
                self._on_waveform_click(time)

        self.fig.on_event(Tap, wf_tap_response)

    def _on_waveform_click(self, time):
        print(f"Clicked time: {time}")
        print(f"Active time: {self.active_time}")


class ArrayDisplay(BokehPlot):
    """
    Display a top-town view of a telescope array.

    This can be used in two ways: by default, you get a display of all
    telescopes in the subarray, colored by telescope type, however you can
    also color the telescopes by a value (like trigger pattern, or some other
    scalar per-telescope parameter). To set the color value, simply set the
    `value` attribute, and the fill color will be updated with the value. You
    might want to set the border color to zero to avoid confusion between the
    telescope type color and the value color (
    `array_disp.telescope.set_linewidth(0)`)

    To display a vector field over the telescope positions, e.g. for
    reconstruction, call `set_uv()` to set cartesian vectors, or `set_r_phi()`
    to set polar coordinate vectors.  These both take an array of length
    N_tels, or a single value.


    Parameters
    ----------
    subarray: ctapipe.instrument.SubarrayDescription
        the array layout to display
    axes: matplotlib.axes.Axes
        matplotlib axes to plot on, or None to use current one
    title: str
        title of array plot
    tel_scale: float
        scaling between telescope mirror radius in m to displayed size
    autoupdate: bool
        redraw when the input changes
    radius: Union[float, list, None]
        set telescope radius to value, list/array of values. If None, radius
        is taken from the telescope's mirror size.
    """

    def __init__(
        self,
        subarray,
        frame=None,
        scale=5.0,
        alpha=1.0,
        title=None,
        cmap="inferno",
        radius=None,
        use_notebook=None,
        autoshow=True,
        values=None,
    ):
        if title is None:
            frame_name = (frame or subarray.tel_coords.frame).__class__.__name__
            title = f"{subarray.name} ({frame_name})"

        super().__init__(
            autoshow=autoshow,
            use_notebook=use_notebook,
            title=title,
            match_aspect=True,
            aspect_scale=1,
        )

        self.frame = frame
        self.subarray = subarray
        self.datasource = None

        self._init_datasource(
            subarray,
            values=values,
            radius=radius,
            frame=frame,
            scale=scale,
            alpha=alpha,
        )

        self.figure = figure(title=title, match_aspect=True, aspect_scale=1)

        if isinstance(cmap, str):
            cmap = palette_from_mpl_name(cmap)

        # color by type if no value given
        if values is None:
            n_types = len(subarray.telescope_types)
            palette = cmap or d3["Category10"][max(n_types, 10)]
            self._color_mapper = CategoricalColorMapper(
                palette=palette, factors=[str(t) for t in subarray.telescope_types]
            )
            field = "type"
        else:
            palette = cmap or Viridis256
            self._color_mapper = LinearColorMapper(palette=palette)
            field = "values"

        color = dict(field=field, transform=self._color_mapper)

        self._telescopes = self.figure.circle(
            x="x",
            y="y",
            radius="radius",
            alpha="alpha",
            line_alpha="alpha",
            fill_color=color,
            line_color=color,
            source=self.datasource,
            legend_field="type",
        )
        self.figure.add_tools(
            HoverTool(tooltips=[("id", "@id"), ("type", "@type"), ("z", "@z")])
        )
        self.figure.legend.orientation = "horizontal"
        self.figure.legend.location = "top_left"

    def add_colorbar(self):
        self._color_bar = ColorBar(
            color_mapper=self._color_mapper,
            label_standoff=12,
            border_line_color=None,
            location=(0, 0),
        )
        self.figure.add_layout(self._color_bar, "right")
        self.update()

    def _init_datasource(self, subarray, values, *, radius, frame, scale, alpha):
        telescope_ids = sorted(subarray.tel)
        tel_coords = subarray.tel_coords

        # get the telescope positions. If a new frame is set, this will
        # transform to the new frame.
        if frame is not None:
            tel_coords = tel_coords.transform_to(frame)

        tel_types = []
        mirror_radii = np.zeros(len(telescope_ids))

        for i, telescope_id in enumerate(telescope_ids):
            telescope = subarray.tel[telescope_id]
            tel_types.append(str(telescope))
            mirror_area = telescope.optics.mirror_area.to_value(u.m ** 2)
            mirror_radii[i] = np.sqrt(mirror_area) / np.pi

        if np.isscalar(alpha):
            alpha = np.full(len(telescope_ids), alpha)
        else:
            alpha = np.array(alpha)

        data = {
            "id": telescope_ids,
            "x": tel_coords.x.to_value(u.m).tolist(),
            "y": tel_coords.y.to_value(u.m).tolist(),
            "z": tel_coords.z.to_value(u.m).tolist(),
            "alpha": alpha.tolist(),
            "type": tel_types,
            "mirror_radius": mirror_radii.tolist(),
            "radius": (radius if radius is not None else mirror_radii * scale).tolist(),
        }

        if values is not None:
            data["values"] = values

        if self.datasource is None:
            self.datasource = ColumnDataSource(data=data)
        else:
            self.datasource.update(data=data)
