from django.contrib import admin
from .models import EmergencyRequest, PriorityQueue, EmergencyAnalytics, SlotSwap, EmergencyApprovalLog

@admin.register(EmergencyRequest)
class EmergencyRequestAdmin(admin.ModelAdmin):
    list_display = ('token', 'emergency_type', 'status', 'created_at')
    list_filter = ('status', 'emergency_type')
    search_fields = ('token__serial_number', 'token__user__username')

@admin.register(PriorityQueue)
class PriorityQueueAdmin(admin.ModelAdmin):
    list_display = ('token', 'priority_serial', 'insertion_position', 'urgency_level')
    search_fields = ('token__serial_number', 'priority_serial')

@admin.register(EmergencyAnalytics)
class EmergencyAnalyticsAdmin(admin.ModelAdmin):
    list_display = ('organization', 'total_emergencies', 'approved_requests', 'rejected_requests')

@admin.register(SlotSwap)
class SlotSwapAdmin(admin.ModelAdmin):
    list_display = ('requester', 'target_user', 'current_slot', 'requested_slot', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('requester__username', 'target_user__username')

@admin.register(EmergencyApprovalLog)
class EmergencyApprovalLogAdmin(admin.ModelAdmin):
    list_display = ('request', 'action', 'reviewed_by', 'timestamp')
    list_filter = ('action',)
    search_fields = ('request__token__serial_number', 'reviewed_by__username')
