"""Deterministic spans; narration -> narrator, dialogue left unattributed (rendered in the narrator's voice)."""

from __future__ import annotations

from ...base import Context
from ..base import AttributeMethod
from ..spans import build_spans

__all__ = ["NarratorAttribute"]


class NarratorAttribute(AttributeMethod):
    def run(self, ctx: Context):
        counts = build_spans(ctx.need_wd(), force=True)
        ctx.log(f"  {sum(counts.values()):,} spans in {len(counts)} chapters (single narrator)")
        return counts
