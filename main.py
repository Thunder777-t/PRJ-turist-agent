import os

from dotenv import load_dotenv

LANGCHAIN_READY = True
try:
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr
except ModuleNotFoundError:
    LANGCHAIN_READY = False
    create_agent = None
    HumanMessage = None
    ChatOpenAI = None
    SecretStr = None

    def tool(func):  # type: ignore[no-redef]
        return func


# Load environment variables
load_dotenv()
api_key = os.getenv("DeepSeek_API_KEY")


# --- 1. Define a simple tool ---
@tool
def check_weather(city: str):
    """Returns the weather for a given city. Use this for weather questions."""
    # In a real project, you would call a real Weather API here.
    return f"The weather in {city} is sunny and 25C."


def _run_fallback_demo(user_input: str) -> None:
    """Run a deterministic local demo when model/tool agent is unavailable."""
    city = "London"
    print("[HUMAN]:", user_input)
    print("[AI]: I will use the local weather tool fallback.")
    if hasattr(check_weather, "invoke"):
        print("[TOOL]:", check_weather.invoke({"city": city}))
    else:
        print("[TOOL]:", check_weather(city))
    print("[AI]: Fallback mode completed successfully.")


# --- 2. Initialize optional LLM and Agent ---
llm = None
agent_executor = None
if api_key and LANGCHAIN_READY:
    try:
        llm = ChatOpenAI(
            model="deepseek-chat",
            temperature=0,
            base_url="https://api.deepseek.com",
            api_key=SecretStr(api_key),
        )
        tools = [check_weather]
        agent_executor = create_agent(model=llm, tools=tools)
    except Exception as e:
        print(f"WARNING: Failed to initialize online agent ({e}). Falling back to local mode.")
elif api_key and not LANGCHAIN_READY:
    print("WARNING: langchain package missing. Falling back to local mode.")


# --- 3. Run the Agent ---
def main():
    print("--- Agent Started ---")
    user_input = "What is the weather in London today?"
    print(f"User: {user_input}")

    if agent_executor is None:
        print("WARNING: Running in fallback mode (no online agent).")
        _run_fallback_demo(user_input)
        return

    try:
        result = agent_executor.invoke(
            {"messages": [HumanMessage(content=user_input)]},
        )
        messages = result.get("messages", [])
        if messages:
            print(f"[AI]: {messages[-1].content}")
        else:
            print("[AI]: No response")
    except Exception as e:
        print(f"WARNING: Online run failed ({e}). Switching to fallback mode.")
        _run_fallback_demo(user_input)


if __name__ == "__main__":
    main()
