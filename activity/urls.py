from django.urls import path

from .views import EventIngestView

urlpatterns = [
    # Support both with and without trailing slash (POST redirects are painful).
    path("events", EventIngestView.as_view(), name="events-ingest"),
    path("events/", EventIngestView.as_view(), name="events-ingest-slash"),
]
