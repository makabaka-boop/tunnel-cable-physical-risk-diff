"""pytest 配置：把 api/ 加入 sys.path。"""

import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
