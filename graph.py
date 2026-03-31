import operator
import os
import sys
from typing import Annotated, List, Tuple, TypedDict

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph

try:
    from planner import Plan, create_planner
except ImportError:
    print("ERROR: Could not import planner.py. Make sure it exists in the same folder.")
    sys.exit(1)

# Load environment variables
load_dotenv()

api_key = os.getenv("DeepSeek_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    print("ERROR: DeepSeek_API_KEY not found in environment variables.")
    sys.exit(1)


class PlanExecuteState(TypedDict, total=False):
    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    response: str


@tool
def search_web(query: str):
    """Useful for searching travel information on the internet."""
    print(f"    [TOOL CALL] search_web(query={query})")
    return f"Mock search result for '{query}': Found relevant travel info."


@tool
def book_hotel(location: str):
    """Useful for checking accommodation options."""
    print(f"    [TOOL CALL] book_hotel(location={location})")
    return f"Mock booking check in {location}: Found available hotels."


def _route_tool_for_step(step: str) -> str:
    lowered = step.lower()
    if "hotel" in lowered or "accommodation" in lowered:
        return book_hotel.invoke({"location": step})
    return search_web.invoke({"query": step})


def plan_node(state: PlanExecuteState):
    """Generate the initial plan from user input."""
    print("\n[PLANNER] Planning...")
    planner = create_planner()
    parser = PydanticOutputParser(pydantic_object=Plan)

    try:
        result = planner.invoke(
            {
                "objective": state["input"],
                "format_instructions": parser.get_format_instructions(),
            }
        )
        return {"plan": result.steps}
    except Exception as e:
        err = f"Planning failed: {e}"
        print(f"ERROR: {err}")
        return {"plan": [], "response": err}


def execute_step(state: PlanExecuteState):
    """Execute a single step in the current plan."""
    plan = state.get("plan", [])

    if not plan:
        return {}

    current_task = plan[0]
    print(f"\n[EXECUTOR] Executing step: {current_task}")
    execution_result = _route_tool_for_step(current_task)

    return {
        "plan": plan[1:],
        "past_steps": [(current_task, execution_result)],
    }


def should_continue(state: PlanExecuteState):
    """If steps remain, keep executing; otherwise finalize."""
    if state.get("plan"):
        return "executor"
    return "finalizer"


def finalize_node(state: PlanExecuteState):
    """Build a readable final response from executed steps."""
    if state.get("response"):
        return {"response": state["response"]}

    past_steps = state.get("past_steps", [])
    if not past_steps:
        return {"response": "No itinerary could be produced."}

    lines = ["Final itinerary draft based on executed steps:"]
    for i, (step, result) in enumerate(past_steps, 1):
        lines.append(f"{i}. Step: {step}")
        lines.append(f"   Result: {result}")
    return {"response": "\n".join(lines)}


workflow = StateGraph(PlanExecuteState)
workflow.add_node("planner", plan_node)
workflow.add_node("executor", execute_step)
workflow.add_node("finalizer", finalize_node)

workflow.set_entry_point("planner")
workflow.add_edge("planner", "executor")
workflow.add_conditional_edges(
    "executor",
    should_continue,
    {
        "executor": "executor",
        "finalizer": "finalizer",
    },
)
workflow.add_edge("finalizer", END)

app = workflow.compile()


if __name__ == "__main__":
    print("--- Starting Tourist Agent (DeepSeek Powered) ---")
    user_input = "I want a 2-day trip to Kyoto."

    try:
        final_state = app.invoke(
            {"input": user_input, "past_steps": []},
            {"recursion_limit": 50},
        )

        print("\nMission complete.")
        print("\n--- Final Response ---")
        print(final_state.get("response", "No response generated."))

    except Exception as e:
        print(f"\nRuntime error: {e}")
