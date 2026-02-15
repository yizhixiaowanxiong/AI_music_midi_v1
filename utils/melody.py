from utils.base import BaseTrackHandler


class MelodyHandler(BaseTrackHandler):
    """Melody 请求配置。"""

    # 依赖同段 harmony 结果，放在有依赖层。
    compute_layer: int = 1
