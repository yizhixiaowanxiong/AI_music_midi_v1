from typing import Dict, List

from schema.arrangement import TrackGenRequest
from schema.base import AgentRoutingRole
from schema.blueprint_schema import DetailedSection, SongBlueprint
from schema.section_schema import Strictness
from utils.base import BaseTrackHandler
from utils.bass import BassHandler
from utils.fx import FxHandler
from utils.harmony import HarmonyHandler
from utils.melody import MelodyHandler
from utils.standard import StandardHandler


class TrackDispatcher:
    def __init__(self):
        standard_handler = StandardHandler()
        self._handlers: Dict[AgentRoutingRole, BaseTrackHandler] = {
            AgentRoutingRole.BASS: BassHandler(),
            AgentRoutingRole.MELODY: MelodyHandler(),
            AgentRoutingRole.HARMONY: HarmonyHandler(),
            AgentRoutingRole.PERCUSSION: standard_handler,
            AgentRoutingRole.FX: FxHandler(),
        }
        self._default_handler = standard_handler

    def dispatch_section(
        self,
        section: DetailedSection,
        blueprint: SongBlueprint,
        section_runtime_index: int,
        strictness: Strictness = 1,
    ) -> List[TrackGenRequest]:
        requests: List[TrackGenRequest] = []
        for track_key, design in section.arrangement.items():
            handler = self._handlers.get(design.role, self._default_handler)
            req = handler.create_request(
                section=section,
                track_key=track_key,
                blueprint=blueprint,
                design=design,
                strictness=strictness,
                section_runtime_index=section_runtime_index,
            )
            requests.append(req)

        requests.sort(key=lambda r: r.compute_layer)
        return requests


_dispatcher_instance = TrackDispatcher()


def dispatch_section_to_requests(
    section: DetailedSection,
    blueprint: SongBlueprint,
    section_runtime_index: int,
    strictness: Strictness = 1,
) -> List[TrackGenRequest]:
    return _dispatcher_instance.dispatch_section(
        section=section,
        blueprint=blueprint,
        section_runtime_index=section_runtime_index,
        strictness=strictness,
    )
