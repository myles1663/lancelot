import os
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

class CalendarService:
    def __init__(self):
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticates with Google Calendar API or falls back to mock."""
        creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if creds_path and os.path.exists(creds_path):
            try:
                self.creds = service_account.Credentials.from_service_account_file(
                    creds_path, scopes=['https://www.googleapis.com/auth/calendar']
                )
                self.service = build('calendar', 'v3', credentials=self.creds)
                print("Authenticated with Google Calendar API.")
            except Exception as e:
                print(f"Error authenticating: {e}. Falling back to mock.")
                self.service = None
        else:
            print("No Google Credentials found. Using Mock Calendar Service.")
            self.service = None

    def create_event(self, summary: str, description: str = "") -> str:
        """Creates a calendar event (or mocks it)."""
        
        # Calculate time (starts next hour, 1 hour duration)
        now = datetime.datetime.utcnow()
        start_time = (now + datetime.timedelta(hours=1)).isoformat() + 'Z'
        end_time = (now + datetime.timedelta(hours=2)).isoformat() + 'Z'
        
        if self.service:
            event = {
                'summary': summary,
                'description': description,
                'start': {'dateTime': start_time},
                'end': {'dateTime': end_time},
            }
            try:
                event = self.service.events().insert(calendarId='primary', body=event).execute()
                link = event.get('htmlLink')
                return f"Event created: {link}"
            except Exception as e:
                return f"Failed to create event: {e}"
        else:
            # Mock Behavior
            return f"[MOCK] Event '{summary}' created for {start_time}. (Link: http://mock-calendar/event-123)"

if __name__ == "__main__":
    svc = CalendarService()
    print(svc.create_event("Test Meeting"))
