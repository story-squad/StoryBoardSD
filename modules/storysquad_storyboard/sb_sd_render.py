import asyncio
import base64
import doctest
import random
from typing import Any, List
import httpx
from PIL import Image
import os

import requests
import json
from modules.storysquad_storyboard import env
from modules.storysquad_storyboard.sb_rendering import SBMultiSampleArgs, SBImageResults, SBIRenderParams, \
    SBIHyperParams

from interface.sb_render_interface import SBRenderInterface

wrap_around = 0
AWS_SERVER_IPS = None
SBRI: SBRenderInterface = None


def get_fixed_seed(seed):
    import time
    # setup the random number generator correctly using time and the seed
    random.seed(time.time() + seed)

    if seed is None or seed == '' or seed == -1:
        return int(random.randrange(4294967294))

    return seed


async def setup_render_servers():
    global x, AWS_SERVER_IPS, SBRI

    if env.STORYBOARD_USE_AWS:

        sbri_url = os.environ.get('STORYBOARD_SERVER_CONTROLLER_URL')
        sbri_user = os.environ.get('STORYBOARD_USER_NAME')
        sbri_pass = os.environ.get('STORYBOARD_PASSWORD')
        SBRI = SBRenderInterface(sb_controller_url=sbri_url,
                                 sb_controller_user=sbri_user,
                                 sb_controller_pass=sbri_pass,
                                 )

        AWS_SERVER_IPS = None
        render_server_urls = None

        if render_server_urls is None:

            if SBRI.server_ips and len(SBRI.server_ips) == len(SBRI.iids):
                pass
            else:
                AWS_SERVER_IPS = await SBRI.start_all_render_servers_and_apis(verbose=True)

        render_server_urls = \
            [f"http://{x}:7860/sdapi/v1/txt2img" for x in AWS_SERVER_IPS]

        env.STORYBOARD_RENDER_SERVER_URLS.clear()
        env.STORYBOARD_RENDER_SERVER_URLS.extend(render_server_urls)

    else:
        env.STORYBOARD_RENDER_SERVER_URLS = env.STORYBOARD_RENDER_SERVER_URLS


