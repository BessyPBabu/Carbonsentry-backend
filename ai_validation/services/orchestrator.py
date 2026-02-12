from django.utils import timezone
from .document_preprocessor import DocumentPreprocessor
from .readability_checker import ReadabilityChecker
from .relevance_classifier import RelevanceClassifier
from .authenticity_analyzer import AuthenticityAnalyzer
from .metadata_extractor import MetadataExtractor
from .risk_calculator import RiskCalculator
from ..models import DocumentValidation, ManualReviewQueue
from ..constants import MIN_AUTO_APPROVE_CONFIDENCE


class ValidationOrchestrator:
    def __init__(self):
        self.preprocessor = DocumentPreprocessor()
        self.readability_checker = ReadabilityChecker()
        self.relevance_classifier = RelevanceClassifier()
        self.authenticity_analyzer = AuthenticityAnalyzer()
        self.metadata_extractor = MetadataExtractor()
        self.risk_calculator = RiskCalculator()
    
    def validate_document(self, document):
        """Main orchestration method"""
        
        # Create validation record
        validation = DocumentValidation.objects.create(
            document=document,
            status='processing',
            started_at=timezone.now()
        )
        
        try:
            # Step 0: Preprocess document
            validation.current_step = 'readability'
            validation.save()
            
            success, image_base64, error = self.preprocessor.process(document.file.path)
            
            if not success:
                self._mark_failed(validation, 'preprocessing', error)
                return validation
            
            # Step 1: Readability Check
            success, result, error = self.readability_checker.check(image_base64, validation)
            
            if not success or not result['is_readable']:
                self._mark_failed(validation, 'readability', error or 'Document not readable')
                return validation
            
            validation.readability_passed = result['is_readable']
            validation.readability_score = result['quality_score']
            validation.readability_issues = result['issues']
            validation.save()
            
            # Step 2: Relevance Classification
            validation.current_step = 'relevance'
            validation.save()
            
            success, result, error = self.relevance_classifier.classify(image_base64, validation)
            
            if not success or not result['is_relevant']:
                self._mark_failed(validation, 'relevance', error or 'Document not relevant')
                return validation
            
            validation.is_relevant = result['is_relevant']
            validation.detected_document_type = result['document_type']
            validation.relevance_confidence = result['confidence']
            validation.save()
            
            # Step 3: Authenticity Analysis
            validation.current_step = 'authenticity'
            validation.save()
            
            success, result, error = self.authenticity_analyzer.analyze(image_base64, validation)
            
            if success:
                validation.authenticity_score = result['score']
                validation.authenticity_indicators = result['indicators']
                validation.authenticity_red_flags = result['red_flags']
                validation.save()
            
            # Step 4: Metadata Extraction
            validation.current_step = 'extraction'
            validation.save()
            
            success, metadata, error = self.metadata_extractor.extract(image_base64, validation)
            
            if not success:
                self._mark_failed(validation, 'extraction', error)
                return validation
            
            # Step 5: Calculate Overall Confidence
            overall_confidence = self._calculate_overall_confidence(validation)
            validation.overall_confidence = overall_confidence
            
            # Determine if manual review needed
            should_flag = self._should_flag_for_review(validation)
            validation.requires_manual_review = should_flag
            
            if should_flag:
                validation.flagged_reason = self._get_flag_reason(validation)
                
                # Create manual review queue entry
                ManualReviewQueue.objects.create(
                    document_validation=validation,
                    priority=self._get_priority(validation),
                    reason=validation.flagged_reason
                )
            
            # Step 6: Risk Analysis
            validation.current_step = 'risk_analysis'
            validation.save()
            
            self.risk_calculator.calculate(document.vendor)
            
            # Mark complete
            validation.status = 'completed'
            validation.current_step = 'completed'
            validation.completed_at = timezone.now()
            validation.total_processing_time_seconds = (
                validation.completed_at - validation.started_at
            ).seconds
            validation.save()
            
            # Update document status
            if should_flag:
                document.status = 'flagged'
            else:
                document.status = 'valid'
            
            if metadata and metadata.expiry_date:
                document.expiry_date = metadata.expiry_date
            
            document.save()
            
            return validation
            
        except Exception as e:
            self._mark_failed(validation, validation.current_step, str(e))
            return validation
    
    def _calculate_overall_confidence(self, validation):
        """Calculate weighted confidence score"""
        from decimal import Decimal
        
        scores = []
        
        if validation.readability_score:
            scores.append(float(validation.readability_score) * 0.15)
        
        if validation.relevance_confidence:
            scores.append(float(validation.relevance_confidence) * 0.25)
        
        if validation.authenticity_score:
            scores.append(float(validation.authenticity_score) * 0.30)
        
        if hasattr(validation, 'metadata'):
            metadata = validation.metadata
            extraction_scores = []
            
            if metadata.co2_extraction_confidence:
                extraction_scores.append(float(metadata.co2_extraction_confidence))
            if metadata.issue_date_confidence:
                extraction_scores.append(float(metadata.issue_date_confidence))
            if metadata.expiry_date_confidence:
                extraction_scores.append(float(metadata.expiry_date_confidence))
            
            if extraction_scores:
                avg_extraction = sum(extraction_scores) / len(extraction_scores)
                scores.append(avg_extraction * 0.30)
        
        if scores:
            return Decimal(str(sum(scores)))
        
        return Decimal('0')
    
    def _should_flag_for_review(self, validation):
        """Determine if document should be flagged"""
        if not validation.overall_confidence:
            return True
        
        if float(validation.overall_confidence) < MIN_AUTO_APPROVE_CONFIDENCE:
            return True
        
        if len(validation.authenticity_red_flags) >= 2:
            return True
        
        return False
    
    def _get_flag_reason(self, validation):
        """Get human-readable flag reason"""
        reasons = []
        
        if validation.overall_confidence and float(validation.overall_confidence) < MIN_AUTO_APPROVE_CONFIDENCE:
            reasons.append(f"Low confidence score ({validation.overall_confidence}%)")
        
        if validation.authenticity_red_flags:
            reasons.append(f"Authenticity concerns: {', '.join(validation.authenticity_red_flags[:3])}")
        
        return "; ".join(reasons) if reasons else "Requires verification"
    
    def _get_priority(self, validation):
        """Determine review priority"""
        if len(validation.authenticity_red_flags) >= 3:
            return 'high'
        
        if validation.overall_confidence and float(validation.overall_confidence) < 50:
            return 'high'
        
        return 'medium'
    
    def _mark_failed(self, validation, step, error):
        """Mark validation as failed"""
        validation.status = 'failed'
        validation.current_step = step
        validation.error_message = error
        validation.completed_at = timezone.now()
        validation.requires_manual_review = True
        validation.flagged_reason = f"Failed at {step}: {error}"
        validation.save()
        
        # Create manual review entry
        ManualReviewQueue.objects.create(
            document_validation=validation,
            priority='high',
            reason=f"Validation failed at {step}"
        )
        
        # Update document
        validation.document.status = 'invalid'
        validation.document.save()