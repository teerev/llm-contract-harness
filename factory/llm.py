import os
from datetime import datetime
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# Log file for SE prompts and responses
_LOG_FILE: Path | None = None
_CALL_COUNT = 0


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use in a filename."""
    # Replace spaces and special chars with underscores
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")


def _get_log_file(work_order_name: str | None = None, iteration: int | None = None) -> Path:
    """Get or create the log file path for this run."""
    global _LOG_FILE
    if _LOG_FILE is None:
        log_dir = Path.home() / ".langgraph-prototype" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        wo_part = f"{_sanitize_filename(work_order_name)}_" if work_order_name else ""
        iter_part = f"iter{iteration}_" if iteration is not None else ""
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        _LOG_FILE = log_dir / f"{wo_part}{iter_part}{timestamp}.txt"
    return _LOG_FILE


def _log_llm_call(
    system: str,
    user: str,
    response: str,
    iteration: int | None = None,
    work_order_name: str | None = None,
) -> None:
    """Log the raw prompt and response to a text file."""
    global _CALL_COUNT
    _CALL_COUNT += 1
    log_file = _get_log_file(work_order_name=work_order_name, iteration=iteration)
    
    separator = "=" * 80
    iter_str = f" | ITERATION {iteration}" if iteration is not None else ""
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n{separator}\n")
        f.write(f"LLM CALL #{_CALL_COUNT}{iter_str} - {datetime.now().isoformat()}\n")
        f.write(f"{separator}\n\n")
        
        f.write(">>> SYSTEM PROMPT:\n")
        f.write(f"{system}\n\n")
        
        f.write(">>> USER PROMPT:\n")
        f.write(f"{user}\n\n")
        
        f.write(">>> RAW RESPONSE:\n")
        f.write(f"{response}\n\n")
    
    print(f"[DEBUG] LLM call #{_CALL_COUNT} (iteration={iteration}) logged to: {log_file}")


def get_model():

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o")

    class OpenAIModel:
        def __init__(self) -> None:
            self._llm = ChatOpenAI(model=model_name)
            self._model_name = model_name

        def complete(
            self,
            system: str,
            user: str,
            iteration: int | None = None,
            work_order_name: str | None = None,
        ) -> str:
            response = self._llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            content = str(response.content)
            _log_llm_call(system, user, content, iteration=iteration, work_order_name=work_order_name)
            return content

    return OpenAIModel()
