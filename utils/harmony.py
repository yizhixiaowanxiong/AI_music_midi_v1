from utils.base import BaseTrackHandler


class HarmonyHandler(BaseTrackHandler):
    """Harmony 请求配置。"""

    # Harmony 本身无同段前置依赖，放在基础层执行。
    compute_layer: int = 0
