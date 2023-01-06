import copy
import os
import random
from dataclasses import dataclass
from typing import List, Callable, Any

import PIL
import numpy
import numpy as np
from PIL import Image

from modules.storysquad_storyboard.storyboard import SBIHyperParams, get_prompt_words_and_weights_list, \
    get_frame_seed_data, StoryBoardPrompt

# from .storyboard_gr import StoryBoardGradio

GTTS_SAMPLE_RATE = 24000.0
MAX_BATCH_SIZE = 10


@dataclass
class DefaultRender:
    fps: int = 24
    minutes: int = 2
    seconds: int = minutes * 60
    sections: int = 2
    num_frames = int(seconds * fps)
    min_frames_per_render = fps * 10
    num_frames_per_sctn = int(num_frames / sections)
    early_stop_seconds = int(60 * 30 * 16)  # 8 hours
    width = 512
    height = 512
    restore_faces = False
    tiling = False
    batch_count = 1
    batch_size = MAX_BATCH_SIZE
    sampler_index = 9


@dataclass
class SBIRenderParams:
    width: int = 512
    height: int = 512
    restore_faces: bool = False
    tiling: bool = False
    batch_count: int = 1
    batch_size: int = MAX_BATCH_SIZE
    sampler_index: int = 9


def quick_timer(func, *args, **kwargs):
    """
    this is a quick timer to time functions
    outputs the time and the result of the function

    """
    import time
    start = time.time()
    result = func(*args, **kwargs)
    end = time.time()
    return end - start, result


def get_img_diff(img1: Image, img2: Image) -> float:
    # convert the PIL images to a numpy arrays
    img1 = np.array(img1, dtype=np.float32)
    img2 = np.array(img2, dtype=np.float32)
    # find the mean difference per pixel
    diff = np.mean(np.abs(img1 - img2))
    return diff


def join_video_audio(video_file, audio_file):
    from moviepy.editor import VideoFileClip, AudioFileClip
    import random
    video = VideoFileClip(video_file)
    audio = AudioFileClip(audio_file)
    video.audio = audio
    # create a random name for the output file
    rnd_file = f"tmp_{random.randint(0, 1000000)}.mp4"
    video.write_videofile(rnd_file)
    return rnd_file


def remove_silence(data, data_sample_rate, desired_length_secs):
    """removes up to 20% of the audio that is closer to being considered silence.
    this is done by calculating how to long the audio would be if values below n percentile were removed
    using a smoothed 1000 sample representation of the audio
    """
    import numpy as np
    aud_len = len(data) / data_sample_rate
    if desired_length_secs / aud_len < .8:
        desired_length_secs = aud_len * .8

    data = data[0, :]
    conv = np.array(np.sin(np.arange(0, np.pi, np.pi / 1000)))[1:]
    conved = np.convolve(data, np.array(conv) / sum(conv), 'valid')

    sample_in_per_sample_out = int(len(conved) / 1000)
    out = np.zeros(0)
    conved = conved[::sample_in_per_sample_out]
    conved = conved / np.max(np.abs(conved))

    conv_sec_len = aud_len / 1000
    conved = np.abs(conved)
    tester = [conved[conved < np.percentile(conved, i / 10)].shape[0] * conv_sec_len for i in range(1, 1000)]
    tester = np.array(tester)
    percentile_to_use = np.abs(tester - desired_length_secs).argmin()
    percentile_value = np.percentile(conved, percentile_to_use / 10)

    for i in range(1000):
        if conved[i] < percentile_value:
            out = np.append(out, data[i * sample_in_per_sample_out:(i + 1) * sample_in_per_sample_out])
        else:
            pass

    out = np.stack([out, out])
    return out


def create_voice_over_for_storyboard(text_to_read, speech_speed, vo_length_sec):
    """
    >>> while True:
    ...  import os
    ...  # print the current directory
    ...  print(os.getcwd())
    ...  create_voice_over_for_storyboard("one two three four five six seven eight nine ten ", None, 10)
    ...  break
    """
    # time everything

    print("on_preview_audio")
    save_sample_rate = 44100
    # get the audio samples to aud_out while timing it

    t, (aud_out, aud_length_secs) = quick_timer(get_samples_from_gtts, text_to_read)
    print(f"get_samples_from_gtts: latency {t}")
    if aud_length_secs < vo_length_sec:
        t, (aud_out_slow, aud_length_secs_slow) = quick_timer(get_samples_from_gtts, text_to_read, slow=True)
    reg_dist = (vo_length_sec - aud_length_secs) ** 2
    slow_dist = (vo_length_sec - aud_length_secs_slow) ** 2
    if slow_dist < reg_dist:
        aud_out = aud_out_slow
        aud_length_secs = aud_length_secs_slow

    t, (data, data_sample_rate) = quick_timer(robot_voice_effect, aud_out)
    print(f'robot_voice_effect latency: {t}')

    audio_length_secs = max(*data.shape) / data_sample_rate
    if audio_length_secs > vo_length_sec:
        # remove silence of longer than .5 seconds
        t, data = quick_timer(remove_silence, data, data_sample_rate, vo_length_sec)
        print(f'remove_silence latency: {t}')

    audio_length_secs = max(*data.shape) / data_sample_rate

    rnd_file = write_mp3(data, data_sample_rate, save_sample_rate)

    return rnd_file, audio_length_secs


