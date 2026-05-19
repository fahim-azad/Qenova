from django.urls import path
from . import views

urlpatterns = [
    path('organizations/', views.org_list_view, name='org_list'),
    path('organizations/<int:org_id>/', views.org_detail_view, name='org_detail'),
    path('organizations/<int:org_id>/book/', views.book_queue_view, name='book_queue'),
    path('booking/success/<int:token_id>/', views.booking_success_view, name='booking_success'),
    path('organizations/<int:org_id>/status-api/', views.queue_status_api, name='queue_status_api'),
    path('booking/<int:booking_id>/cancel/', views.cancel_booking_view, name='cancel_booking'),
    path('booking/<int:booking_id>/reschedule/', views.reschedule_booking_view, name='reschedule_booking'),
    path('organizations/<int:org_id>/analytics/', views.analytics_view, name='analytics'),
    path('organizations/<int:org_id>/feedback/', views.submit_feedback_view, name='submit_feedback'),
]
