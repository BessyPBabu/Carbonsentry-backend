from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DocumentValidationViewSet,
    VendorRiskProfileViewSet,
    ManualReviewQueueViewSet
)

router = DefaultRouter()
router.register(r'validations', DocumentValidationViewSet, basename='validation')
router.register(r'risk-profiles', VendorRiskProfileViewSet, basename='risk-profile')
router.register(r'manual-reviews', ManualReviewQueueViewSet, basename='manual-review')

urlpatterns = [
    path('', include(router.urls)),
]