#!/usr/bin/env python
# coding=utf-8


import unittest

import modules.vdn as vdn


class Test(unittest.TestCase):

    def test_eval(self):
        """Available backbones: resnet34 (default), resnet18, resnet50.

        """
        vdn_instance = vdn.VectorDetectionNetwork(backbone='resnet34')
        vdn_instance.eval()


if __name__ == '__main__':
    unittest.main()
