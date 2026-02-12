from django.apps import AppConfig

class AiValidationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ai_validation'

    def ready(self):
        import ai_validation.signals  # noqa