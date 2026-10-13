from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/alerts/(?P<session_id>[\w-]+)/$', consumers.ProctorAlertConsumer.as_asgi()),
    re_path(r'ws/alerts/$', consumers.ProctorAlertConsumer.as_asgi()),
]
