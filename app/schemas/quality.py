"""
Sales / Pre-Sales Quality Scoring (Weight-Based).

Every response is evaluated against 5 criteria.
Each dimension is scored 0–5, multiplied by its weight.
Maximum weighted total = 100 (raw max = 25, weighted max = 20).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class QualityScore(BaseModel):
    """
    Weighted quality scoring aligned with Sales/Pre-Sales evaluation criteria.

    Scoring:
        weighted_total = sum(score_i * weight_i)   (max = 20)
        percentage     = weighted_total / max_weighted * 100

    Thresholds:
        17-20  (85-100%)  → Excellent  → return as-is
        14-16  (70-84%)   → Good       → return, log for review
        12-13  (60-69%)   → Acceptable → return, flag for improvement
         8-11  (40-59%)   → Below Std  → trigger one-shot refinement
         0-7   ( 0-39%)   → Failed     → refine; if still <12, add disclaimer
    """

    # ── Scores (0-5 each) ───────────────────────────────────────────
    accuracy: int = Field(default=0, ge=0, le=5, description="Technically and contextually correct")
    relevancy: int = Field(default=0, ge=0, le=5, description="Right solution for right problem")
    completeness: int = Field(default=0, ge=0, le=5, description="Covers what sales needs")
    clarity: int = Field(default=0, ge=0, le=5, description="Clarity and structure of response")
    sales_maturity: int = Field(default=0, ge=0, le=5, description="Sales/Pre-Sales tone and framing")

    # ── Weights (fixed) ─────────────────────────────────────────────
    accuracy_weight: int = 5
    relevancy_weight: int = 5
    completeness_weight: int = 4
    clarity_weight: int = 3
    sales_maturity_weight: int = 3

    # ── Computed fields ─────────────────────────────────────────────
    weighted_total: float = 0.0
    raw_total: int = 0
    max_weighted: int = 20
    percentage: float = 0.0

    # ── Check results ───────────────────────────────────────────────
    failed_checks: list[str] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compute_totals(self) -> "QualityScore":
        """Auto-compute weighted total, raw total, and percentage."""
        self.raw_total = (
            self.accuracy + self.relevancy + self.completeness
            + self.clarity + self.sales_maturity
        )
        self.weighted_total = (
            self.accuracy * self.accuracy_weight
            + self.relevancy * self.relevancy_weight
            + self.completeness * self.completeness_weight
            + self.clarity * self.clarity_weight
            + self.sales_maturity * self.sales_maturity_weight
        ) / 5  # Normalize to 0-20 scale (max = 5*5+5*5+5*4+5*3+5*3 = 100, /5 = 20)
        self.percentage = (
            (self.weighted_total / self.max_weighted * 100)
            if self.max_weighted > 0
            else 0.0
        )
        return self

    @property
    def label(self) -> str:
        """Human-readable quality label."""
        if self.weighted_total >= 17:
            return "Excellent"
        if self.weighted_total >= 14:
            return "Good"
        if self.weighted_total >= 12:
            return "Acceptable"
        if self.weighted_total >= 8:
            return "Below Standard"
        return "Failed"

    @property
    def needs_refinement(self) -> bool:
        """Whether the response should be sent through refinement."""
        return self.weighted_total < 12
