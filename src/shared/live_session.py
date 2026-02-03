"""Live API session manager for real-time multimodal interaction.

Manages async WebSocket sessions with Gemini's Live API, enabling
low-latency text streaming with session memory.
"""
import asyncio
from google import genai
from google.genai import types


class LiveSessionManager:
    """Manages a single Gemini Live API session."""

    def __init__(self, client, model_name: str, system_instruction: str = ""):
        self.client = client
        self.model_name = model_name
        self.system_instruction = system_instruction
        self._session = None

    async def connect(self):
        """Opens a live session with Gemini.

        Returns the session object for direct use if needed.
        """
        config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction=types.Content(
                parts=[types.Part(text=self.system_instruction)]
            ),
        )
        self._session = await self.client.aio.live.connect(
            model=self.model_name,
            config=config,
        )
        return self._session

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def send_text(self, text: str):
        """Sends text input and yields response chunks as they arrive.

        Args:
            text: The user message to send.

        Yields:
            str: Response text chunks from the model.
        """
        if not self._session:
            raise RuntimeError("Session not connected. Call connect() first.")

        await self._session.send(input=text, end_of_turn=True)

        async for response in self._session.receive():
            if response.text:
                yield response.text

    async def close(self):
        """Closes the live session and cleans up resources."""
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
