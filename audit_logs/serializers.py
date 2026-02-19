from rest_framework import serializers
from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            'id', 'action', 'entity_type', 'entity_id',
            'details', 'ip_address', 'created_at',
            'actor', 'actor_email', 'actor_name',
        ]
        read_only_fields = fields

    def get_actor_email(self, obj):
        return obj.actor.email if obj.actor else 'system'

    def get_actor_name(self, obj):
        if not obj.actor:
            return 'System'
        return obj.actor.get_full_name() or obj.actor.email