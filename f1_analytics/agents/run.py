"""
CLI entry point for F1 Analytics agents.

Usage:
    cd f1_analytics
    python agents/run.py plan
"""

import argparse
import os
import sys

# Ensure f1_analytics/ is on sys.path so 'agents' and 'config' are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure Django before importing anything that needs it
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def cmd_plan(args):
    """Run the planner agent and write plan.json."""
    import django
    django.setup()

    from agents.planner import PlannerAgent
    agent = PlannerAgent()
    plan = agent.run_and_save()

    # Print a brief summary
    if "tasks" in plan:
        print(f"\nPlan contains {len(plan['tasks'])} tasks:")
        for task in plan["tasks"][:10]:
            print(f"  [{task.get('priority', '?')}] {task.get('title', '?')} ({task.get('effort', '?')})")
        if len(plan["tasks"]) > 10:
            print(f"  ... and {len(plan['tasks']) - 10} more")
    else:
        print("\nPlan written (no structured tasks found — see plan.json for full content)")


def main():
    parser = argparse.ArgumentParser(
        description="F1 Analytics agent CLI",
        prog="python agents/run.py",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 'plan' command
    plan_parser = subparsers.add_parser(
        "plan",
        help="Analyse project state and write a prioritised task plan to agents/plan.json",
    )
    plan_parser.set_defaults(func=cmd_plan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
