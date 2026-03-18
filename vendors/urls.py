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
    VendorSendEmailsView,  
)
from vendors.views.document_views import (
    DocumentListView,
    DocumentDetailView,
    DocumentResendLinkView
)

from vendors.views.media_views import (  
    DocumentFileView,
    DocumentDownloadView,
    
)

urlpatterns = [
    path("bulk-upload/", VendorBulkUploadView.as_view(), name="vendor-bulk-upload"),
    
    path("send-emails/", VendorSendEmailsView.as_view(), name="vendor-send-emails"),
    
    path("", VendorListCreateView.as_view(), name="vendor-list-create"),
    path("<uuid:vendor_id>/", VendorDetailView.as_view(), name="vendor-detail"),
    path("<uuid:vendor_id>/documents/", VendorDocumentListView.as_view(), name="vendor-documents"),
    path('documents/<uuid:document_id>/resend-link/', DocumentResendLinkView.as_view(), name='document-resend-link'),

    path("upload/<str:token>/", VendorPublicUploadView.as_view(), name="vendor-public-upload"),

    path("config/industries/", IndustryListCreateView.as_view(), name="industry-list-create"),
    path("config/document-types/", DocumentTypeListCreateView.as_view(), name="document-type-list-create"),
    path("config/industry-documents/", IndustryRequiredDocumentListCreateView.as_view(), name="industry-documents"),
    
    path("documents/", DocumentListView.as_view(), name="documents-list"),
    path("documents/<uuid:document_id>/", DocumentDetailView.as_view(), name="document-detail"),

    path("documents/<uuid:document_id>/file/", DocumentFileView.as_view(), name="document-file"),
    path("documents/<uuid:document_id>/download/", DocumentDownloadView.as_view(), name="document-download"),


]