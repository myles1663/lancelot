
import os
import json
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from google import genai
from google.genai import types

class ParamItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(..., description="Parameter name")
    value: str = Field(..., description="Parameter value")

class PlanStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(..., description="Step number, starting from 1")
    description: str = Field(..., description="Clear description of the task step")
    tool: str = Field(..., description="Tool to use (e.g., 'read_file', 'grep_search', 'execute_command')")
    params: List[ParamItem] = Field(..., description="List of parameters for the tool")
    dependencies: List[int] = Field(default_factory=list, description="IDs of steps that must complete before this one")

class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(..., description="The high-level goal this plan achieves")
    steps: List[PlanStep] = Field(..., description="Ordered list of steps")

class Planner:
    def __init__(self, model_name="gemini-2.0-flash"):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = model_name
        self.client = None
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key, http_options={'api_version': 'v1alpha'})

    def create_plan(self, goal: str, context: str) -> Optional[Plan]:
        """Generates a structured plan using Gemini."""
        if not self.client:
            print("Planner Error: Gemini Client not initialized.")
            return None

        prompt = f"""
        You are an Autonomous Agent Planner.
        GOAL: {goal}
        
        CONTEXT:
        {context}
        
        Create a detailed, step-by-step execution plan using the available tools:
        - read_file(path)
        - list_workspace(dir)
        - search_workspace(query)
        - execute_command(command)
        - write_to_file(path, content)
        
        For parameters, provide a list of key-value pairs.
        
        Return the plan in STRICT JSON format matching the schema.
        """
        
        # Manual Schema to avoid SDK conversion bugs (snake_case additional_properties)
        schema = {
            "type": "OBJECT",
            "properties": {
                "goal": {"type": "STRING"},
                "steps": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "id": {"type": "INTEGER"},
                            "description": {"type": "STRING"},
                            "tool": {"type": "STRING"},
                            "params": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "key": {"type": "STRING"},
                                        "value": {"type": "STRING"}
                                    },
                                    "required": ["key", "value"]
                                }
                            },
                            "dependencies": {"type": "ARRAY", "items": {"type": "INTEGER"}}
                        },
                        "required": ["id", "description", "tool", "params"]
                    }
                }
            },
            "required": ["goal", "steps"]
        }

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.2
                )
            )
            
            # Parse response
            plan_data = json.loads(response.text)
            return Plan(**plan_data)
            
        except Exception as e:
            print(f"Planner Error: {e}")
            return None
