from utils.base import BaseTrackHandler


class BassHandler(BaseTrackHandler):
    """Bass 请求配置。"""

    # 依赖同段 drums 结果，放在有依赖层。
    compute_layer: int = 1