def write_mp3(effected, data_sample_rate, save_sample_rate):
    import numpy as np
    import random
    # import AudioArrayClip from moviepy
    from moviepy.audio.AudioClip import AudioArrayClip

    rnd_file = f"tmp_{random.randint(0, 1000000)}.wav"
    afc = AudioArrayClip(np.moveaxis(effected, 0, -1), data_sample_rate)
    afc.write_audiofile(rnd_file, fps=save_sample_rate)
    return rnd_file


def robot_voice_effect(aud_out):
    import numpy as np

    rate = 44100
    print(aud_out.shape)
    effected = np.copy(aud_out)
    divisor = 4
    # effected = board(effected, sample_rate=rate)
    effected = effected[:, ::divisor]
    offset = int(rate / 125)
    effected = effected[:, :effected.shape[1] - offset] + effected[:, offset:]
    effected = effected[:, :effected.shape[1] - offset] + effected[:, offset:]
    effected = effected[:, :effected.shape[1] - offset] + effected[:, offset:]
    effected = effected[:, :effected.shape[1] - offset] + effected[:, offset:]
    # normalize
    effected = effected / np.max(np.abs(effected))
    print(effected.shape)
    rate = int(rate / divisor)

    return effected, rate


def get_samples_from_gtts(text_to_read, slow=False) -> (numpy.ndarray, float):
    # TODO: this needs paraellization see https://gtts.readthedocs.io/en/latest/tokenizer.html#minimizing
    # 100 characters per request

    from gtts import gTTS
    from pedalboard.io import AudioFile
    import moviepy.editor as mpy
    import os

    audio = gTTS(
        text=text_to_read,
        lang="en",
        slow=slow,
    )
    audio.save("tmp.mp3")

    mpy.AudioFileClip("tmp.mp3").write_audiofile("tmp.wav")
    with AudioFile("tmp.wav", "r") as f:
        aud_out = f.read(f.frames)
    # get the length of the audio
    aud_length_secs = aud_out.shape[1] / GTTS_SAMPLE_RATE

    # delete the files
    os.remove("tmp.mp3")
    os.remove("tmp.wav")

    return aud_out, aud_length_secs


def make_mp4(input_path, filepath, filename, width, height, keep, fps=30) -> str:
    # TODO: use moviepy instead
    import subprocess
    import os
    import glob
    import uuid
    image_input_path = os.path.join(input_path, "%05d.png")
    mp4_path = os.path.join(filepath, f"{str(filename)}.mp4")
    # check if the file exists, if it does, change mp4_path to include part of a uuid
    if os.path.exists(mp4_path):
        print(f"file exists, changing name to {mp4_path}")
        mp4_path = os.path.join(filepath, f"{str(filename)}_{str(uuid.uuid4()).split('-')[-1]}.mp4")
    mp4_path = mp4_path.split(".mp4")[0] + ".mp4"
    # make the mp4

    cmd = [
        'ffmpeg',
        '-y',
        '-vcodec', 'png',
        '-r', str(fps),
        '-start_number', str(0),
        '-i', str(image_input_path),
        '-c:v', 'libx264',
        '-vf', 'scale=' + str(width) + ':' + str(height),
        '-pix_fmt', 'yuv420p',
        '-crf', '17',
        '-preset', 'veryfast',
        str(mp4_path)
    ]
    print(f'executing: {" ".join(cmd)}')
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        print(stderr)
        raise RuntimeError(stderr)
    if keep == False:
        for ifile in glob.glob(input_path + "/*.png"):
            os.remove(ifile)
    return mp4_path


def make_mp4_from_images(image_list, filepath, filename, width, height, keep, fps=30,
                         filter_func=False) -> str:
    import os
    from PIL import Image
    """Make an mp4 from a list of images, using make_mp4"""

    if filter_func:
        image_list = filter_func(image_list)
        # convert numpy array to PIL image
        image_list = [Image.fromarray((image * 255).astype("uint8")) for image in image_list]

    # save the images to a temp folder
    temp_folder_path = os.path.join(filepath, "temp")
    if not os.path.exists(temp_folder_path):
        try:
            os.mkdir(temp_folder_path)
        except OSError:
            # create the parent folder if it doesn't exist
            os.mkdir(os.path.dirname(temp_folder_path))
            os.mkdir(temp_folder_path)

    for i, img in enumerate(image_list):
        i_filename = os.path.join(temp_folder_path, f"{str(i).zfill(5)}.png")
        img.save(f"{i_filename}")

    # make the mp4

    return make_mp4(f"{temp_folder_path}", filepath, filename, width, height, keep, fps=DefaultRender.fps)


