from utils.base import BaseTrackHandler


class StandardHandler(BaseTrackHandler):
    """默认请求配置（如 drums）。"""

    include_chords: bool = False
