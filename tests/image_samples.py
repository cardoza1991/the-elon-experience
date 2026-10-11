"""Print sample verdicts for each synthetic image type:  python -m tests.image_samples"""
import random

import brain
import convo
import vision
from store import DEFAULT_CONFIG, Store
from tests import synth_images as S
from tests.convo_harness import shared_index, tweets


def main():
    for kind, fn in S.ALL.items():
        f = vision.analyze(fn(), kind + (".png" if kind in ("screenshot", "wallpaper") else ".jpg"))
        flags = [k for k in ("dark", "blurry", "sharp", "vibrant", "mono", "duotone", "glow", "screenshot", "meme",
                             "grainy", "people", "overexposed", "low_contrast") if f[k]]
        print(f"=== {kind}: primary={f['primary']} score={f['score']}/10 react={' '.join(vision.emojis(f))} "
              f"traits={','.join(flags)}")
        for seed in range(4):
            b = brain.Brain(Store(None), tweets(), rng=random.Random(seed), index=shared_index())
            convo.Conversations(b)
            j = vision.ImageJudge(b)
            asked = seed < 2
            v = j.verdict(f, gid=1, uid=5, name="GamerQuailYT", cfg=dict(DEFAULT_CONFIG), asked=asked)
            print(f"  {'(asked) ' if asked else '(random)'} {v}")
        print()


if __name__ == "__main__":
    main()
