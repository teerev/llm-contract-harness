"""wrapper for calling the langauge model."""

import os
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI


def get_model():
    """returns an instance of the configured llm."""
    model_name = os.getenv("OPENAI_MODEL", "gpt-4")

    class OpenAIModel:
        def __init__(self) -> None:
            self._llm = ChatOpenAI(model=model_name)
            self._model_name = model_name

        def complete(self, system: str, user: str) -> str:
            response = self._llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            return str(response.content)

    return OpenAIModel()