def get_sd_txt_2_image_params_from_story_board_params(sb_iparams: SBMultiSampleArgs):
    # convert the story board params to StableDiffusionProcessingTxt2Img params
    if not isinstance(sb_iparams, SBMultiSampleArgs):
        raise TypeError(f"sb_iparams must be of type SBMultiSampleArgs, but is {type(sb_iparams)}")

    # get the hyper params
    prompt = sb_iparams.hyper.prompt
    negative_prompt = sb_iparams.hyper.negative_prompt
    steps = sb_iparams.hyper.steps
    seed = sb_iparams.hyper.seed
    subseed = sb_iparams.hyper.subseed
    subseed_strength = sb_iparams.hyper.subseed_strength
    cfg_scale = sb_iparams.hyper.cfg_scale

    # get the render params

    width = sb_iparams.render.width
    height = sb_iparams.render.height
    restore_faces = sb_iparams.render.restore_faces
    tiling = sb_iparams.render.tiling
    batch_count = sb_iparams.render.batch_count
    batch_size = sb_iparams.render.batch_size

    if isinstance(prompt, list):
        if batch_size > len(prompt):
            batch_size = len(prompt)
    else:
        batch_size = 1

    sampler_index = sb_iparams.render.sampler_index
    sampler_name = sb_iparams.render.sampler_name

    # convert the render params to the StableDiffusionProcessingTxt2Img params
    if "shared" not in globals():
        shared = lambda: None
        shared.sd_model = None
        opts = lambda: None
        opts.outdir_samples = None
        opts.outdir_grids = None
        opts.outdir_txt2img_samples = None
        opts.outdir_txt2img_grids = None
        try:
            tmp = StableDiffusionProcessingTxt2Img(
                sd_model=shared.sd_model,
                outpath_samples=opts.outdir_samples or opts.outdir_txt2img_samples,
                outpath_grids=opts.outdir_grids or opts.outdir_txt2img_grids,
                prompt=prompt,
                styles=["None", "None"],
                negative_prompt=negative_prompt if type(negative_prompt) is not list else negative_prompt[0],
                seed=seed,
                subseed=subseed,
                subseed_strength=subseed_strength,  # if type(subseed_strength) is not list else subseed_strength[0],
                sampler_name=sampler_name if type(sampler_name) is not list else sampler_name[0],
                batch_size=batch_size if type(batch_size) is not list else batch_size[0],
                n_iter=1,
                steps=steps if type(steps) is not list else steps[0],
                cfg_scale=cfg_scale if type(cfg_scale) is not list else cfg_scale[0],
                width=width if type(width) is not list else width[0],
                height=height if type(height) is not list else height[0],
                restore_faces=restore_faces if type(restore_faces) is not list else restore_faces[0],
                tiling=tiling if type(tiling) is not list else tiling[0],
                seed_enable_extras=True
            )
        except NameError as e:
            #print(e)
            if "StableDiffusionProcessingTxt2Img" in str(e):
                # likely doing a test so just return a dummy object
                tmp = lambda: None
                tmp.sd_model = shared.sd_model
                tmp.outpath_samples = opts.outdir_samples or opts.outdir_txt2img_samples
                tmp.outpath_grids = opts.outdir_grids or opts.outdir_txt2img_grids
                tmp.prompt = [prompt] if isinstance(prompt, str) else prompt
                tmp.styles = ["None", "None"]
                tmp.negative_prompt = [negative_prompt] if isinstance(negative_prompt, str) else negative_prompt
                tmp.seed = seed
                tmp.subseed = subseed
                tmp.subseed_strength = subseed_strength
                tmp.sampler_name = sampler_name if isinstance(sampler_name, str) else sampler_name[0]
                tmp.batch_size = 1
                tmp.n_iter = 1
                tmp.steps = 6
                tmp.cfg_scale = 7.0
                tmp.width = 512
                tmp.height = 512
                tmp.restore_faces = False
                tmp.tiling = False
                tmp.seed_enable_extras = True

    if isinstance(tmp.prompt, str):
        tmp.prompt = [tmp.prompt]
    return tmp


def storyboard_call_multi(params: SBMultiSampleArgs, *args, **kwargs) -> SBImageResults:
    p = get_sd_txt_2_image_params_from_story_board_params(params)

    p.scripts = modules.scripts.scripts_txt2img

    # turn all -1 seeds to random values ala modules.processing.get_fixed_seed(-1)
    for i in range(len(p.seed)):
        if p.seed[i] == -1:
            p.seed[i] = modules.processing.get_fixed_seed(-1)
    p.do_not_save_samples = True

    try:
        processed = process_images(p)
    except Exception as e:
        print(e)
        # try to process each prompt separately
        results = []
        for i in range(len(params.combined.hyper.prompt)):
            try:
                sbim = params[i]
                p = get_sd_txt_2_image_params_from_story_board_params(sbim)
                results.append(process_images(p))
            except Exception as e:
                print(e)
                results.append(None)

    shared.total_tqdm.clear()

    generation_info_js = processed.js()
    if opts.samples_log_stdout:
        print(generation_info_js)

    if opts.do_not_show_images:
        processed.images = []
    sb_results = SBImageResults(
        processed=processed,
    )

    return sb_results


async def storyboard_call_endpoint(params: SBMultiSampleArgs, *args, **kwargs) -> SBImageResults:
    """
    Call the render server endpoint to render the images. The servers are chosen in a round-robin fashion.
    """
    server_url = get_next_server_url()
    p = get_sd_txt_2_image_params_from_story_board_params(params)
    # p.scripts = modules.scripts.scripts_txt2img
    set_random_seeds(p)
    p.do_not_save_samples = True
    processed = await call_json_api_endpoint_async(url=server_url, data=p)
    images = processed["images"]
    generation_info_js = processed["parameters"]
    sb_results = create_sb_image_results(images, generation_info_js)
    return sb_results


