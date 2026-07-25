"""PyInstaller 入口脚本：仅调用 app.main()（等价 __main__.py 约束）。"""

import sys

from auto_video_maker.app import main

if __name__ == "__main__":
    sys.exit(main())
