import logging
import mimetypes
from django.http import FileResponse, Http404, HttpResponse
from django.views import View
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from pathlib import Path
from django.conf import settings
from vendors.models import Document

logger = logging.getLogger("vendors.media_views")


class DocumentFileView(APIView):
    permission_classes = [AllowAny] 
    
    def get(self, request, document_id):
        try:
            document = Document.objects.select_related('vendor', 'vendor__organization').get(id=document_id)
            
            if not document.file:
                raise Http404("Document file not found")
            
            is_authorized = False
            if request.user.is_authenticated:
                try:
                    user_org = request.user.organization
                    doc_org = document.vendor.organization
                    
                    if user_org.id == doc_org.id:  
                        is_authorized = True
                        logger.debug(f"Authorized via org match: {request.user.email}")
                except AttributeError as e:
                    logger.warning(f"Organization comparison failed: {e}")
            
            token = request.GET.get('token')
            if not is_authorized and token:
                if document.vendor.upload_token and document.vendor.upload_token == token:
                    from django.utils import timezone
                    if document.vendor.upload_token_expires_at:
                        if document.vendor.upload_token_expires_at > timezone.now():
                            is_authorized = True
                            logger.debug("Authorized via valid token")
                        else:
                            logger.warning("Token expired")
                    else:
                        is_authorized = True
                        logger.debug("Authorized via token (no expiry)")
            
            if not is_authorized:
                logger.warning(
                    f"Access denied - document: {document_id}, user: {request.user}",
                    extra={
                        "authenticated": request.user.is_authenticated,
                        "has_org": hasattr(request.user, 'organization'),
                        "token_provided": bool(token)
                    }
                )
                return HttpResponse("Unauthorized", status=403)
            
            file_path = Path(settings.MEDIA_ROOT) / str(document.file)
            
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                raise Http404("File not found on server")
            
            content_type, _ = mimetypes.guess_type(str(file_path))
            response = FileResponse(open(file_path, 'rb'), content_type=content_type or 'application/octet-stream')
            response['Content-Disposition'] = f'inline; filename="{file_path.name}"'
            
            logger.info(f"File served: {document_id}")
            return response
            
        except Document.DoesNotExist:
            raise Http404("Document not found")
        except Exception as e:
            logger.exception(f"Error serving document {document_id}")
            return HttpResponse("Server error", status=500)
            

class DocumentDownloadView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request, document_id):
        try:
            try:
                document = Document.objects.select_related('vendor').get(id=document_id)
            except Document.DoesNotExist:
                raise Http404("Document not found")
            
            if not document.file:
                raise Http404("Document file not found")
            
            
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
            file_path = Path(settings.MEDIA_ROOT) / str(document.file)
            
            if not file_path.exists():
                raise Http404("File not found on server")
            
            content_type, _ = mimetypes.guess_type(str(file_path))
            if not content_type:
                content_type = 'application/octet-stream'
            
            response = FileResponse(
                open(file_path, 'rb'),
                content_type=content_type
            )
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