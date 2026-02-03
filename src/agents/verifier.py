
import os
import json
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from google import genai
from google.genai import types

class VerificationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    success: bool = Field(..., description="Whether the step was completed successfully")
    reason: str = Field(..., description="Explanation of the verdict")
    correction_suggestion: Optional[str] = Field(None, description="If failed, what should be done differently?")

class Verifier:
    def __init__(self, model_name="gemini-2.0-flash"):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = model_name
        self.client = None
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key, http_options={'api_version': 'v1alpha'})

    def verify_step(self, goal: str, output: str, context: str = "") -> VerificationResult:
        """Verifies if the output satisfies the goal."""
        if not self.client:
            return VerificationResult(success=False, reason="Gemini Client not initialized.")

        prompt = f"""
        You are an Autonomous Verifier.
        
        STEP GOAL:
        {goal}
        
        EXECUTION OUTPUT:
        {output}
        
        CONTEXT SNAPSHOT:
        {context[:2000]}... (truncated)
        
        Did the execution output successfully achieve the step goal?
        If NO, provide a correction suggestion.
        
        Return STRICT JSON.
        """
        
        # Manual Schema
        schema = {
            "type": "OBJECT",
            "properties": {
                "success": {"type": "BOOLEAN"},
                "reason": {"type": "STRING"},
                "correction_suggestion": {"type": "STRING"}
            },
            "required": ["success", "reason"]
        }

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1
                )
            )
            
            data = json.loads(response.text)
            return VerificationResult(**data)
            
        except Exception as e:
            return VerificationResult(success=False, reason=f"Verification Error: {e}")
