from django.apps import AppConfig


class CommunicationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'communication'

    def ready(self):
        # connect signal handlers
        import communication.signals  # noqa: F401