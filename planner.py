import os
import sys
from typing import List

from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv()

# Verify API Key
api_key = os.getenv("DeepSeek_API_KEY")
if not api_key:
    print("ERROR: DeepSeek API Key not found in environment variables.")
    sys.exit(1)


# --- 1. Define the Plan Structure ---
class Plan(BaseModel):
    """Plan to follow for a tourist request."""

    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )


# --- 2. Initialize DeepSeek LLM ---
llm = ChatOpenAI(
    model="deepseek-chat",
    temperature=0,
    base_url="https://api.deepseek.com",
    api_key=api_key,
)

# --- 3. Create the Parser ---
# This parser will give us instructions on how to format the output
parser = PydanticOutputParser(pydantic_object=Plan)


# --- 4. Create the Planner Function ---
def create_planner():
    """
    Creates a chain that takes a user goal and outputs a structured Plan.
    """

    # We inject the parser's instructions into the system prompt
    planner_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are an expert travel planner. Your task is to create a step-by-step plan "
                "to fulfill the user's travel request.\n"
                "Focus on information gathering and itinerary creation steps.\n"
                "Do NOT execute the steps, just list them.\n"
                "For example, if user asks 'Trip to Paris', your steps might be: "
                "['Search for top attractions in Paris', 'Find hotels in Paris', 'Create a daily itinerary'].\n\n"
                "IMPORTANT: You must format your output strictly as a JSON object matching the following instructions:\n"
                "{format_instructions}"
            ),
            ("user", "{objective}"),
        ]
    )

    # We use a simple chain: Prompt -> LLM -> Parser
    # This avoids the "with_structured_output" API call that DeepSeek failed on
    planner_chain = planner_prompt | llm | parser

    return planner_chain


# --- 5. Test the Planner ---
if __name__ == "__main__":
    print("--- Testing Planner (Fix for DeepSeek) ---")
    user_request = "I want a 3-day trip to Tokyo with a focus on anime and food."
    print(f"User Request: {user_request}")

    try:
        planner = create_planner()
        # Pass the format instructions to the prompt
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
