"""
CostCalculator — stateless cost computation from a cost rate + token counts.
Keeps financial arithmetic in Decimal to avoid float rounding errors.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from backend.metering.dto.cost_rate_dto import CostRateReadDTO

_THOUSAND = Decimal("1000")
_ZERO = Decimal("0")
_QUANTIZE = Decimal("0.00000001")  # 8 decimal places


class CostCalculator:
    """
    Computes cost_usd from a CostRateReadDTO and token counts.

    Usage:
        calculator = CostCalculator()
        cost = calculator.compute(rate, prompt_tokens=1200, completion_tokens=300)
    """

    def compute(
        self,
        rate: CostRateReadDTO,
        prompt_tokens: int,
        completion_tokens: int,
        thinking_tokens: int = 0,
    ) -> Decimal:
        """
        Return cost in USD as a Decimal(14,8).

        thinking_tokens are billed at thinking_cost_per_1k when present,
        otherwise at input_cost_per_1k (Anthropic's current billing model).
        """
        input_cost = (
            Decimal(prompt_tokens) / _THOUSAND * rate.input_cost_per_1k
        )
        output_cost = (
            Decimal(completion_tokens) / _THOUSAND * rate.output_cost_per_1k
        )

        if thinking_tokens > 0:
            thinking_rate = (
                rate.thinking_cost_per_1k
                if rate.thinking_cost_per_1k is not None
                else rate.input_cost_per_1k
            )
            thinking_cost = Decimal(thinking_tokens) / _THOUSAND * thinking_rate
        else:
            thinking_cost = _ZERO

        total = (input_cost + output_cost + thinking_cost).quantize(
            _QUANTIZE, rounding=ROUND_HALF_UP
        )
        return total