def get_frame_values_for_prompt_word_weights(prompts, num_frames):  # list[sections[frames[word:weight tuples]]]
    """
    >>> while True:
    ...     sections = get_frame_values_for_prompt_word_weights([CallArgsAsData(prompt= "(dog:1) cat:0"),CallArgsAsData(prompt= "(dog:1) cat:1"),CallArgsAsData(prompt= "(dog:0) cat:1")],4)
    ...     for section in sections:
    ...         print(section)
    ...     break
    [[('dog', 1.0), ('cat', 0.0)], [('dog', 1.0), ('cat', 0.3333333333333333)], [('dog', 1.0), ('cat', 0.6666666666666666)], [('dog', 1.0), ('cat', 1.0)]]
    [[('dog', 1.0), ('cat', 1.0)], [('dog', 0.6666666666666667), ('cat', 1.0)], [('dog', 0.33333333333333337), ('cat', 1.0)], [('dog', 0.0), ('cat', 1.0)]]
    """
    # get the weights for each word of each prompt in the prompts list returns a list of lists of tuples
    words_and_weights_for_prompts = [get_prompt_words_and_weights_list(p) for p in prompts]

    # define the two distinct sections of the storyboard_call_multi
    # each section is composed of frames, each frame has different weights for each word (probably) which results
    # in a unique image for the animation
    sections = [
        [words_and_weights_for_prompts[0], words_and_weights_for_prompts[1]],
        # transition from lattice pt 1 to 2
        [words_and_weights_for_prompts[1], words_and_weights_for_prompts[2]],
        # transition from lattice pt 2 to 3
    ]

    # interpolate the weights linearly for each word in each section for each frame and return the sections

    sections_frames = []
    for section in sections:
        start: tuple(str, float) = section[0]
        end: tuple(str, float) = section[1]
        word_frame_weights = []
        for i in range(num_frames):
            frame_weights = []
            for word_idx, word_at_pos in enumerate(start):
                # format like: ('dog', 0.0)
                word_start_weight = start[word_idx][1]
                word_end_weight = end[word_idx][1]
                word_frame_weight = \
                    word_start_weight + (word_end_weight - word_start_weight) * (i / (num_frames - 1))
                frame_weights.append((word_at_pos[0], word_frame_weight))
            word_frame_weights.append(frame_weights)
        sections_frames.append(word_frame_weights)

    return sections_frames


def limit_per_pixel_change_slice_old(frames, max_change):
    """ limits the change in each pixel to be no more than max_change """
    import numpy as np
    from PIL import Image
    # change the frames to a numpy array if they are not and normalize the values to be between 0 and 1
    if isinstance(frames[0], Image.Image):
        frames = [np.array(f) / 255 for f in frames]
    for i in range(1, len(frames)):
        # get the difference between the current frame and the last frame
        diff = frames[i] - frames[i - 1]
        # get the absolute value of the difference
        abs_diff = np.abs(diff)
        # get the pixels that have changed more than max_change
        too_big = abs_diff > max_change
        # scale the pixels that have changed too much
        frames[i][too_big] = frames[i - 1][too_big] + np.sign(diff[too_big]) * max_change
    return frames


def limit_per_pixel_change_slice(frames, max_change):
    """
    Limits the change in each pixel to be no more than max_change.

    Parameters:
    frames: a list of numpy arrays or PIL Image objects representing the frames.
    max_change: a float representing the maximum change in each pixel allowed.

    Returns:
    A list of numpy arrays representing the modified frames.

    Examples:
    >>> frames = [
    ...     np.array([
    ...         [0, 0, 0],
    ...         [0, 0, 0]
    ...     ]),
    ...     np.array([
    ...         [0.1, 0.1, 0.1],
    ...         [0.1, 0.1, 0.1]
    ...     ])
    ... ]
    >>> max_change = 0.2
    >>> limit_per_pixel_change_slice(frames, max_change)
    [array([[0, 0, 0],
           [0, 0, 0]]), array([[0.1, 0.1, 0.1],
           [0.1, 0.1, 0.1]])]
    >>> frames = [
    ...     np.array([
    ...         [0, 0, 0],
    ...         [0, 0, 0]
    ...     ]),
    ...     np.array([
    ...         [0.5, 0.5, 0.5],
    ...         [0.5, 0.5, 0.5]
    ...     ])
    ... ]
    >>> max_change = 0.2
    >>> limit_per_pixel_change_slice(frames, max_change)
    [array([[0, 0, 0],
           [0, 0, 0]]), array([[0.2, 0.2, 0.2],
           [0.2, 0.2, 0.2]])]
    >>> frames = [
    ...     Image.fromarray((np.array([
    ...         [0, 0, 0],
    ...         [0, 0, 0]
    ...     ]) * 255).astype(np.uint8)),
    ...     Image.fromarray((np.array([
    ...         [0.5, 0.5, 0.5],
    ...         [0.5, 0.5, 0.5]
    ...     ]) * 255).astype(np.uint8))
    ... ]
    >>> max_change = 0.2
    >>> limit_per_pixel_change_slice(frames, max_change)
    [array([[0., 0., 0.],
           [0., 0., 0.]]), array([[0.2, 0.2, 0.2],
           [0.2, 0.2, 0.2]])]
    """
    import numpy as np
    from PIL import Image
    # change the frames to a numpy array if they are not and normalize the values to be between 0 and 1
    if isinstance(frames[0], Image.Image):
        frames = [np.array(f) / 255 for f in frames]

    for i in range(1, len(frames)):
        # get the difference between the current frame and the last frame
        diff = frames[i] - frames[i - 1]
        # get the absolute value of the difference
        abs_diff = np.abs(diff)
        # get the pixels that have changed more than max_change
        too_big = abs_diff > max_change
        # scale the pixels that have changed too much
        frames[i][too_big] = frames[i - 1][too_big] + np.sign(diff[too_big]) * max_change
    return frames