def get_next_server_url() -> str:
    """
    Choose the next server URL in a round-robin fashion.
    """
    global wrap_around
    server_url = env.STORYBOARD_RENDER_SERVER_URLS[wrap_around]
    wrap_around = (wrap_around + 1) % len(env.STORYBOARD_RENDER_SERVER_URLS)
    return server_url


def set_random_seeds(params) -> None:
    """
    Turn all -1 seeds in the given parameters to random values.
    """

    for i, seed in enumerate(params.seed):
        if seed == -1:
            params.seed[i] = get_fixed_seed(-1)


def create_sb_image_results(images: List[bytes], generation_info_js: dict) -> SBImageResults:
    """
    Create an SBImageResults object from the given image data and generation information.
    """
    if "shared" in globals():
        shared.total_tqdm.clear()

    generation_info_js["images"] = images
    # if opts.do_not_show_images:
    #       images = []
    return SBImageResults(api_results=generation_info_js)


def decode_base64_to_image(encoding):
    if encoding.startswith("data:image/"):
        encoding = encoding.split(";")[1].split(",")[1]
    from io import BytesIO
    image = Image.open(BytesIO(base64.b64decode(encoding)))
    return image


def call_json_api_endpoint(url, data):
    headers = {'content-type': 'application/json'}
    if isinstance(data, StableDiffusionProcessingTxt2Img):
        data = vars(data)
        data.pop("scripts", None)
        data.pop("script_args")
        data.pop("s_tmax")
    res = requests.post(url, data=json.dumps(data), headers=headers)
    rj = res.json()
    rj["images"] = [decode_base64_to_image(i) for i in rj["images"]]
    return rj


async def call_json_api_endpoint_async(url: str, data: Any) -> dict:
    headers = {'content-type': 'application/json'}
    # if isinstance(data, StableDiffusionProcessingTxt2Img):
    data = vars(data)
    if "scripts" in data:
        data.pop("scripts", None)
    if "script_args" in data:
        data.pop("script_args")
    if "s_tmax" in data:
        data.pop("s_tmax")

    timeout = httpx.Timeout(300.0)  # Set the read timeout to 5 minutes

    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(url=url,
                                data=json.dumps(data),
                                headers=headers)

    rj = res.json()
    rj["images"] = [decode_base64_to_image(i) for i in rj["images"]]
    return rj


async def storyboard_call_endpoint_split_batch(params: SBMultiSampleArgs, batch_size: int, *args,
                                               **kwargs) -> SBImageResults:
    """
    Call the render server endpoint to render the images in batches. The servers are chosen in a round-robin fashion.
    """
    import time
    start_time = time.time()
    all_images = []
    all_json = []
    async_tasks = []
    for i in range(0, len(params), batch_size):
        batch_params = params[i:i + batch_size]
        task = storyboard_call_endpoint(batch_params, *args, **kwargs)
        async_tasks.append(task)

    async_results = await asyncio.gather(*async_tasks)

    out_res = async_results[0]
    for result in async_results[1:]:
        out_res += result
    end_time = time.time()
    print(f"Total time: {end_time - start_time}")
    return out_res


