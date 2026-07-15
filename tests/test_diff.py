import numpy as np
from PIL import Image

from app.capture.screenshot import image_to_gray_array, mean_abs_diff


def test_identical_images_low_diff():
    img = Image.new("RGB", (100, 40), color=(30, 30, 30))
    a = image_to_gray_array(img)
    b = image_to_gray_array(img)
    assert mean_abs_diff(a, b) < 0.001


def test_different_images_high_diff():
    a = image_to_gray_array(Image.new("RGB", (80, 40), color=(0, 0, 0)))
    b = image_to_gray_array(Image.new("RGB", (80, 40), color=(255, 255, 255)))
    assert mean_abs_diff(a, b) > 0.5


def test_mean_abs_diff_shape_mismatch():
    a = np.zeros((20, 40), dtype=np.float32)
    b = np.ones((10, 10), dtype=np.float32) * 255
    d = mean_abs_diff(a, b)
    assert 0.0 <= d <= 1.0
