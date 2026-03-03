"""
Planner agent for F1 Analytics.

Analyses the current project state and produces a prioritised JSON task list
for building an ML-powered lineup recommendation system.
"""

import json
import os
from pathlib import Path

from agents.base import Agent
from agents.tools import ALL_TOOLS

OUTPUT_PATH = Path(__file__).parent / "plan.json"

SYSTEM_PROMPT = """You are an expert data engineer and machine learning practitioner
analysing an F1 Analytics Django project.

Your goal is to:
1. Understand what data is currently in the database (use query_data_summary)
2. Review key source files to understand the architecture (use read_file)
3. Identify what is missing or incomplete
4. Produce a prioritised JSON task list for building an ML-powered lineup
   recommendation system for F1 Fantasy

Focus on:
- Data completeness (what sessions/data types are missing)
- Data quality (fields that exist but aren't populated)
- Feature engineering readiness (what ML features can be built now vs later)
- Model training prerequisites (minimum data requirements)

Output ONLY valid JSON as your final answer, in this format:
{
  "data_summary": { ... },
  "identified_gaps": [ ... ],
  "tasks": [
    {
      "priority": 1,
      "title": "...",
      "rationale": "...",
      "effort": "low|medium|high",
      "depends_on": []
    },
    ...
  ],
  "recommendations": "..."
}

Use the provided tools to inspect the project before writing your plan.
Do NOT guess — use tools to discover actual state.
"""


class PlannerAgent(Agent):
    def __init__(self):
        super().__init__(
            model="claude-sonnet-4-6",
            system_prompt=SYSTEM_PROMPT,
            tools=ALL_TOOLS,
            max_turns=20,
        )

    def run_and_save(self) -> dict:
        """Run the planner and write the plan to plan.json."""
        print("Running PlannerAgent...")
        print("=" * 60)

        result_text = self.run(
            "Analyse the F1 analytics project. Check the database summary, "
            "review key files, identify gaps, and produce a prioritised JSON "
            "task list for building the ML-powered lineup recommendation system."
        )

        print("=" * 60)
        print("Agent response received. Parsing JSON...")

        # Try to extract JSON from the response
        plan = None
        try:
            # Look for a JSON block in the response
            start = result_text.find("{")
            end = result_text.rfind("}") + 1
            if start >= 0 and end > start:
                plan = json.loads(result_text[start:end])
            else:
                plan = {"raw_response": result_text}
        except json.JSONDecodeError:
            plan = {"raw_response": result_text, "parse_error": "Could not parse JSON"}

        # Write to file
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, default=str)

        print(f"Plan written to: {OUTPUT_PATH}")
        return plan