if __name__ == "__main__":
    # requires StoryBoardAPI server running locallay on port 7860
    # from modules.processing import StableDiffusionProcessingTxt2Img
    test_prompts = ["red car",
                    "blue boat",
                    "green bush",
                    "yellow jacket",
                    "orange sunrise",
                    "purple flower",
                    "pink dress",
                    "black cat",
                    "white dog"]

    test_neg_prompts = ["old car",
                        "amateur",
                        "close up",
                        "bug",
                        "amateur",
                        "photograph",
                        "woman",
                        "adult cat",
                        "adult dog"]
    env.STORYBOARD_USE_AWS = False
    env.STORYBOARD_RENDER_SERVER_URLS = ["http://127.0.0.1:7861/sdapi/v1/txt2img"]

    test_render_params = SBIRenderParams()
    test_hyper_params = SBIHyperParams(
        prompt=test_prompts[0],
        seed=-1,
        subseed=-1,
        cfg_scale=7,
        negative_prompt=test_neg_prompts[0],
        subseed_strength=0,
        steps=30,
    )
    test_render_params.batch_size = 4

    for i in range(0, 8):
        test_hyper_params += SBIHyperParams(
            prompt=test_prompts[i + 1],
            seed=-1,
            subseed=-1,
            cfg_scale=7,
            negative_prompt=test_neg_prompts[i + 1],
            subseed_strength=0,
            steps=30,
        )
    test_SBMSA = SBMultiSampleArgs(render=test_render_params, hyper=test_hyper_params)
    print(f"Doing Tests")


    async def test(SBMSA=test_SBMSA):
        import time
        # this is how storyboard calls this module
        start_time = time.time()
        # this is for a single batch that is to be rendered in a single async call
        result.append(await storyboard_call_endpoint(SBMSA))
        end_time = time.time()
        print(f"call endpoint Total time: {end_time - start_time}")

        start_time = time.time()
        # this is for a single batch that is to be split into multiple async calls
        result.append(await storyboard_call_endpoint_split_batch(SBMSA, batch_size=1))
        end_time = time.time()
        print(f"split batch Total time: {end_time - start_time}")


    if input("would you like to proceed with an local render render test? (y/n)") == "y":
        result = []
        asyncio.run(test())
        print(result)
        print(f"expecting 9 images, got {len(result[0].all_images)}")
        assert len(result[0].all_images) == 9
        assert len(result[1].all_images) == 9

        if input("compare all results?") == "y":
            import numpy as np

            pairs = zip(result[0].all_images, result[1].all_images)
            for i, (a, b) in enumerate(pairs):
                print(f"comparing image {i}")
                npa = np.array(a, dtype=np.float32)
                npb = np.array(b, dtype=np.float32)
                diff = np.abs(npa - npb) / 255
                print(f" max diff:  {np.max(diff):.3f},"
                      f" min diff:  {np.min(diff):.3f},"
                      f" mean diff: {np.mean(diff):.3f}")
                if np.mean(diff) > 0.5:
                    print("WARNING: mean diff is greater than 5%")
                    a.show()
                    b.show()

        if input("show all results?") == "y":
            for r in result:
                for i in r.all_images:
                    i.show()

    if input("would you like to proceed with an SB render interface render? (y/n)") == "y":
        # just setting up some test data
        result = []
        for sbi in test_SBMSA:
            for i, seed in enumerate(sbi[0].seed):
                sbi[0].seed[i] = i

        # this is how we tell this module to use the SB renderer interface
        env.STORYBOARD_USE_AWS = True


        # crude simulation of the main loop
        async def main_loop():
            print(f'calling setup_render_servers')
            await setup_render_servers()
            print(f'calling setup_render_servers done')

            print(f'calling test')
            await test(test_SBMSA)
            print(f'calling test done')

            print(f'stopping render servers')
            await SBRI.stop_all_render_servers()
            print(f'stopping render servers done')


        asyncio.run(main_loop())
        print(result)
        print(f"expecting 9 images, got {len(result[0].all_images)}")
        assert len(result[0].all_images) == 9
        assert len(result[1].all_images) == 9

        if input("show all results?") == "y":
            for r in result:
                for i in r.all_images:
                    i.show()

        result[0].all_images[0].show()
        result[1].all_images[0].show()

    doctest.testmod()

else:
    # these are down here so that the doctests can run without importing the rest of the modules
    import modules.scripts
    import modules.shared as shared
    from modules.processing import StableDiffusionProcessingTxt2Img, \
        process_images
    from modules.shared import opts, cmd_opts
    from modules.api.models import StableDiffusionTxt2ImgProcessingAPI, StableDiffusionProcessingTxt2Img

    wrap_around = 0
    setup_render_servers()
