import logging
from decimal import Decimal

from django.utils import timezone

from .document_preprocessor import DocumentPreprocessor
from .readability_checker import ReadabilityChecker
from .relevance_classifier import RelevanceClassifier
from .authenticity_analyzer import AuthenticityAnalyzer
from .metadata_extractor import MetadataExtractor
from .risk_calculator import RiskCalculator
from ..models import DocumentValidation, ManualReviewQueue
from ..constants import MIN_AUTO_APPROVE_CONFIDENCE

logger = logging.getLogger(__name__)


class ValidationOrchestrator:

    def __init__(self):
        self.preprocessor = DocumentPreprocessor()
        self.readability_checker = ReadabilityChecker()
        self.relevance_classifier = RelevanceClassifier()
        self.authenticity_analyzer = AuthenticityAnalyzer()
        self.metadata_extractor = MetadataExtractor()
        self.risk_calculator = RiskCalculator()

    def validate_document(self, document, validation):
        logger.info("validate_document: starting for document %s", document.id)

        try:
            # ── preprocessing ──────────────────────────────────────────────
            self._set_step(validation, 'readability')

            success, image_base64, error = self.preprocessor.process(document.file.path)
            if not success:
                logger.error("validate_document: preprocessing failed — %s", error)
                return self._mark_failed(validation, 'preprocessing', error)

            # ── step 1: readability (hard gate — only truly broken docs fail) ──
            success, result, _ = self.readability_checker.check(image_base64, validation)

            if success and result:
                validation.readability_passed = result['is_readable']
                validation.readability_score = result['quality_score']
                validation.readability_issues = result['issues']
            else:
                validation.readability_passed = True
                validation.readability_score = None
                validation.readability_issues = []

            validation.save(update_fields=['readability_passed', 'readability_score', 'readability_issues'])

            score = float(validation.readability_score or 100)
            if not validation.readability_passed and score < 20:
                logger.warning("validate_document: document %s truly unreadable (score=%.1f)", document.id, score)
                return self._mark_failed(
                    validation, 'readability',
                    f"Document unreadable (score={score}). Issues: {', '.join(validation.readability_issues)}"
                )

            # ── step 2: relevance (soft gate — affects confidence score only) ──
            self._set_step(validation, 'relevance')

            success, result, _ = self.relevance_classifier.classify(image_base64, validation)
            if success and result:
                validation.is_relevant = result['is_relevant']
                validation.detected_document_type = result['document_type']
                validation.relevance_confidence = result['confidence']
            else:
                validation.is_relevant = True
                validation.detected_document_type = 'Emission Report'
                validation.relevance_confidence = None

            validation.save(update_fields=['is_relevant', 'detected_document_type', 'relevance_confidence'])

            # ── step 3: authenticity (soft gate — affects confidence score only) ──
            self._set_step(validation, 'authenticity')

            success, result, _ = self.authenticity_analyzer.analyze(image_base64, validation)
            if success and result:
                validation.authenticity_score = result['score']
                validation.authenticity_indicators = result['indicators']
                validation.authenticity_red_flags = result['red_flags']
            else:
                validation.authenticity_score = None
                validation.authenticity_indicators = []
                validation.authenticity_red_flags = []

            validation.save(update_fields=['authenticity_score', 'authenticity_indicators', 'authenticity_red_flags'])

            # ── step 4: metadata extraction ────────────────────────────────
            self._set_step(validation, 'extraction')

            success, metadata, error = self.metadata_extractor.extract(image_base64, validation)
            if not success:
                logger.error("validate_document: extraction failed for %s — %s", document.id, error)
                return self._mark_failed(validation, 'extraction', error)

            # ── overall confidence + flag decision ─────────────────────────
            overall_confidence = self._calculate_confidence(validation, metadata)
            validation.overall_confidence = overall_confidence

            should_flag, flag_reason = self._check_flag(validation)
            validation.requires_manual_review = should_flag
            validation.flagged_reason = flag_reason if should_flag else ''

            if should_flag:
                ManualReviewQueue.objects.get_or_create(
                    document_validation=validation,
                    defaults={
                        'priority': self._get_priority(validation),
                        'reason': flag_reason,
                    }
                )

            validation.save(update_fields=['overall_confidence', 'requires_manual_review', 'flagged_reason'])

            # ── step 5: risk calculation ───────────────────────────────────
            self._set_step(validation, 'risk_analysis')

            try:
                self.risk_calculator.calculate(document.vendor)
            except Exception as e:
                # risk calc failing should not abort the validation
                logger.warning("validate_document: risk calculation non-fatal error — %s", e)

            # ── complete ───────────────────────────────────────────────────
            validation.status = 'completed'
            validation.current_step = 'completed'
            validation.completed_at = timezone.now()
            validation.total_processing_time_seconds = int(
                (validation.completed_at - validation.started_at).total_seconds()
            )
            validation.save(update_fields=[
                'status', 'current_step', 'completed_at', 'total_processing_time_seconds'
            ])

            document.status = 'flagged' if should_flag else 'valid'
            if metadata and metadata.expiry_date:
                document.expiry_date = metadata.expiry_date
            document.save()

            logger.info(
                "validate_document: completed — document=%s confidence=%.1f flagged=%s",
                document.id, float(overall_confidence), should_flag,
            )
            return validation

        except Exception as e:
            logger.exception("validate_document: unhandled error for document %s", document.id)
            return self._mark_failed(validation, validation.current_step or 'unknown', str(e))

    # ── helpers ────────────────────────────────────────────────────────────────

    def _set_step(self, validation, step):
        validation.current_step = step
        validation.save(update_fields=['current_step'])

    def _calculate_confidence(self, validation, metadata):
        score = 0.0

        # readability 10%
        score += float(validation.readability_score or 70) * 0.10

        # relevance 25%
        score += float(validation.relevance_confidence or 60) * 0.25

        # authenticity 25%
        score += float(validation.authenticity_score or 60) * 0.25

        # extraction 40%
        if metadata:
            fields = [
                metadata.co2_extraction_confidence,
                metadata.issue_date_confidence,
                metadata.expiry_date_confidence,
                metadata.issuing_authority_confidence,
            ]
            values = [float(f) for f in fields if f is not None]
            avg_extraction = (sum(values) / len(values)) if values else 30.0
        else:
            avg_extraction = 30.0

        score += avg_extraction * 0.40

        final = round(score, 2)
        logger.info("_calculate_confidence: validation=%s score=%.2f", validation.id, final)
        return Decimal(str(final))

    def _check_flag(self, validation):
        reasons = []

        conf = float(validation.overall_confidence or 0)
        if conf < MIN_AUTO_APPROVE_CONFIDENCE:
            reasons.append(f"Low confidence ({conf:.1f}% < {MIN_AUTO_APPROVE_CONFIDENCE}%)")

        red_flags = validation.authenticity_red_flags or []
        if len(red_flags) >= 3:
            reasons.append(f"Multiple authenticity concerns: {', '.join(red_flags[:3])}")

        if validation.is_relevant is False:
            reasons.append("Document may not be a compliance document")

        if reasons:
            return True, "; ".join(reasons)
        return False, ""

    def _get_priority(self, validation):
        if len(validation.authenticity_red_flags or []) >= 3:
            return 'high'
        conf = float(validation.overall_confidence or 0)
        if conf < 40:
            return 'high'
        if conf < MIN_AUTO_APPROVE_CONFIDENCE:
            return 'medium'
        return 'low'

    def _mark_failed(self, validation, step, error):
        logger.error("_mark_failed: validation=%s step=%s error=%s", validation.id, step, error)
        validation.status = 'failed'
        validation.current_step = step
        validation.error_message = str(error)[:1000]
        validation.completed_at = timezone.now()
        validation.requires_manual_review = True
        validation.flagged_reason = f"Failed at {step}: {error}"
        validation.save()

        ManualReviewQueue.objects.get_or_create(
            document_validation=validation,
            defaults={'priority': 'high', 'reason': f"Validation failed at {step}"}
        )

        validation.document.status = 'invalid'
        validation.document.save()
        return validation