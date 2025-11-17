import torch
import comfy.sample
import comfy.samplers
import comfy.model_management
import latent_preview
import logging

logger = logging.getLogger("Comfyui-PainterSampler")

# 官方 common_ksampler 副本（保持原样）
def common_ksampler(model, seed, steps, cfg, sampler_name, scheduler, positive, negative, latent,
                    denoise=1.0, disable_noise=False, start_step=None, last_step=None,
                    force_full_denoise=True, noise_mask=None, callback=None, disable_pbar=False):
    latent_image = latent["samples"]
    latent_image = comfy.sample.fix_empty_latent_channels(model, latent_image)
    if disable_noise:
        noise = torch.zeros_like(latent_image)
    else:
        batch_inds = latent.get("batch_index", None)
        noise = comfy.sample.prepare_noise(latent_image, seed, batch_inds)
    noise_mask = latent.get("noise_mask", None)
    if callback is None:
        callback = latent_preview.prepare_callback(model, steps)
    disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
    samples = comfy.sample.sample(model, noise, steps, cfg, sampler_name, scheduler,
                                  positive, negative, latent_image,
                                  denoise=denoise, disable_noise=disable_noise,
                                  start_step=start_step, last_step=last_step,
                                  force_full_denoise=force_full_denoise,
                                  noise_mask=noise_mask, callback=callback,
                                  disable_pbar=disable_pbar, seed=seed)
    out = latent.copy()
    out["samples"] = samples
    return out


class PainterSampler:
    """
    Dual-Model Tandem Sampler: 100% replicates the generation effect of the official KSamplerAdvanced,
    with only dual-model input integrated.
    
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "high_model": ("MODEL",),
                "low_model": ("MODEL",),
                "add_noise": (["enable", "disable"], {"default": "enable"}),
                "noise_seed": ("INT", {
                    "default": 0, "min": 0, "max": 0xffffffffffffffff,
                    "control_after_generate": True
                }),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
                # === 新增：高噪声阶段 cfg
                "high_cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step": 0.01}),
                # === 新增：低噪声阶段 cfg
                "low_cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step": 0.01}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS,),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "latent_image": ("LATENT",),
                "start_at_step": ("INT", {"default": 0, "min": 0, "max": 10000}),
                "switch_at_step": ("INT", {"default": 2, "min": 1, "max": 10000}),
                "end_at_step": ("INT", {"default": 10000, "min": 0, "max": 10000}),
                "return_leftover_noise": (["disable", "enable"], {"default": "disable"}),
            }
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latent",)
    FUNCTION = "sample"
    CATEGORY = "sampling/painter"

    OUTPUT_NODE = False
    ICON = "KSampler"
    WIDTH = 210

    # === 改动：新增 high_cfg / low_cfg 参数
    def sample(self, high_model, low_model, add_noise, noise_seed, steps,
               high_cfg, low_cfg,               # <---
               sampler_name, scheduler,
               positive, negative, latent_image,
               start_at_step, switch_at_step, end_at_step, return_leftover_noise):

        # 参数标准化
        start_at_step = max(0, start_at_step)
        end_at_step = min(steps, max(start_at_step + 2, end_at_step))
        switch_at_step = max(start_at_step + 1, min(switch_at_step, end_at_step - 1))

        force_full_denoise = (return_leftover_noise == "disable")
        disable_noise = (add_noise == "disable")

        callback = latent_preview.prepare_callback(high_model, steps)
        disable_pbar = not getattr(comfy.utils, 'PROGRESS_BAR_ENABLED', True)

        # 第一阶段：高噪声模型
        if start_at_step < switch_at_step:
            logger.info(f"Phase 1: High-noise [{start_at_step}→{switch_at_step}]  cfg={high_cfg}")
            latent_stage1 = latent_image.copy()
            latent_stage1["samples"] = latent_image["samples"].clone()

            samples_stage1 = common_ksampler(
                high_model, noise_seed, steps, high_cfg, sampler_name, scheduler,  # high_cfg
                positive, negative, latent_stage1,
                denoise=1.0, disable_noise=disable_noise,
                start_step=start_at_step, last_step=switch_at_step,
                force_full_denoise=False,
                noise_mask=latent_image.get("noise_mask", None),
                callback=callback, disable_pbar=disable_pbar
            )
            current_latent = samples_stage1
        else:
            current_latent = latent_image

        # 第二阶段：低噪声模型
        logger.info(f"Phase 2: Low-noise [{switch_at_step}→{end_at_step}]  cfg={low_cfg}")
        samples_final = common_ksampler(
            low_model, noise_seed, steps, low_cfg, sampler_name, scheduler,  # low_cfg
            positive, negative, current_latent,
            denoise=1.0, disable_noise=True,
            start_step=switch_at_step, last_step=end_at_step,
            force_full_denoise=force_full_denoise,
            noise_mask=current_latent.get("noise_mask", None),
            callback=callback, disable_pbar=disable_pbar
        )

        return (samples_final,)


NODE_CLASS_MAPPINGS = {"PainterSampler": PainterSampler}
NODE_DISPLAY_NAME_MAPPINGS = {"PainterSampler": "Painter Sampler Advanced"}
