import os
import re
from typing import List

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

# Load environment variables
load_dotenv()

# Optional API key. If missing, we fall back to a local rule-based planner.
api_key = os.getenv("DeepSeek_API_KEY")


# --- 1. Define the Plan Structure ---
class Plan(BaseModel):
    """Plan to follow for a tourist request."""

    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )


def _extract_destination(user_input: str) -> str:
    match = re.search(r"\bto\s+([A-Za-z][A-Za-z\s\-]{1,50})", user_input, re.IGNORECASE)
    if not match:
        return "your destination"

    destination = match.group(1).strip(" .,")
    for stop_word in [" with ", " for ", " on ", " including "]:
        idx = destination.lower().find(stop_word)
        if idx != -1:
            destination = destination[:idx].strip()
    return destination or "your destination"


def _extract_days(user_input: str) -> int:
    match = re.search(r"(\d+)\s*-\s*day|(\d+)\s*day|(\d+)\s*days", user_input.lower())
    if not match:
        return 3

    value = next((group for group in match.groups() if group), "3")
    return max(1, int(value))


def _fallback_plan(objective: str) -> Plan:
    destination = _extract_destination(objective)
    days = _extract_days(objective)

    return Plan(
        steps=[
            f"Search top attractions in {destination}.",
            f"Find accommodation options in {destination}.",
            f"Check weather forecast in {destination}.",
            f"Plan transportation between attractions in {destination}.",
            f"Create a detailed {days}-day itinerary for {destination}.",
            "Estimate total budget including food, transport, and tickets.",
            "Verify opening hours for key attractions before finalizing.",
        ]
    )


# --- 2. Initialize DeepSeek LLM (optional) ---
llm = None
if api_key:
    llm = ChatOpenAI(
        model="deepseek-chat",
        temperature=0,
        base_url="https://api.deepseek.com",
        api_key=SecretStr(api_key),
    )

# --- 3. Create the Parser ---
# This parser will give us instructions on how to format the output
parser = PydanticOutputParser(pydantic_object=Plan)


# --- 4. Create the Planner Function ---
def create_planner():
    """
    Creates a planner that takes a user goal and outputs a structured Plan.
    """

    if llm is None:

        class LocalPlanner:
            def invoke(self, inputs):
                objective = inputs.get("objective", "")
                return _fallback_plan(objective)

        return LocalPlanner()

    planner_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert travel planner. Your task is to create a step-by-step plan "
                "to fulfill the user's travel request.\n"
                "Focus on information gathering and itinerary creation steps.\n"
                "Do NOT execute the steps, just list them.\n"
                "Each step must be actionable and tool-friendly (weather, place search, hotels, transport, budget).\n"
                "Include one explicit verification step for attraction existence/opening information.\n"
                "For example, if user asks 'Trip to Paris', your steps might be: "
                "['Search for top attractions in Paris', 'Find hotels in Paris', 'Create a daily itinerary'].\n\n"
                "IMPORTANT: You must format your output strictly as a JSON object matching the following instructions:\n"
                "{format_instructions}"
            ),
            ("user", "{objective}"),
        ]
    )

    # We use a simple chain: Prompt -> LLM -> Parser
    # This avoids the with_structured_output API call that DeepSeek may not support.
    planner_chain = planner_prompt | llm | parser
    return planner_chain


# --- 5. Test the Planner ---
if __name__ == "__main__":
    print("--- Testing Planner ---")
    user_request = "I want a 3-day trip to Tokyo with a focus on anime and food."
    print(f"User Request: {user_request}")

    if llm is None:
        print("WARNING: DeepSeek API key missing. Using fallback local planner.")

    try:
        planner = create_planner()
        plan = planner.invoke(
            {
                "objective": user_request,
                "format_instructions": parser.get_format_instructions(),
            }
        )

        print("\n--- Generated Plan ---")
        if plan and plan.steps:
            for i, step in enumerate(plan.steps):
                print(f"{i + 1}. {step}")
        else:
            print("WARNING: Plan generated but steps are empty.")

    except Exception as e:
        print(f"\nERROR: Execution failed: {e}")
