#!/usr/bin/env python
# coding=utf-8


import os
import time
import pprint
import shutil
import sys
import numpy as np

import cv2
import torch
import torch.backends.cudnn as cudnn
import torch.nn.parallel
import torch.nn.parallel
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import torchvision.transforms as transforms

import libs.core.config as lib_config
from libs.core.config import get_model_name

import libs.core.trainval as lib_function
from libs.core.inference import get_final_preds

import libs.core.loss as lib_loss

import libs.utils.utils as lib_util
from libs.utils.transforms import get_affine_transform

import libs.dataset as lib_dataset
import libs.models.vdn_model as vdn_model

import utils.vis.util as vis_util
from PIL import ImageDraw

from typing import Optional

sys.path.append(".")
save = False

root_dir = '/VDN'
model_path = os.path.join(root_dir, "weights/vdn_best.pth.tar")


class VectorDetectionNetwork:
    """
    """

    def __init__(self, train=False):
        if train:
            vdn_config = os.path.join(root_dir, "cfgs/resnet152/train.yaml")
        else:
            vdn_config = os.path.join(root_dir, "cfgs/resnet152/eval.yaml")

        lib_config.update_config(vdn_config)

        cudnn.benchmark = lib_config.config.CUDNN.BENCHMARK
        torch.backends.cudnn.deterministic = lib_config.config.CUDNN.DETERMINISTIC
        torch.backends.cudnn.enabled = lib_config.config.CUDNN.ENABLED

        if not train:
            model = vdn_model.get_vdn_resnet(lib_config.config, is_train=False)
            model.load_state_dict({k.replace('module.', ''): v for k, v in torch.load(model_path).items()})
        else:
            model = vdn_model.get_vdn_resnet(lib_config.config, is_train=True)

        self.gpus = [int(i) for i in lib_config.config.GPUS.split(',')]
        self.model = torch.nn.DataParallel(model, device_ids=self.gpus).cuda()

        self.output_dir = os.path.join(root_dir, 'output/demo')
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def train(self):
        """
        """
        cfgs = lib_config.config

        logger, final_output_dir, tb_log_dir = lib_util.create_logger(cfgs, 'train')
        logger.info(pprint.pformat(cfgs))

        # copy model file for reference
        this_dir = os.path.dirname(__file__)
        shutil.copy2(os.path.join(this_dir, '../libs/models', cfgs.MODEL.NAME + '.py'), final_output_dir)

        # define loss function (criterion) and optimizer
        crit_heatmap = lib_loss.JointsMSELoss(use_target_weight=cfgs.LOSS.USE_TARGET_WEIGHT).cuda()
        crit_vector = lib_loss.RegL2Loss().cuda()

        optimizer = lib_util.get_optimizer(cfgs, self.model)

        lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(
            optimizer, cfgs.TRAIN.LR_STEP, cfgs.TRAIN.LR_FACTOR
        )

        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                         std=[0.229, 0.224, 0.225])

        train_dataset = lib_dataset.CoCo(
            cfgs,
            cfgs.DATASET.ROOT,
            cfgs.DATASET.TRAIN_SET,
            True,
            transform=transforms.Compose([
                transforms.ToTensor(),
                normalize, ])
        )
        valid_dataset = lib_dataset.CoCo(
            cfgs,
            cfgs.DATASET.ROOT,
            cfgs.DATASET.TEST_SET,
            False,
            transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ])
        )
        train_loader = torch.utils.data.DataLoader(
            train_dataset,
            batch_size=cfgs.TRAIN.BATCH_SIZE * len(self.gpus),
            shuffle=cfgs.TRAIN.SHUFFLE,
            num_workers=cfgs.WORKERS,
            pin_memory=True,

        )
        valid_loader = torch.utils.data.DataLoader(
            valid_dataset,
            batch_size=cfgs.TEST.BATCH_SIZE * len(self.gpus),
            shuffle=False,
            num_workers=cfgs.WORKERS,
            pin_memory=True
        )

        best_perf = 0.0
        for epoch in range(cfgs.TRAIN.BEGIN_EPOCH, cfgs.TRAIN.END_EPOCH):
            # train for one epoch
            lib_function.train(cfgs, train_loader, self.model, crit_heatmap, crit_vector,
                               optimizer, epoch, final_output_dir)

            #  In PyTorch 1.1.0 and later, you should call optimizer.step() before lr_scheduler.step().
            lr_scheduler.step()

            # evaluate on validation set
            perf_indicator = lib_function.validate(cfgs, valid_loader, valid_dataset, self.model,
                                                   crit_heatmap, crit_vector, final_output_dir, epoch)

            if perf_indicator > best_perf:
                best_perf = perf_indicator
                best_model = True
            else:
                best_model = False

            logger.info('=> saving checkpoint to {}'.format(final_output_dir))
            lib_util.save_checkpoint({
                'epoch': epoch + 1,
                'model': get_model_name(cfgs),
                'state_dict': self.model.state_dict(),
                'perf': perf_indicator,
                'optimizer': optimizer.state_dict(),
            }, best_model, final_output_dir)

            if epoch % 40 == 0 and epoch != 0:
                torch.save(self.model.module.state_dict(), os.path.join(final_output_dir, f'{epoch}.pth.tar'))

        final_model_state_file = os.path.join(final_output_dir, 'final_state.pth.tar')
        logger.info('saving final model state to {}'.format(final_model_state_file))
        torch.save(self.model.module.state_dict(), final_model_state_file)

    def eval(self):
        cfgs = lib_config.config

        logger, final_output_dir, tb_log_dir = lib_util.create_logger(cfgs, 'eval')
        logger.info(pprint.pformat(cfgs))

        # define loss function (criterion) and optimizer
        crit_heatmap = lib_loss.JointsMSELoss(use_target_weight=cfgs.LOSS.USE_TARGET_WEIGHT).cuda()
        crit_vector = lib_loss.RegL2Loss().cuda()

        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                         std=[0.229, 0.224, 0.225])

        valid_dataset = lib_dataset.CoCo(
            cfgs,
            cfgs.DATASET.ROOT,
            cfgs.DATASET.TEST_SET,
            False,
            transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ])
        )

        valid_loader = torch.utils.data.DataLoader(
            valid_dataset,
            batch_size=cfgs.TEST.BATCH_SIZE * len(self.gpus),
            shuffle=False,
            num_workers=cfgs.WORKERS,
            pin_memory=True
        )

        # evaluate on validation or test set (depending on the cfg)
        perf_indicator = lib_function.validate(cfgs, valid_loader, valid_dataset, self.model,
                                               crit_heatmap, crit_vector, final_output_dir, 200)

    # @torchsnooper.snoop()
    def get_vectors(self, roi_image: np.ndarray, verbose: Optional[str] = None):
        """Given roi_image of pointer-type meter dial face, return vectors represented by 2 points [[[ps_x, ps_y],
        [pe_x, pe_y]], ...]. Here ps is for start point, and pe is for end point.

        :param roi_image: 
        :param verbose: result image name
        :return:
        """
        model = self.model
        cfgs = lib_config.config

        image_height = roi_image.shape[0]
        image_width = roi_image.shape[1]

        center = np.array([image_width * 0.5, image_height * 0.5], dtype=np.float32)

        shape = np.array([image_width / 200.0, image_height / 200.0], dtype=np.float32)
        rotation = 0

        trans = get_affine_transform(center, shape, rotation, cfgs.MODEL.IMAGE_SIZE)

        net_input = cv2.warpAffine(roi_image, trans,
                                   (int(cfgs.MODEL.IMAGE_SIZE[0]), int(cfgs.MODEL.IMAGE_SIZE[1])),
                                   flags=cv2.INTER_LINEAR)

        transform = transforms.Compose([transforms.ToTensor(),
                                        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                                             std=[0.229, 0.224, 0.225]),
                                        ])

        net_input = transform(net_input).unsqueeze(0)
        # switch to evaluate mode
        model.eval()

        start = time.time()
        with torch.no_grad():
            # compute output heat map
            output_hm, output_vm = model(net_input)
            preds_start, preds_end, _, maxvals = get_final_preds(output_hm.clone().cpu().numpy(),
                                                                 output_vm.clone().cpu().numpy(),
                                                                 np.asarray([center]), np.asarray([shape]))

            spent = time.time() - start
            print(spent)

            # squeeze the batch and joint dims
            preds_start = np.squeeze(preds_start, (0, 1))
            preds_end = np.squeeze(preds_end, (0, 1))
            maxvals = np.squeeze(maxvals, (0, 1))
            # print('pred shape', preds_start.shape)
            # print('maxvals shape', maxvals.shape)

            # print("points", preds_start[0], "vectors", preds_end[0], "\n", "score", maxvals)
            if verbose is not None:
                roi_pil = vis_util.cv_img_to_pil(roi_image)
                draw = ImageDraw.Draw(roi_pil)

                for i, (start_point, end_point) in enumerate(zip(preds_start, preds_end)):
                    # start_point (k, 2); end_point (k)
                    print("initial_point", start_point, "end_point", end_point)
                    vis_util.apply_dot(draw, start_point, image_width, image_height, idx=i)
                    vis_util.apply_line(draw, start_point, end_point, image_width, image_height, idx=i)

                output_image = vis_util.pil_img_to_cv(roi_pil)
                cv2.imwrite(os.path.join(self.output_dir, f'res_{verbose}.jpg'), output_image)

                vis_util.save_batch_heatmaps(net_input, output_hm,
                                             os.path.join(self.output_dir, f'hmap_{verbose}.jpg'))
                vis_util.save_batch_vectormaps(net_input, output_vm,
                                               os.path.join(self.output_dir, f'vmap_{verbose}.jpg'))

            return preds_start, preds_end, maxvals
