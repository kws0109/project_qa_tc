"""인터뷰로 쌓은 게임 지식과 그로부터의 TC 생성."""

from .models import Content, Slot, SlotSpec, SlotStatus
from .slots import BASE_SLOTS, KNOWN_TYPES, TYPE_SLOTS, build_slot_set

__all__ = [
    "BASE_SLOTS",
    "KNOWN_TYPES",
    "TYPE_SLOTS",
    "Content",
    "Slot",
    "SlotSpec",
    "SlotStatus",
    "build_slot_set",
]
