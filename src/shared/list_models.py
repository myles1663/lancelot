import google.generativeai as genai
import os

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("No API Key found")
else:
    genai.configure(api_key=api_key)
    print("Listing models...")
    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                print(m.name)
    except Exception as e:
        print(f"Error listing models: {e}")
