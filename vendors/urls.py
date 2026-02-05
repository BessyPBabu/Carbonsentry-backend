from django.urls import path
from vendors.views.bulk_views import VendorBulkUploadView
from vendors.views.upload_views import VendorPublicUploadView
from vendors.views.config_views import (
    IndustryListCreateView,
    DocumentTypeListCreateView,
    IndustryRequiredDocumentListCreateView,
)
from vendors.views.vendor_views import (
    VendorListCreateView,
    VendorDetailView,
    VendorDocumentListView,
)
from vendors.views.document_views import (
    DocumentListView,
    DocumentDetailView,
)


urlpatterns = [
    path("bulk-upload/", VendorBulkUploadView.as_view()),

    path("", VendorListCreateView.as_view()),
    path("<uuid:vendor_id>/", VendorDetailView.as_view()),
    path("<uuid:vendor_id>/documents/", VendorDocumentListView.as_view()),

    path("upload/<str:token>/", VendorPublicUploadView.as_view()),

    path("config/industries/", IndustryListCreateView.as_view()),
    path("config/document-types/", DocumentTypeListCreateView.as_view()),
    path("config/industry-documents/", IndustryRequiredDocumentListCreateView.as_view()),
    path("documents/", DocumentListView.as_view(), name="documents-list"),
    path("documents/<uuid:document_id>/", DocumentDetailView.as_view(), name="document-detail"),
]
