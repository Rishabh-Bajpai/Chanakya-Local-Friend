# MAF Class Mapping for Chanakya MVP

This document maps Chanakya MVP requirements to concrete Microsoft Agent Framework (MAF) Python classes used in this repo.

## Best-Fit MAF Classes

- `agent_framework.openai.OpenAIChatClient`
  - Connects to your OpenAI-compatible endpoint from `.env`.
  - Used for direct conversational responses and tool-capable agent execution.

- `agent_framework.Agent`
  - Implements user-facing direct response behavior and weather-tool behavior.
  - Used in `chanakya_mvp/maf_runtime.py` (`ChanakyaDirectAgent`, `ChanakyaWeatherAgent`).

- `agent_framework.tool` decorator
  - Defines strongly typed tool contracts that MAF can expose to the model.
  - Used for `maf_weather_tool`.

- `agent_framework.executor` decorator
  - Defines workflow execution steps as first-class MAF executors.
  - Used for `developer_step`, `tester_step`, and `aggregate_step`.

- `agent_framework.WorkflowBuilder`
  - Builds the delegated execution graph and enforces ordered execution via edges.
  - Used in `AgentManager._run_maf_workflow`.

- `agent_framework.WorkflowContext`
  - Handles inter-step messaging (`send_message`) and final output (`yield_output`).
  - Used by each delegated workflow step.

## Requirement Coverage Notes

- **PA Routing (FR-1..FR-4):** Implemented by `ChanakyaPA`, with MAF agents used for direct/tool branches.
- **Tool Support (FR-5..FR-7):** MAF tool path implemented with `@tool` + `Agent(tools=[...])`.
- **Delegation + Decomposition (FR-8..FR-12):** MAF workflow graph used for delegated execution.
- **Dependency Handling (FR-22..FR-24):** Enforced in `tester_step` using persisted dependency state.
- **Input Loop (FR-25..FR-28):** Waiting-input state emitted by workflow and resumed by follow-up linkage.
- **Observability (FR-29..FR-30):** Persisted SQLite task data + transition history + JSONL runtime logs.

## What Is MAF-Native vs Custom

- **MAF-native:** agent execution, tool integration, workflow composition, step messaging.
- **Custom glue:** domain task schema, state transition persistence, route heuristics, scenario harness.