def limit_per_pixel_change_slice_optical_flow(frames, max_change):
    """
    Limits the change in each pixel to be no more than max_change using optical flow.

    Parameters:
    frames: a list of numpy arrays or PIL Image objects representing the frames.
    max_change: a float representing the maximum change in each pixel allowed.

    Returns:
    A list of numpy arrays representing the modified frames.
    >>> test_limit_per_pixel_change_slice_optical_flow()
    """
    import cv2
    import numpy as np

    # change the frames to a numpy array if they are not and normalize the values to be between 0 and 1
    if isinstance(frames[0], Image.Image):
        np_frames = [np.array(f) for f in frames]
        gray_frames = [cv2.cvtColor(f.astype("uint8"), cv2.COLOR_RGB2GRAY) for f in np_frames]
        frames = [(f / 255).astype("float16") for f in np_frames]
    # create a blank image to use as the previous frame
    prev_frame = np.zeros_like(frames[0])
    out_frames = []
    for prev_idx, cur_idx in zip(range(len(frames) - 1), range(1, len(frames))):
        prev_frame = frames[prev_idx]  # float 0-1
        out_frames.append(prev_frame)
        cur_frame = frames[cur_idx]  # float 0-1
        # convert the frames to grayscale
        prev_gray = gray_frames[prev_idx]  # uint8 0-255
        curr_gray = gray_frames[cur_idx]  # uint8 0-255
        # compute the optical flow between the current frame and the previous frame
        flow = cv2.calcOpticalFlowFarneback(prev_gray, curr_gray, None, 0.5, 5, 64, 5, 7, 1.5,
                                            cv2.OPTFLOW_FARNEBACK_GAUSSIAN)  # -1 to 1 ?
        # get the pixels that have changed more than max_change
        # get the pixels that have changed more than max_change
        too_big = np.power(flow, 2)
        too_big = np.sum(too_big, axis=2)
        too_big = np.power(too_big, .5)  # 0 to 1 representing distance moved, unknown units
        too_big_bool = too_big > max_change
        # add an extra dimension to the too_big array so that it has the same shape as prev_frame and flow
        # too_big = np.expand_dims(too_big, axis=-1)
        # scale the pixels that have changed too much
        over_the_limit = too_big - max_change
        over_the_limit[over_the_limit < 0] = 0
        over_the_limit_ratio = over_the_limit / too_big
        per_of_old = over_the_limit_ratio
        per_of_new = 1 - over_the_limit_ratio
        per_of_new = np.expand_dims(per_of_new, axis=-1)
        per_of_old = np.expand_dims(per_of_old, axis=-1)
        per_of_old[per_of_old < 0] = 0
        per_of_new[per_of_new < 0] = 0
        new_frame = (cur_frame * per_of_new) + (prev_frame * per_of_old)
        out_frames.append(new_frame)
        # gray_frames.insert(i + 1, cv2.cvtColor((new_frame * 255).astype("uint8"), cv2.COLOR_RGB2GRAY))
        # frames[i][too_big_bool] = new_frame[too_big_bool]
    frames = out_frames
    dump_frames(frames, "optical_flow")
    return frames


def dump_frames(frames, folder):
    """
    Dumps the frames to the specified folder.

    Parameters:
    frames: a list of numpy arrays or PIL Image objects representing the frames.
    folder: a string representing the folder to dump the frames to.
    >>> frames = [
    ...     Image.fromarray((np.array([
    ...         [0, 0, 0],
    ...         [0, 0, 0]
    ...     ]) * 255).astype(np.uint8)),
    ...     Image.fromarray((np.array([
    ...         [0.5, 0.5, 0.5],
    ...         [0.5, 0.5, 0.5]
    ...     ]) * 255).astype(np.uint8))
    ... ]
    >>> dump_frames(frames, "test")
    >>> os.system("explorer .")
    """
    import os
    import numpy as np
    from PIL import Image
    if not os.path.exists(folder):
        os.makedirs(folder)
    for i, frame in enumerate(frames):
        if isinstance(frame, Image.Image):
            frame = np.array(frame)
        Image.fromarray((frame * 255).astype(np.uint8)).save(os.path.join(folder, "frame_{}.png".format(i)))


def test_limit_per_pixel_change_slice_optical_flow():
    import random
    random.seed(0)  # set a seed for reproducibility
    # generate two random images
    img1 = np.random.randint(0, 256, size=(100, 100, 3), dtype=np.uint8)
    img2 = np.random.randint(0, 256, size=(100, 100, 3), dtype=np.uint8)
    frames = [img1, img2]
    max_change = 0.5
    # the first frame should not be modified
    assert np.array_equal(limit_per_pixel_change_slice_optical_flow(frames, max_change)[0], img1)
    # the second frame should have pixels that have changed more than max_change scaled
    assert not np.array_equal(limit_per_pixel_change_slice_optical_flow(frames, max_change)[1], img2)


def batched_renderer(SBIMulti, SBIMA_render_func, to_npy=False, rparam: DefaultRender = DefaultRender(),
                     early_stop=None):
    import time
    import numpy as np
    images_to_save = []
    batch_times = []
    start_time = time.time()
    for i in range(0, len(SBIMulti), MAX_BATCH_SIZE):
        max_batch_size = min(MAX_BATCH_SIZE, len(SBIMulti) - i)
        max_idx = min(i + MAX_BATCH_SIZE, len(SBIMulti))
        slice = SBIMulti[i:max_idx]
        slice.render.batch_size = max_batch_size
        results = SBIMA_render_func(slice.combined, 0)
        images_to_save = images_to_save + results.all_images[1:1 + len(slice)]

        batch_times.append(time.time())
        print(f"Images {i} to {i + MAX_BATCH_SIZE}, of {len(SBIMulti)}")
        if len(batch_times) > 1:
            print(f"batch time: {batch_times[-1] - batch_times[-2]}")
        if time.time() - start_time > early_stop:
            print("early stop")
            break
    if to_npy:
        images_to_save = [np.array(img).astype("float16") / 255.0 for img in images_to_save]
    end_time = time.time()
    print(f"Time taken: {end_time - start_time}")
    return images_to_save


