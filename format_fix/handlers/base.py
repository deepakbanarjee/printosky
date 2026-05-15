"""Abstract section handler.

A SectionHandler claims responsibility for ONE document section type
(cover, acknowledgement, chapter, references, etc.). The orchestrator
walks pages, asks each handler in priority order whether it applies to
the current page, and dispatches to the first match.

Handler contract:
  - Stateless. All state lives in the shared Context.
  - Pure: render() mutates the doc (only) and returns None.
  - Priority is a class attribute; lower number = checked first.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docx import Document
    from ..context import Context


class SectionHandler(ABC):
    """One handler == one document section type. Stateless. Deterministic."""

    #: Lower priority means this handler is checked first.
    priority: int = 100

    #: Display name used in logs / debug output.
    name: str = "unnamed"

    @abstractmethod
    def applies_to(self,
                   blocks: list[tuple],
                   page_no: int,
                   ctx: "Context") -> bool:
        """Cheap check — should this handler claim this page?

        Implementations should be O(blocks) at worst. Do not mutate state.
        """

    @abstractmethod
    def render(self,
               doc: "Document",
               blocks: list[tuple],
               page_no: int,
               ctx: "Context") -> None:
        """Mutate the doc to add this section's content.

        Called only after applies_to returned True. Implementations may
        update ctx.in_form_section but must not mutate other ctx state.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} priority={self.priority}>"
