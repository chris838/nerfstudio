"""
NeRF-W (NeRF in the wild) implementation.
"""

import torch
from torchmetrics import PeakSignalNoiseRatio, StructuralSimilarityIndexMeasure
from torchmetrics.image.lpip import LearnedPerceptualImagePatchSimilarity

from pyrad.nerf.field_modules.encoding import NeRFEncoding
from pyrad.nerf.field_modules.field_heads import FieldHeadNames
from pyrad.nerf.fields.nerf_field import NeRFField
from pyrad.nerf.fields.nerfw_field import VanillaNerfWField
from pyrad.nerf.graph.base import Graph
from pyrad.nerf.loss import MSELoss
from pyrad.nerf.ray_sampler import PDFSampler, UniformSampler
from pyrad.nerf.renderers import AccumulationRenderer, DepthRenderer, RGBRenderer, UncertaintyRenderer
from pyrad.structures import colors
from pyrad.structures.rays import RayBundle
from pyrad.utils import visualization, writer


class NerfWGraph(Graph):
    """NeRF-W graph"""

    def __init__(
        self,
        intrinsics=None,
        camera_to_world=None,
        near_plane=2.0,
        far_plane=6.0,
        num_coarse_samples=64,
        num_importance_samples=128,
        **kwargs,
    ) -> None:
        self.near_plane = near_plane
        self.far_plane = far_plane
        self.num_coarse_samples = num_coarse_samples
        self.num_importance_samples = num_importance_samples
        self.field_coarse = None
        self.field_fine = None
        self.num_images = len(intrinsics)
        self.appearance_embedding_dim = 48
        self.transient_embedding_dim = 16
        super().__init__(intrinsics=intrinsics, camera_to_world=camera_to_world, **kwargs)

    def populate_fields(self):
        """Set the fields."""

        position_encoding = NeRFEncoding(
            in_dim=3, num_frequencies=10, min_freq_exp=0.0, max_freq_exp=8.0, include_input=True
        )
        direction_encoding = NeRFEncoding(
            in_dim=3, num_frequencies=4, min_freq_exp=0.0, max_freq_exp=4.0, include_input=True
        )

        self.field_coarse = NeRFField(position_encoding=position_encoding, direction_encoding=direction_encoding)
        self.field_fine = VanillaNerfWField(
            num_images=self.num_images,
            position_encoding=position_encoding,
            direction_encoding=direction_encoding,
            appearance_embedding_dim=self.appearance_embedding_dim,
            transient_embedding_dim=self.transient_embedding_dim,
        )

    def populate_misc_modules(self):
        # samplers
        self.sampler_uniform = UniformSampler(num_samples=self.num_coarse_samples)
        self.sampler_pdf = PDFSampler(num_samples=self.num_importance_samples)

        # renderers
        self.renderer_rgb = RGBRenderer(background_color=colors.BLACK)
        self.renderer_accumulation = AccumulationRenderer()
        self.renderer_depth = DepthRenderer()
        self.renderer_uncertainty = UncertaintyRenderer()

        # losses
        self.rgb_loss = MSELoss()

        # metrics
        self.psnr = PeakSignalNoiseRatio(data_range=1.0)
        self.ssim = StructuralSimilarityIndexMeasure()
        self.lpips = LearnedPerceptualImagePatchSimilarity()

    def get_param_groups(self):
        param_groups = {}
        param_groups["fields"] = list(self.field_coarse.parameters()) + list(self.field_fine.parameters())
        return param_groups

    def get_outputs(self, ray_bundle: RayBundle):
        # uniform sampling
        ray_samples_uniform = self.sampler_uniform(ray_bundle)

        # coarse field
        field_outputs_coarse = self.field_coarse.forward(ray_samples_uniform.to_point_samples())
        weights_coarse = ray_samples_uniform.get_weights(field_outputs_coarse[FieldHeadNames.DENSITY])
        rgb_coarse = self.renderer_rgb(
            rgb=field_outputs_coarse[FieldHeadNames.RGB],
            weights=weights_coarse,
        )
        depth_coarse = self.renderer_depth(weights_coarse, ray_samples_uniform.ts)

        # pdf sampling
        ray_samples_pdf = self.sampler_pdf(ray_bundle, ray_samples_uniform, weights_coarse)

        # fine field
        field_outputs_fine = self.field_fine.forward(ray_samples_pdf.to_point_samples())

        # fine weights
        weights_fine = ray_samples_pdf.get_weights(
            field_outputs_fine[FieldHeadNames.DENSITY] + field_outputs_fine[FieldHeadNames.TRANSIENT_DENSITY]
        )
        weights_fine_static = ray_samples_pdf.get_weights(field_outputs_fine[FieldHeadNames.DENSITY])
        weights_fine_transient = ray_samples_pdf.get_weights(field_outputs_fine[FieldHeadNames.TRANSIENT_DENSITY])

        # rgb
        rgb_fine_static_component = self.renderer_rgb(
            rgb=field_outputs_fine[FieldHeadNames.RGB],
            weights=weights_fine,
        )
        rgb_fine_transient_component = self.renderer_rgb(
            rgb=field_outputs_fine[FieldHeadNames.TRANSIENT_RGB],
            weights=weights_fine,
        )
        rgb_fine = rgb_fine_static_component + rgb_fine_transient_component
        rgb_fine_static = self.renderer_rgb(
            rgb=field_outputs_fine[FieldHeadNames.RGB],
            weights=weights_fine_static,
        )

        # density
        density_transient = field_outputs_fine[FieldHeadNames.TRANSIENT_DENSITY]

        # depth
        depth_fine = self.renderer_depth(weights_fine, ray_samples_pdf.ts)
        depth_fine_static = self.renderer_depth(weights_fine_static, ray_samples_pdf.ts)

        # uncertainty
        uncertainty = self.renderer_uncertainty(field_outputs_fine[FieldHeadNames.UNCERTAINTY], weights_fine_transient)

        outputs = {
            "rgb_coarse": rgb_coarse,  # (num_rays, 3)
            "rgb_fine": rgb_fine,
            "rgb_fine_static": rgb_fine_static,
            "depth_coarse": depth_coarse,
            "depth_fine": depth_fine,
            "depth_fine_static": depth_fine_static,
            "density_transient": density_transient,  # (num_rays, num_samples, 1)
            "uncertainty": uncertainty,  # (num_rays, 1)
        }
        return outputs

    def get_loss_dict(self, outputs, batch):
        device = outputs["rgb_coarse"].device
        pixels = batch["pixels"].to(device)
        rgb_coarse = outputs["rgb_coarse"]
        rgb_fine = outputs["rgb_fine"]
        density_transient = outputs["density_transient"]
        betas = outputs["uncertainty"]
        rgb_loss_coarse = 0.5 * ((pixels - rgb_coarse) ** 2).sum(-1).mean()
        rgb_loss_fine = 0.5 * (((pixels - rgb_fine) ** 2).sum(-1) / (betas[..., 0] ** 2)).mean()
        uncertainty_loss = 0.5 * (3 + torch.log(betas)).mean()
        density_loss = density_transient.mean()

        loss_dict = {
            "rgb_loss_coarse": rgb_loss_coarse,
            "rgb_loss_fine": rgb_loss_fine,
            "uncertainty_loss": uncertainty_loss,
            "density_loss": density_loss,
        }
        return loss_dict

    def log_test_image_outputs(self, image_idx, step, batch, outputs):
        image = batch["image"]
        rgb_coarse = outputs["rgb_coarse"]
        rgb_fine = outputs["rgb_fine"]
        rgb_fine_static = outputs["rgb_fine_static"]
        depth_coarse = outputs["depth_coarse"]
        depth_fine = outputs["depth_fine"]
        depth_fine_static = outputs["depth_fine_static"]
        uncertainty = outputs["uncertainty"]

        depth_coarse = visualization.apply_depth_colormap(depth_coarse)
        depth_fine = visualization.apply_depth_colormap(depth_fine)
        depth_fine_static = visualization.apply_depth_colormap(depth_fine_static)
        uncertainty = visualization.apply_depth_colormap(uncertainty)

        row0 = torch.cat([image, uncertainty, torch.ones_like(rgb_fine)], dim=-2)
        row1 = torch.cat([rgb_fine, rgb_fine_static, rgb_coarse], dim=-2)
        row2 = torch.cat([depth_fine, depth_fine_static, depth_coarse], dim=-2)
        combined_image = torch.cat([row0, row1, row2], dim=-3)

        writer.put_image(name=f"img/image_idx_{image_idx}-nerfw", image=combined_image, step=step)

        mask = batch["mask"].repeat(1, 1, 3)
        writer.put_image(name=f"mask/image_idx_{image_idx}", image=mask, step=step)