def batched_selective_renderer(SBIMultiArgs, SBIMA_render_func, rparam: DefaultRender = DefaultRender(),
                               early_stop=None):
    """
    >>> if True:
    ...   import PIL
    ...   import numpy as np
    ...   f = SBMultiSampleArgs(render=SBIRenderParams(),hyper=SBIHyperParams(prompt="0"))
    ...   results = lambda :None;results.all_images=[np.random.rand(512, 512, 3) for _ in range(MAX_BATCH_SIZE+1)]
    ...   for i in range(1441-1):
    ...     f += SBIHyperParams(prompt=str(i))
    ...   batched_selective_renderer(SBIMultiArgs=f, early_stop=10,SBIMA_render_func=lambda x,y:results )

    """
    import time
    import numpy as np
    from PIL import Image
    images_to_save = {}
    batch_times = []
    start_time = time.time()
    # start by rendering only half of the frames
    even_SBIM = SBIMultiArgs[::2]
    odd_SBIM = SBIMultiArgs[1::2]

    even_results = batched_renderer(even_SBIM,
                                    SBIMA_render_func=SBIMA_render_func,
                                    to_npy=True,
                                    rparam=rparam,
                                    early_stop=early_stop)
    SBIMultiArgs = SBIMultiArgs[:len(even_results) * 2]
    # now render the other half of the frames if the difference between the two is greater than a threshold
    threshold = 0.015 / 2
    # threshold = 0.0
    to_process = None
    # first build a list of the indices of the SBIMultiArgs that need to be rendered based on the difference in the odd frames
    # need something like [(odd_img_prev,even_idx_now,odd_img_next)]
    for i in range(1, len(even_results) - 1):
        difference = np.mean(np.square(even_results[i] - even_results[i + 1]))
        print(difference)
        if difference > threshold:
            if to_process is None:
                to_process = [(i * 2, odd_SBIM[i])]
            else:
                to_process.append((i * 2, odd_SBIM[i]))
    # create the new SBIMultiArgs to render
    new_SBIM = None
    time_idxs = []
    if to_process is not None:
        print(
            f'selective renderer found {len(to_process)} frames to process of {int(len(SBIMultiArgs) / 2)} possible frames')
        for time_idx, sbim in to_process:
            if new_SBIM is None:
                new_SBIM = copy.deepcopy(sbim)
                time_idxs.append(time_idx)
            else:
                new_SBIM += copy.deepcopy(sbim)
                time_idxs.append(time_idx)

    # render the new SBIMultiArgs
    ti_sm_res = {}
    if len(time_idxs) > 0:
        smoothing_results = batched_renderer(new_SBIM,
                                             SBIMA_render_func=SBIMA_render_func,
                                             to_npy=True,
                                             rparam=rparam,
                                             early_stop=early_stop)
        ti_sm_res = dict(zip(time_idxs, smoothing_results))

    images_to_save = []
    for time_idx in range(len(SBIMultiArgs)):
        odd = time_idx % 2 == 0
        even = not odd
        if even:
            try:
                images_to_save.append(even_results[int(time_idx / 2)])
            except:
                print(f'error with time_idx {time_idx} and tmp_results {len(even_results)}')
        if odd:
            if time_idx in ti_sm_res.keys():
                images_to_save.append(ti_sm_res[time_idx])
            else:
                try:
                    images_to_save.append(even_results[int(time_idx / 2)])
                except:
                    print(f'error with time_idx {time_idx}')

    # convert the images to PIL images
    images_to_save = [Image.fromarray(np.uint8(img * 255.0)) for img in images_to_save]
    end_time = time.time()
    print(f"Time taken: {end_time - start_time}")
    return images_to_save


class SBMultiSampleArgs:
    """This is a class to hold and manage a collection of arguments to pass to the model
     with a batch size equal to the number of samples in the collection
     The arguments that are passed to the model and can be different per sample are
     prompt, negative_prompt, steps, seed, subseed, subseed_strength, cfg_scale
     The arguments that are passed to the model and are the same for all samples are
     width, height, restore_faces, tiling, batch_count, batch_size, sampler_index

     they have been split into hyper params and render params"""

    def __init__(self, render: SBIRenderParams, hyper):
        # this just ensures that all the params are lists
        self._hyper: SBIHyperParams = self._make_list(hyper)
        self._render = render
        self._length = len(self._hyper)
        if self._hyper == []:
            pass
        elif isinstance(self._hyper[0], str):
            self._hyper = [SBIHyperParams(prompt=p) for p in self._hyper]
        elif isinstance(self._hyper[0], SBIHyperParams):
            pass

        self.__post_init__()

    def __post_init__(self):
        self._length = len(self._hyper)

    @property
    def combined(self):
        # combines the contents of each hyper param into a single hyper param
        # this is useful for when you want to run a single render with multiple hyper params
        self._combined = copy.deepcopy(self._hyper[0])
        for i in range(1, self._length):
            self._combined += self._hyper[i]
        return SBMultiSampleArgs(render=self._render, hyper=[self._combined])

    def _make_list(self, item):

        if isinstance(item, list):
            return item
        else:
            return [item]

    def __add__(self, other):
        # if other is a tuple of hyper and render
        if isinstance(other, SBIHyperParams):
            # add the params to the lists
            self._hyper.append(other)
            # maintain the other attributes
            self.__post_init__()
        elif isinstance(other, SBMultiSampleArgs):
            self._hyper.append(other._hyper[0])
            if other._render != None:
                # if the other render params are not None
                Warning("Render params passed to add will be ignored")
            # maintain the other attributes
            self.__post_init__()
        else:
            raise TypeError("Can only add SBIHyperParams or SBMultiSampleArgs")

        return self

    @property
    def hyper(self):
        if self._length == 1:
            # if there is only one hyper param, return it
            return self._hyper[0]
        else:
            return self._hyper

    @hyper.setter
    def hyper(self, value):
        self._hyper = self._make_list(value)
        self.__post_init__()

    @property
    def render(self):
        return self._render

    def __iter__(self):
        my_iter = iter(zip(self._hyper, [self._render] * len(self._hyper)))
        return my_iter

    def __len__(self):
        return self._length

    def __getitem__(self, item):
        tmp = SBMultiSampleArgs(render=self._render, hyper=self._hyper[item])
        return tmp


