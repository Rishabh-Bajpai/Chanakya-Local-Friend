from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ToolResult:
    status: str
    summary: str


class WeatherTool:
    """Simple weather tool mock for MVP feasibility testing."""

    def run(self, location: str | None) -> ToolResult:
        if not location:
            return ToolResult(
                status="missing_location",
                summary="I can fetch weather, but please provide a location.",
            )
        return ToolResult(
            status="ok",
            summary=f"Weather for {location}: 27C, partly cloudy, light wind.",
        )
