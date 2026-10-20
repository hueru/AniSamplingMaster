import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from anisampler.gui import main

if __name__ == "__main__":
    main()