class SBImageResults:
    """
    This class is used to hold the results of a render provided in/by the modules.processing.Processed class
    """

    def __init__(self, processed):
        self.batch_size = processed.batch_size
        self.processed = processed
        self.all_images = processed.images
        self.all_prompts = processed.all_prompts
        self.all_seeds = processed.all_seeds
        self.all_subseeds = processed.all_subseeds
        if len(self.all_subseeds) != len(self.all_seeds):
            self.all_subseeds = self.all_seeds.copy()

        # these need to be adapted to be changable inter-batch if possible
        self.all_negative_prompts = [processed.negative_prompt] * processed.batch_size
        self.all_steps = [processed.steps] * processed.batch_size
        self.all_subseed_strengths = [processed.subseed_strength] * len(processed.all_seeds)
        self.all_cfg_scales = [processed.cfg_scale] * len(processed.all_seeds)

        self.batch_size = processed.batch_size
        self.cfg_scale = processed.cfg_scale
        self.clip_skip = processed.clip_skip
        self.height = processed.height
        self.width = processed.width
        self.job_timestamp = processed.job_timestamp
        self.negative_prompt = processed.negative_prompt
        self.sampler_index = processed.sampler_index
        self.sampler_name = processed.sampler
        self.steps = processed.steps
        self.sb_iparams: SBMultiSampleArgs = self.sb_multi_sample_args_from_sd_results()

        self.img_hyper_params_list = [SBIHyperParams(prompt=prompt,
                                                     negative_prompt=negative_prompt,
                                                     steps=steps,
                                                     seed=seed,
                                                     subseed=subseed,
                                                     subseed_strength=subseed_strength,
                                                     cfg_scale=cfg_scale)
                                      for prompt,
                                          negative_prompt,
                                          steps,
                                          seed,
                                          subseed,
                                          subseed_strength,
                                          # todo: fix the dimensionality of the ones that are not lists
                                          cfg_scale in zip(self.all_prompts,
                                                           self.all_negative_prompts,
                                                           self.all_steps,
                                                           self.all_seeds,
                                                           self.all_subseeds,
                                                           self.all_subseed_strengths,
                                                           self.all_cfg_scales)]

        tmp_list_of_st_sq_render_params = [SBIRenderParams(width=self.width,
                                                           height=self.height,
                                                           restore_faces=processed.restore_faces,
                                                           # tiling does not make it to through the conversion to "procesed"
                                                           # tiling=processed.extra_generation_params[""],
                                                           tiling=None,
                                                           # batch count does not make it to through the conversion to "procesed"
                                                           # batch_count=processed.,batch_count
                                                           batch_count=None,
                                                           batch_size=processed.batch_size,
                                                           sampler_index=processed.sampler_index)
                                           for _ in range(processed.batch_size)]

        self.all_as_stb_image_params = [SBMultiSampleArgs(hyper=hyper,
                                                          render=render)
                                        for hyper, render in zip(self.img_hyper_params_list,
                                                                 tmp_list_of_st_sq_render_params)]

    def __iter__(self):
        o = iter(self.img_hyper_params_list)
        return o

    def __getitem__(self, item):
        return self.img_hyper_params_list[item]

    def __add__(self, other):
        if isinstance(other, SBImageResults):
            self.img_hyper_params_list += other.img_hyper_params_list
        else:
            print("Cannot add SBImageResults to non SBImageResults")

    def sb_multi_sample_args_from_sd_results(self) -> SBMultiSampleArgs:
        # convert the StableDiffusionProcessingTxt2Img params to SBMultiSampleArgs
        processed = self.processed
        if len(processed.all_seeds) == len(processed.all_prompts):
            # then these results are for multiple seeds and prompts
            t_hyp = SBIHyperParams(prompt=processed.all_prompts,
                                   negative_prompt=processed.negative_prompt,
                                   steps=processed.steps,
                                   seed=processed.all_seeds,
                                   subseed=processed.all_subseeds,
                                   # TODO: check if this is valid for multiple subseed
                                   #  strengths
                                   subseed_strength=processed.subseed_strength,
                                   cfg_scale=processed.cfg_scale)
        else:
            t_hyp = SBIHyperParams(prompt=processed.prompt,
                                   negative_prompt=processed.negative_prompt,
                                   steps=processed.steps,
                                   seed=processed.seed,
                                   subseed=processed.subseed,
                                   subseed_strength=processed.subseed_strength,
                                   cfg_scale=processed.cfg_scale)

        t_render = SBIRenderParams(width=processed.width,
                                   height=processed.height,
                                   restore_faces=processed.restore_faces,
                                   # TODO: figure out where to get this from
                                   tiling=False,
                                   # TODO: check if the Processed class is just for one batch always
                                   batch_count=1,
                                   batch_size=processed.batch_size,
                                   sampler_index=processed.sampler_index)

        t_params = SBMultiSampleArgs(hyper=t_hyp, render=t_render)

        return t_params


