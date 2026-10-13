from django.urls import path
from . import views

app_name = 'monitoring'

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('incidents/', views.incident_list_view, name='incidents'),
    path('incidents/<uuid:incident_id>/review/', views.review_incident_view, name='review_incident'),
    path('students/', views.student_list_view, name='students'),
    path('cameras/', views.camera_config_view, name='camera_config'),
    path('stream/<str:camera_id>/', views.video_feed, name='video_feed'),
    path('api/camera/start/', views.start_camera_api, name='start_camera_api'),
    path('api/camera/stop/', views.stop_camera_api, name='stop_camera_api'),
]
