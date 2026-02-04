from django.urls import path

from .views import EventIngestView, FeedView

urlpatterns = [
    # Support both with and without trailing slash (POST redirects are painful).
    path("events", EventIngestView.as_view(), name="events-ingest"),
    path("events/", EventIngestView.as_view(), name="events-ingest-slash"),

    path("feed", FeedView.as_view(), name="feed"),
    path("feed/", FeedView.as_view(), name="feed-slash"),
]