def get_frame_deltas(frames: List[Image.Image]) -> List[float]:
    """"get the difference between each image"""
    frame_deltas = []
    for i in range(len(frames) - 1):
        frame_delta = get_img_diff(frames[i], frames[i + 1])
        frame_deltas.append(frame_delta)
    return frame_deltas


def compose_storyboard_render_dev(my_ren_p, storyboard_params, ui_params, render_func, test=False,
                                  early_stop=-1):
    """
    this function composes the other rendering function to render a storyboard
    :param my_ren_p: the render parameters for the storyboard
    :param storyboard_params: the storyboard parameters
    :param ui_params: the ui parameters
    :param render_func: the render function to use for rendering the SBMultiSampleArgs
    :param test: if true, then the function will perform a quick test render
    :param early_stop: if not -1, then the function will stop after this many seconds
    >>> test_ui_params = ["test","nude",7,3,4,5,6,7,8,9,10,11,12,7.5]
    >>> compose_storyboard_render_dev(DefaultRender(),None,test_ui_params ,lambda x: random.random() ,test=True)
    """

    if test:
        voice_over_text = "one two three four five six seven eight nine ten"
    else:
        voice_over_text = ui_params[0]

    audio_f_path, vo_len_secs = create_voice_over_for_storyboard(voice_over_text, 1, DefaultRender.seconds)

    my_ren_p.num_frames_per_section = int((my_ren_p.fps * vo_len_secs) / my_ren_p.sections)
    my_ren_p.num_frames = my_ren_p.num_frames_per_section * my_ren_p.sections
    my_ren_p.seconds = vo_len_secs

    if test:
        sb_prompts = [
            "dog :1.0 ate cat:0.0",
            "dog :0.5 ate cat:0.5",
            "dog :0.0 ate cat:1.0",
        ]
    else:
        sb_prompts = [i.prompt for i in storyboard_params]

    sb_prompt = StoryBoardPrompt(sb_prompts, my_ren_p.seconds, True)

    # TODO: need to find seeds/subseeds/weights for each prompt

    ez_p_func: Callable[[Any], SBIHyperParams] = lambda ti: SBIHyperParams(prompt=sb_prompt[ti],
                                          negative_prompt=ui_params[1],
                                          steps=ui_params[2],
                                          seed=[1] * len(sb_prompt[ti]),
                                          subseed=[-1] * len(sb_prompt[ti]),
                                          subseed_strength=[0] * len(sb_prompt[ti]),
                                          cfg_scale=ui_params[13])

    ez_r_func: Callable[[Any], Any] = lambda x: render_func(SBMultiSampleArgs(hyper=ez_p_func(x), render=my_ren_p))

    imgs_by_seconds = {
        np.float64(0): ez_r_func(0.0).all_images[-1],
        np.float64(my_ren_p.seconds): ez_r_func(my_ren_p.seconds).all_images[-1],
    }

    imgs_pairs_by_diff = {
        get_img_diff(imgs_by_seconds[0], imgs_by_seconds[my_ren_p.seconds]): (
            list(imgs_by_seconds.keys())[0], list(imgs_by_seconds.keys())[1])
    }

    done_pairs = []
    while True:
        imgs_by_seconds = dict(sorted(imgs_by_seconds.items()))
        imgs_by_seconds_keys_list = list(imgs_by_seconds.keys())
        # find the largest difference in means between two records
        frame_deltas = get_frame_deltas(list(imgs_by_seconds.values()))

        canidate_imgs_pairs_by_diff = []  # [delta, seconds_idx_1, seconds_idx_2]
        for i in range(len(frame_deltas) ):
            time_pair = (imgs_by_seconds_keys_list[i], imgs_by_seconds_keys_list[i + 1])
            if time_pair not in done_pairs:
                canidate_imgs_pairs_by_diff.append(
                    (frame_deltas[i], (imgs_by_seconds_keys_list[i], imgs_by_seconds_keys_list[i + 1])))

        # sort so that the largest difference is first
        imgs_pairs_by_diff_sorted = sorted(canidate_imgs_pairs_by_diff, key=lambda x: x[0], reverse=True)

        # if the largest difference is less than some value then we are done
        if imgs_pairs_by_diff_sorted[0][0] < 9.0:
            break

        # get a batch of the worst images
        worst_pairs_batch = imgs_pairs_by_diff_sorted[:MAX_BATCH_SIZE]

        # update done_pairs
        pairs_to_do = [v[1] for v in worst_pairs_batch]
        done_pairs.append(pairs_to_do)

        # get the target times for each pair
        target_times = [np.mean(v[1:]) for v in worst_pairs_batch]

        # render the batch
        imgs = ez_r_func(target_times).all_images[1:]
        pairs_done_this_iter = pairs_to_do;
        del pairs_to_do

        tt_imgs = zip(target_times, pairs_done_this_iter, imgs)
        # only keep the images that decrease the difference
        for t, p, i in tt_imgs:
            diff_a = get_img_diff(imgs_by_seconds[p[0]], i)
            diff_b = get_img_diff(i, imgs_by_seconds[p[1]])
            # this is the difference between the two images
            # TODO: this maybe can be safety retrieved from frame_deltas
            diff_c = get_img_diff(imgs_by_seconds[p[0]], imgs_by_seconds[p[1]])
            if (diff_a + diff_b) / 2 < diff_c:
                imgs_by_seconds[t] = i

        # if we have rendered enough images, then stop
        if len(imgs_by_seconds) >= my_ren_p.num_frames:
            break

    print(imgs_by_seconds)
    images_to_save = [i for i in imgs_by_seconds.values()]
    for k, v in imgs_by_seconds.items():
        v.save(f'tmp_{k}.png')

    target_mp4_f_path = compose_file_handling(audio_f_path, images_to_save)
    return target_mp4_f_path


