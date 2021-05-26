from abc import abstractmethod
import numpy as np
from ..core.component import TelescopeComponent
from ..core.traits import FloatTelescopeParameter, BoolTelescopeParameter
from ..instrument import PixelShape
from .geometry_converter import (
    convert_rect_image_1d_to_2d,
    convert_rect_image_back_to_1d,
)
from scipy.ndimage import gaussian_filter
from scipy.signal import convolve2d


def add_noise(image, noise_level, rng=None, correct_bias=True):
    """
    Create a new image with added poissonian noise
    """
    if not rng:
        rng = np.random.default_rng()
    noisy_image = image.copy()
    noise = rng.poisson(noise_level)
    noisy_image += noise
    if correct_bias:
        noisy_image -= noise_level
    return noisy_image


def smear_image(image, geom, smear_factor):
    """
    Create a new image with values smeared to the direct pixel neighbors
    Pixels at the camera edge lose charge this way.
    This behaves differently for square and hexagonal pixels:
    - For hexagonal pixels a fraction of light equal to smear_factor
    in each pixel gets shifted to the neighboring pixels. Each pixel receives 1/6
    of the light
    - For square pixels, the image is converted to a 2d-arra and a 3x3 gaussian
    kernel is applied. Less light diffuses to diagonal neighbors. Smear factor
    is the standard deviation of the gaussian kernel in this case
    
    Parameters:
    -----------
    image: ndarray
    geom: ctapipe.instrument.CameraGeometry
    smear_factor: float
    
    Returns:
    --------
    smeared_image: ndarray   
    """
    if geom.pix_type is PixelShape.HEXAGON:
        max_neighbors = 6
        diffused_image = (
            (image * geom.neighbor_matrix).sum(axis=1) * smear_factor / max_neighbors
        )
        remaining_image = image * (1 - smear_factor)
        smeared_image = remaining_image + diffused_image
    elif geom.pix_type is PixelShape.SQUARE:
        rows_cols, image_2d = convert_rect_image_1d_to_2d(geom, image)
        # construct a normalized 3x3 kernel for convolution
        kernel = np.zeros((3, 3))
        kernel[1, 1] = 1
        kernel = gaussian_filter(kernel, sigma=smear_factor)

        smeared_2d = convolve2d(image_2d, kernel, mode="same")
        smeared_image = convert_rect_image_back_to_1d(rows_cols, smeared_2d)
    else:
        raise Exception(f"Unknown pixel type {geom.pix_type}")
    return smeared_image


class ImageModifier(TelescopeComponent):
    """
    Abstract class for configurable image modifying algorithms. Use
    ``ImageModifier.from_name()`` to construct an instance of a particular algorithm
    """

    @abstractmethod
    def __call__(self, tel_id: int, image: np.ndarray) -> np.ndarray:
        """
        Modifies a given image

        Returns
        -------
        np.ndarray
            modified image
        """
        pass


class NullModifier(ImageModifier):
    def __call__(self, tel_id: int, image: np.ndarray) -> np.ndarray:
        return image


class LSTImageModifier(ImageModifier):
    """
    Add in everything lstchain does.
    """

    smear_factor = FloatTelescopeParameter(
        default_value=0.0, help="Fraction of light to move to each neighbor"
    ).tag(config=True)
    transition_charge = FloatTelescopeParameter(
        default_value=0.0, help="separation between dim and bright pixels"
    ).tag(config=True)
    dim_pixel_bias = FloatTelescopeParameter(
        default_value=0.0, help="extra bias to add in dim pixels"
    ).tag(config=True)
    dim_pixel_noise = FloatTelescopeParameter(
        default_value=0.0, help="expected extra noise in dim pixels"
    ).tag(config=True)
    bright_pixel_noise = FloatTelescopeParameter(
        default_value=0.0, help="expected extra noise in bright pixels"
    ).tag(config=True)
    correct_bias = BoolTelescopeParameter(
        default_value=True,
        help="If True subtract the expected noise from the image. possibly unclear what this does with dim_pixel_bias",
    ).tag(config=True)

    def __call__(self, tel_id, image, rng=None):
        smeared_image = smear_image(
            image,
            self.subarray.tel[tel_id].camera.geometry,
            self.smear_factor.tel[tel_id],
        )
        noise = np.where(
            image > self.transition_charge.tel[tel_id],
            self.bright_pixel_noise.tel[tel_id],
            self.dim_pixel_noise.tel[tel_id],
        )
        image_with_noise = add_noise(
            smeared_image, noise, rng=rng, correct_bias=self.correct_bias.tel[tel_id]
        )
        bias = np.where(
            image > self.transition_charge.tel[tel_id],
            0,
            self.dim_pixel_noise.tel[tel_id],
        )
        return (image_with_noise + bias).astype(np.float32)
