import logging
import mimetypes
from django.http import FileResponse, Http404, HttpResponse
from django.views import View
from django.contrib.auth.mixins import LoginMixin
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from pathlib import Path
from django.conf import settings
from vendors.models import Document

logger = logging.getLogger("vendors.media_views")


class DocumentFileView(APIView):
    """
    Serve document files with proper authentication
    Allows both authenticated users and public token access
    """
    permission_classes = [AllowAny]  # We handle auth manually
    
    def get(self, request, document_id):
        try:
            # Try to get document
            try:
                document = Document.objects.select_related('vendor').get(id=document_id)
            except Document.DoesNotExist:
                logger.warning(f"Document not found: {document_id}")
                raise Http404("Document not found")
            
            # Check if file exists
            if not document.file:
                logger.warning(f"Document has no file: {document_id}")
                raise Http404("Document file not found")
            
            # Authorization check
            # Allow if: authenticated user from same org OR valid upload token
            is_authorized = False
            
            # Check authenticated user
            if request.user.is_authenticated:
                if hasattr(request.user, 'organization'):
                    if document.vendor.organization == request.user.organization:
                        is_authorized = True
                        logger.debug(f"Authorized by user organization: {request.user.email}")
            
            # Check upload token (for vendor public access)
            token = request.GET.get('token')
            if token and document.vendor.upload_token == token:
                is_authorized = True
                logger.debug(f"Authorized by upload token for vendor: {document.vendor.id}")
            
            if not is_authorized:
                logger.warning(
                    f"Unauthorized access attempt to document: {document_id}",
                    extra={"user": request.user.email if request.user.is_authenticated else "anonymous"}
                )
                return HttpResponse("Unauthorized", status=403)
            
            # Get file path
            file_path = Path(settings.MEDIA_ROOT) / str(document.file)
            
            if not file_path.exists():
                logger.error(f"File does not exist on disk: {file_path}")
                raise Http404("File not found on server")
            
            # Determine content type
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = 'application/octet-stream'
            
            # Open and serve file
            try:
                response = FileResponse(
                    open(file_path, 'rb'),
                    content_type=content_type
                )
                
                # Set filename for download
                response['Content-Disposition'] = f'inline; filename="{file_path.name}"'
                
                logger.info(
                    f"Document file served successfully: {document_id}",
                    extra={
                        "document_id": str(document_id),
                        "vendor_id": str(document.vendor.id),
                        "file_size": file_path.stat().st_size
                    }
                )
                
                return response
                
            except Exception as e:
                logger.exception(f"Error opening file: {file_path}")
                raise Http404("Error reading file")
        
        except Http404:
            raise
        except Exception as e:
            logger.exception(f"Unexpected error serving document: {document_id}")
            return HttpResponse("Server error", status=500)


class DocumentDownloadView(APIView):
    """
    Force download of document file
    """
    permission_classes = [AllowAny]
    
    def get(self, request, document_id):
        try:
            # Get document
            try:
                document = Document.objects.select_related('vendor').get(id=document_id)
            except Document.DoesNotExist:
                raise Http404("Document not found")
            
            if not document.file:
                raise Http404("Document file not found")
            
            # Authorization
            is_authorized = False
            
            if request.user.is_authenticated:
                if hasattr(request.user, 'organization'):
                    if document.vendor.organization == request.user.organization:
                        is_authorized = True
            
            token = request.GET.get('token')
            if token and document.vendor.upload_token == token:
                is_authorized = True
            
            if not is_authorized:
                return HttpResponse("Unauthorized", status=403)
            
            # Get file
            file_path = Path(settings.MEDIA_ROOT) / str(document.file)
            
            if not file_path.exists():
                raise Http404("File not found on server")
            
            # Serve as download
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = 'application/octet-stream'
            
            response = FileResponse(
                open(file_path, 'rb'),
                content_type=content_type
            )
            
            # Force download
            filename = f"{document.document_type.name}_{document.vendor.name}.{file_path.suffix}"
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-'))
            
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            
            logger.info(f"Document downloaded: {document_id}")
            
            return response
            
        except Http404:
            raise
        except Exception as e:
            logger.exception(f"Download error: {document_id}")
            return HttpResponse("Server error", status=500)