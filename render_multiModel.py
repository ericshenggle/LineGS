#
# Copyright (C) 2023, Inria
# GRAPHDECO research group, https://team.inria.fr/graphdeco
# All rights reserved.
#
# This software is free for non-commercial, research and evaluation use 
# under the terms of the LICENSE.md file.
#
# For inquiries contact  george.drettakis@inria.fr
#

import torch
from scene import Scene
import os
from tqdm import tqdm
from os import makedirs
from gaussian_renderer import render_multiModel
import torchvision
from utils.general_utils import safe_state
from argparse import ArgumentParser
from arguments import ModelParams, PipelineParams, get_combined_args
from gaussian_renderer import GaussianModel
from utils.image_utils import psnr
from utils.loss_utils import ssim
from lpipsPyTorch import lpips


def render_set(model_path, name, iteration, views, gaussians, pipeline, background, combinedDebug=False):
    render_path = os.path.join(model_path, name, "renders")
    gts_path = os.path.join(model_path, name, "gt")

    makedirs(render_path, exist_ok=True)
    makedirs(gts_path, exist_ok=True)

    avg_points = 0

    psnr_metric = []
    ssim_metric = []
    lpips_metric = []

    for idx, view in enumerate(tqdm(views, desc="Rendering progress")):
        _result = render_multiModel(view, gaussians, pipeline, background, combinedDebug=combinedDebug)
        rendering = _result["render"]
        avg_points = avg_points * idx / (idx + 1) + _result["num_points"] / (idx + 1)
        gt = view.original_image[0:3, :, :]

        psnr_metric.append(psnr(rendering, gt))
        ssim_metric.append(ssim(rendering, gt))
        lpips_metric.append(lpips(rendering, gt, net_type="vgg"))

        torchvision.utils.save_image(rendering, os.path.join(render_path, '{0:05d}'.format(idx) + ".png"))
        torchvision.utils.save_image(gt, os.path.join(gts_path, '{0:05d}'.format(idx) + ".png"))

    metrics = {
        "psnr": torch.tensor(psnr_metric).mean().item(),
        "ssim": torch.tensor(ssim_metric).mean().item(),
        "lpips": torch.tensor(lpips_metric).mean().item()
    }

    return avg_points, metrics

def render_sets(dataset : ModelParams, iteration : int, pipeline : PipelineParams, skip_train : bool, skip_test : bool, paths : list, combinedDebug : bool):
    with torch.no_grad():
        scene = Scene(dataset, None, load_iteration=iteration, shuffle=False, unloadGaussians=True)
        gaussians = {}
        gaussians_temp = GaussianModel(dataset.sh_degree)
        gaussians_temp.load_ply(os.path.join(dataset.model_path, "point_cloud", "iteration_" + str(iteration), "point_cloud.ply"))
        gaussians[dataset.model_path] = gaussians_temp
        for path in paths:
            gaussians_temp = GaussianModel(dataset.sh_degree)
            gaussians_temp.load_ply(os.path.join(path, "point_cloud", "iteration_" + str(iteration), "point_cloud.ply"))
            gaussians[path] = gaussians_temp

        bg_color = [1,1,1] if dataset.white_background else [0, 0, 0]
        background = torch.tensor(bg_color, dtype=torch.float32, device="cuda")


        p_model_path = os.path.dirname(dataset.model_path) + "_combined"
        new_model_path = os.path.basename(dataset.model_path)
        for path in paths:
            new_model_path += "_" + os.path.basename(path)
        model_path = os.path.join(p_model_path, new_model_path)

        # sort the gaussians by the number of points
        gaussians = {k: v for k, v in sorted(gaussians.items(), key=lambda item: item[1].get_xyz.shape[0], reverse=True)}

        train_points, test_points = 0, 0
        train_metrics, test_metrics = {}, {}

        if not skip_train:
            train_points, train_metrics = render_set(model_path, "train", scene.loaded_iter, scene.getTrainCameras(), list(gaussians.values()), pipeline, background, combinedDebug=combinedDebug)

        if not skip_test:
            test_points, test_metrics = render_set(model_path, "test", scene.loaded_iter, scene.getTestCameras(), list(gaussians.values()), pipeline, background, combinedDebug=combinedDebug)

        with open(os.path.join(model_path, "points.txt"), "w") as f:
            for path in gaussians.keys():
                f.write("Model: " + path + "\n")
                f.write("Points: " + str(gaussians[path].get_xyz.shape[0]) + "\n")
            f.write("\n")
            f.write("Model Combined: " + model_path + "\n")
            if not skip_train:
                f.write("Train Average Points: " + str(train_points) + "\n")
            if not skip_test:
                f.write("Test Average Points: " + str(test_points) + "\n")

        with open(os.path.join(model_path, "metrics.txt"), "w") as f:
            if not skip_train:
                f.write("Train Metrics: \n")
                for key, value in train_metrics.items():
                    f.write(key + ": " + str(value) + "\n")
                f.write("\n")
            if not skip_test:
                f.write("Test Metrics: \n")
                for key, value in test_metrics.items():
                    f.write(key + ": " + str(value) + "\n")
   

if __name__ == "__main__":
    # Start Time
    import time 
    start_time = time.time()

    # Set up command line argument parser
    parser = ArgumentParser(description="Testing script parameters")
    model = ModelParams(parser, sentinel=True)
    pipeline = PipelineParams(parser)
    parser.add_argument("--iteration", default=30000, type=int)
    parser.add_argument("--skip_train", action="store_true", default=True)
    parser.add_argument("--skip_test", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--model_paths", nargs="*", type=str, required=True, default=[])
    parser.add_argument("--combinedDebug", action="store_true")
    args = get_combined_args(parser)
    print("Rendering " + args.model_path)
    print("Combined with " + str(args.model_paths))

    # Initialize system state (RNG)
    safe_state(args.quiet)

    render_sets(model.extract(args), args.iteration, pipeline.extract(args), args.skip_train, args.skip_test, args.model_paths, args.combinedDebug)

    # End time
    end_time = time.time()

    # Save time
    from utils.system_utils import save_timeline
    save_timeline('render', start_time, end_time, args.model_path)