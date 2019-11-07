from __future__ import absolute_import
from __future__ import division

import math

import numpy as np
import torchvision
import cv2

from libs.core.inference import get_all_preds


def save_batch_image_with_joints(batch_image, batch_joints_xyv, file_name, nrow=8, padding=0):
    """
    """

    grid = torchvision.utils.make_grid(batch_image, nrow, padding, True)
    ndarr = grid.mul(255).clamp(0, 255).byte().permute(1, 2, 0).cpu().numpy()
    ndarr = ndarr.copy()

    nmaps = batch_image.size(0)
    xmaps = min(nrow, nmaps)
    ymaps = int(math.ceil(float(nmaps) / xmaps))
    height = int(batch_image.size(2) + padding)
    width = int(batch_image.size(3) + padding)
    k = 0
    for y in range(ymaps):
        for x in range(xmaps):
            if k >= nmaps:
                break
            joints = batch_joints_xyv[k]

            for joint_list in joints:
                for joint in joint_list:
                    joint[0] = x * width + padding + joint[0]
                    joint[1] = y * height + padding + joint[1]
                    cv2.circle(ndarr, (int(joint[0]), int(joint[1])), 2, [255, 0, 0], 2)
            k = k + 1
    cv2.imwrite(file_name, ndarr)


def save_batch_heatmaps(batch_image, batch_heatmaps, file_name, normalize=True):
    """
    :param batch_image: [batch_size, channel, height, width]
    :param batch_heatmaps: [batch_size, num_joints, height, width]
    :param file_name:
    :param normalize:
    :return:
    """
    if normalize:
        batch_image = batch_image.clone()
        vmin = float(batch_image.min())
        vmax = float(batch_image.max())

        batch_image.add_(-vmin).div_(vmax - vmin + 1e-5)

    batch_size = batch_heatmaps.size(0)
    num_joints = batch_heatmaps.size(1)
    heatmap_height = batch_heatmaps.size(2)
    heatmap_width = batch_heatmaps.size(3)

    grid_image = np.zeros((batch_size * heatmap_height,
                           (num_joints + 1) * heatmap_width,
                           3),
                          dtype=np.uint8)

    preds, maxvals = get_all_preds(batch_heatmaps.detach().cpu().numpy())

    for i in range(batch_size):
        image = batch_image[i].mul(255) \
            .clamp(0, 255) \
            .byte() \
            .permute(1, 2, 0) \
            .cpu().numpy()
        heatmaps = batch_heatmaps[i].mul(255) \
            .clamp(0, 255) \
            .byte() \
            .cpu().numpy()

        resized_image = cv2.resize(image, (int(heatmap_width), int(heatmap_height)))

        height_begin = heatmap_height * i
        height_end = heatmap_height * (i + 1)
        for j in range(num_joints):
            point_list = preds[i][j]
            for point in point_list:
                cv2.circle(resized_image, (point[0], point[1]), 1, [0, 0, 255], 1)

            heatmap = heatmaps[j, :, :]
            colored_heatmap = cv2.applyColorMap(heatmap, cv2.COLORMAP_JET)
            masked_image = colored_heatmap * 1.0 + resized_image * 0

            width_begin = heatmap_width * (j + 1)
            width_end = heatmap_width * (j + 2)
            grid_image[height_begin:height_end, width_begin:width_end, :] = masked_image

        grid_image[height_begin:height_end, 0:heatmap_width, :] = resized_image

    cv2.imwrite(file_name, grid_image)


def save_debug_images(config, input, meta, target, joints_pred, output, prefix):
    if not config.DEBUG.DEBUG:
        return

    if config.DEBUG.SAVE_BATCH_IMAGES_GT:
        save_batch_image_with_joints(input, meta['joints_xyv'], '{}_gt.jpg'.format(prefix))
    if config.DEBUG.SAVE_BATCH_IMAGES_PRED:
        save_batch_image_with_joints(input, joints_pred, '{}_pred.jpg'.format(prefix))
    if config.DEBUG.SAVE_HEATMAPS_GT:
        save_batch_heatmaps(input, target, '{}_hm_gt.jpg'.format(prefix))
    if config.DEBUG.SAVE_HEATMAPS_PRED:
        save_batch_heatmaps(input, output, '{}_hm_pred.jpg'.format(prefix))
