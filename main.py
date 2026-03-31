import os

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

# Load environment variables
load_dotenv()


# Verify API key is loaded
if not os.getenv("DeepSeek_API_KEY"):
    raise ValueError("API Key not found. Please check your .env file.")


# --- 1. Define a simple tool ---
@tool
def check_weather(city: str):
    """Returns the weather for a given city. Use this for weather questions."""
    # In a real project, you would call a real Weather API here.
    return f"The weather in {city} is sunny and 25C."


# --- 2. Initialize the LLM (Planner/Brain) ---
# We point to DeepSeek's API server.
# "deepseek-chat" is their main model (equivalent to GPT-4 in many tasks).
llm = ChatOpenAI(
    model="deepseek-chat",
    temperature=0,
    base_url="https://api.deepseek.com",
    api_key=os.getenv("DeepSeek_API_KEY"),
)


# --- 3. Create the Agent ---
# We use a prebuilt ReAct agent for this "Hello World" demo.
# For your final project, we will build a custom "Plan-and-Execute" graph.
tools = [check_weather]
agent_executor = create_react_agent(llm, tools)


# --- 4. Run the Agent ---
def main():
    print("--- Agent Started ---")
    user_input = "What is the weather in London today?"
    print(f"User: {user_input}")

    # Stream the agent's reasoning steps
    events = agent_executor.stream(
        {"messages": [("user", user_input)]},
        stream_mode="values",
    )

    for event in events:
        # Print the last message from the agent (AI or Tool output)
        if "messages" in event:
            last_message = event["messages"][-1]
            print(f"[{last_message.type.upper()}]: {last_message.content}")


if __name__ == "__main__":
    main()
