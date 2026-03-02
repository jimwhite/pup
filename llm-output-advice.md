Now I have all the information needed. Let me write up a comprehensive answer.

Here's the situation: GPT-OSS models on Groq have well-documented tool-calling issues, but Groq **does** support `response_format` with `json_schema` (including `strict: true`) for those exact models — and this is a completely separate code path from tool calling. This is your best option, and there are two solid approaches. [community.groq](https://community.groq.com/t/gpt-oss-120b-ignoring-tools/385)

## Option 1: Groq's Native `json_schema` Response Format (Recommended)

The GPT-OSS 20B and 120B models on Groq support **constrained decoding** via `response_format` with `type: "json_schema"` and `strict: true`. This is *not* tool calling — it uses a separate constrained decoding pathway that guarantees schema-compliant output. This is the most reliable approach. [console.groq](https://console.groq.com/docs/structured-outputs)

```python
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
import json

# Define your output schema
class AtomicIdea(BaseModel):
    cell_number: int
    what: str
    why: str = Field(default="", description="Why this exists, if inferable")
    how: str = Field(default="", description="Mechanism/implementation, if applicable")

class CellAnnotations(BaseModel):
    annotations: list[AtomicIdea]

llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.0,
)

# Use with_structured_output with method="json_schema"
structured_llm = llm.with_structured_output(
    CellAnnotations,
    method="json_schema",   # Uses response_format, NOT tool calling
    strict=True,
)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an ACL2 expert..."),
    ("human", "{cells_text}"),
])

chain = prompt | structured_llm
result = chain.invoke({"cells_text": "..."})
```

The `langchain-groq` package recently added `json_schema` as a method option, which passes the schema through `response_format` rather than as a tool. If your `langchain-groq` version doesn't have this yet, you can also bind `response_format` directly. [community.groq](https://community.groq.com/t/openai-gpt-oss-models-doesnt-support-structure-output/181)

## Option 2: `PydanticOutputParser` (Pure Prompt-Based Fallback)

If you hit edge cases with `json_schema` mode (some users have reported 400 errors under certain conditions ), the `PydanticOutputParser` approach works entirely through prompt engineering — no tool calling, no `response_format` — just format instructions injected into the prompt and parsing on the output side. [mirascope](https://mirascope.com/blog/langchain-structured-output)

```python
from langchain_groq import ChatGroq
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

class AtomicIdea(BaseModel):
    cell_number: int = Field(description="0-based cell index")
    what: str = Field(description="What this idea defines or claims")
    why: str = Field(default="", description="Rationale, if inferable")
    how: str = Field(default="", description="Mechanism/implementation detail")

class CellAnnotations(BaseModel):
    annotations: list[AtomicIdea] = Field(
        description="All atomic ideas extracted from all cells"
    )

parser = PydanticOutputParser(pydantic_object=CellAnnotations)

prompt = PromptTemplate(
    template="""You are an expert in ACL2 formal verification.
{format_instructions}

{your_existing_instructions}

--- Cells ---
{cells_text}""",
    input_variables=["cells_text", "your_existing_instructions"],
    partial_variables={
        "format_instructions": parser.get_format_instructions()
    },
)

llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0.0)
chain = prompt | llm | parser
result = chain.invoke({...})  # Returns a CellAnnotations instance
```

The `get_format_instructions()` method auto-generates a JSON schema description that gets injected into the prompt, telling the model exactly what JSON structure to produce. No tool calling involved at all. [apxml](https://apxml.com/courses/python-llm-workflows/chapter-4-langchain-fundamentals/langchain-output-parsers)

## Option 3: `JsonOutputParser` (Simplest)

LangChain's own docs describe `JsonOutputParser` as "probably the most reliable output parser for getting structured data that does not use function calling". It also supports streaming with partial JSON: [reference.langchain](https://reference.langchain.com/python/langchain-core/output_parsers/json/JsonOutputParser)

```python
from langchain_core.output_parsers import JsonOutputParser

parser = JsonOutputParser(pydantic_object=CellAnnotations)
chain = prompt | llm | parser
# Returns a dict (not validated Pydantic), but no tool calling
```

## Which to Choose

| Approach | Reliability | Validation | Tool Calling? |
|---|---|---|---|
| `json_schema` + `strict: true` | Guaranteed schema compliance via constrained decoding | Yes (Pydantic) | No |
| `PydanticOutputParser` | Depends on model following instructions | Yes (Pydantic) | No |
| `JsonOutputParser` | Good, prompt-based | JSON only (no type validation) | No |

**My recommendation**: Start with **Option 1** (`method="json_schema"`, `strict=True`). Groq's constrained decoding for GPT-OSS is a separate mechanism from tool calling and is designed to guarantee schema compliance. The Groq community thread issues were specifically about tool calling and `tool_choice` being ignored — `response_format` with `json_schema` is a different API path. [community.groq](https://community.groq.com/t/gpt-oss-120b-ignoring-tools/385)

If you run into the intermittent 400 errors some users reported (which Groq has been actively fixing), fall back to **Option 2** with `PydanticOutputParser`, which is entirely prompt-based and works with any model on Groq. You'd keep your full existing prompt and just append `parser.get_format_instructions()` to replace the tool-calling instructions. [console.groq](https://console.groq.com/docs/langchain)