def compose_storyboard_render(my_render_params, all_state, early_stop, storyboard_params, test,
                              test_render, ui_params, SBIMA_render_func, base_SBIMulti: SBMultiSampleArgs):
    # in the interest of syncing the legth of the audio voice over and the length of the video it is important to
    # consider the length of the audio first, primarily because the audio is much quicker to render, but also
    # because it is harder to manipulate temporaly then the mostly arbitrary contents of the video

    # the audio is rendered first, attempting to reach some target length.
    # the video is rendered second, to match the length of the resultant audio

    mytext = ui_params[0]
    if test or test_render:
        mytext = "one two three four five six seven eight nine ten"
    audio_f_path, vo_len_secs = create_voice_over_for_storyboard(mytext, 1, DefaultRender.seconds)

    my_render_params.num_frames_per_section = int((my_render_params.fps * vo_len_secs) / my_render_params.sections)
    my_render_params.num_frames = my_render_params.num_frames_per_section * my_render_params.sections
    my_render_params.seconds = vo_len_secs
    if my_render_params.num_frames < my_render_params.min_frames_per_render:
        my_render_params.num_frames = my_render_params.min_frames_per_render
        my_render_params.fps = my_render_params.num_frames / my_render_params.seconds
        my_render_params.frames_per_section = int(my_render_params.num_frames / my_render_params.sections)
    else:
        use_fps = my_render_params.fps

    prompt_sections = get_frame_values_for_prompt_word_weights(
        [params.prompt for params in storyboard_params],
        num_frames=my_render_params.num_frames_per_section
    )
    #  turn the weights into a list of prompts
    prompts = []
    for section in prompt_sections:
        for frame in section:
            prompts.append(" ".join([f"({word}:{weight})" for word, weight in frame]))
    seeds = get_frame_seed_data(storyboard_params, my_render_params.num_frames_per_section)
    # create a base SBIRenderArgs object
    # feature: this should allow the user to change the settings for rendering

    # base_SBIMulti:SBMultiSampleArgs = StoryBoardGradio.get_sb_multi_sample_params_from_ui(ui_params)
    base_Hyper = copy.deepcopy(base_SBIMulti.hyper)
    # turn the list of prompts and seeds into a list of CallArgsAsData using the base_params as a template
    # populate the storyboard_call_multi with the prompts and seeds
    for prompt, seed in zip(prompts, seeds):
        base_SBIMulti += SBIHyperParams(
            prompt=prompt,
            seed=seed[0],
            subseed=seed[1],
            subseed_strength=seed[2],
            negative_prompt=base_Hyper.negative_prompt,
            steps=base_Hyper.steps,
            cfg_scale=base_Hyper.cfg_scale,
        )

    if not test or test_render:
        # images_to_save = self.batched_renderer(base_SBIMulti, early_stop, self.storyboard)
        images_to_save = batched_selective_renderer(base_SBIMulti,
                                                    rparam=my_render_params,
                                                    early_stop=early_stop,
                                                    SBIMA_render_func=SBIMA_render_func)

    target_mp4_f_path = compose_file_handling(audio_f_path, images_to_save)
    return all_state, target_mp4_f_path


def compose_file_handling(audio_f_path, images_to_save):
    working_dir = os.path.join(os.getenv("STORYBOARD_RENDER_PATH"), "tmp")
    print(f'working_dir: {working_dir}')
    print(f'audio_f_path: {audio_f_path}')
    video_f_path = make_mp4_from_images(
        images_to_save,
        working_dir,
        "video.mp4", 512, 512, False,
        fps=DefaultRender.fps,
        filter_func=lambda x: limit_per_pixel_change_slice(x, .5))
    print(f'video_f_path: {video_f_path}')
    complete_mp4_f_path = join_video_audio(video_f_path, audio_f_path)
    target_mp4_f_path = os.path.join(os.getenv("STORYBOARD_RENDER_PATH"),
                                     f"StoryBoard-{str(random.randint(1, 1000000))}.mp4")
    print(f"target_mp4_f_path: {target_mp4_f_path}")
    # delete storyboard.mp4
    os.remove(video_f_path)
    # delete the audio file
    os.remove(audio_f_path)
    # move the mp4 to the storyboard folder using os.renam
    os.rename(complete_mp4_f_path, target_mp4_f_path)
    return target_mp4_f_path