import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from decimal import Decimal

from django.utils import timezone

from .document_preprocessor import DocumentPreprocessor
from .input_gate import run as gate_run
from .relevance_classifier import RelevanceClassifier
from .authenticity_analyzer import AuthenticityAnalyzer
from .metadata_extractor import MetadataExtractor
from .risk_calculator import RiskCalculator
from ..models import DocumentValidation, ManualReviewQueue
from ..constants import MIN_AUTO_APPROVE_CONFIDENCE

logger = logging.getLogger(__name__)

# Parallel step wall-clock budget (seconds).
# Relevance + authenticity run concurrently inside this window.
_PARALLEL_TIMEOUT = 12


class ValidationOrchestrator:

    def __init__(self):
        self.preprocessor = DocumentPreprocessor()
        self.relevance = RelevanceClassifier()
        self.authenticity = AuthenticityAnalyzer()
        self.extractor = MetadataExtractor()
        self.risk = RiskCalculator()

    def validate_document(self, document, validation) -> DocumentValidation:
        logger.info("orchestrator: start document=%s validation=%s", document.id, validation.id)

        if validation.status != "processing":
            validation.status = "processing"
            if not validation.started_at:
                validation.started_at = timezone.now()
            validation.save(update_fields=["status", "started_at"])

        file_path = document.file.path if document.file else ""

        # ── Gate 1: local fast-fail (no Gemini cost) ────────────────────────
        passed, reason = gate_run(file_path)
        if not passed:
            logger.info("orchestrator: gate rejected document=%s reason=%s", document.id, reason)
            return self._fail(validation, "input_gate", f"rejected_by_gate:{reason}")

        # ── Preprocess ───────────────────────────────────────────────────────
        self._step(validation, "readability")
        ok, image_b64, err = self.preprocessor.process(file_path)
        if not ok:
            return self._fail(validation, "preprocessing", err)

        # ── Parallel: relevance + authenticity ──────────────────────────────
        # Both calls run concurrently. If relevance returns is_relevant=False
        # with high confidence we skip extraction entirely.
        self._step(validation, "relevance")
        rel_result, auth_result = self._parallel_classify(image_b64, validation, file_path)

        validation.is_relevant = rel_result.get("is_relevant", True)
        validation.detected_document_type = rel_result.get("document_type", "Emission Report")
        validation.relevance_confidence = rel_result.get("confidence")

        validation.authenticity_score = auth_result.get("score")
        validation.authenticity_indicators = auth_result.get("indicators", [])
        validation.authenticity_red_flags = auth_result.get("red_flags", [])
        validation.readability_passed = True  # gate already ensured file is readable
        validation.readability_score = None
        validation.readability_issues = []

        validation.save(update_fields=[
            "is_relevant", "detected_document_type", "relevance_confidence",
            "authenticity_score", "authenticity_indicators", "authenticity_red_flags",
            "readability_passed", "readability_score", "readability_issues",
        ])

        # Early exit: clearly irrelevant document → no extraction cost
        if not validation.is_relevant and (rel_result.get("confidence") or 0) >= 70:
            logger.info(
                "orchestrator: early exit — irrelevant document=%s confidence=%.1f",
                document.id, rel_result.get("confidence", 0),
            )
            validation.overall_confidence = Decimal("20.0")
            validation.requires_manual_review = True
            validation.flagged_reason = "Document is not a carbon compliance document"
            validation.save(update_fields=["overall_confidence", "requires_manual_review", "flagged_reason"])
            self._queue(validation, "high", "Not a compliance document")
            return self._complete(validation, document, metadata=None)

        # ── Extraction ───────────────────────────────────────────────────────
        self._step(validation, "extraction")
        ok, metadata, err = self.extractor.extract(image_b64, validation, file_path)
        if not ok:
            return self._fail(validation, "extraction", err)

        # ── Confidence + flag decision ────────────────────────────────────────
        confidence = self._confidence(validation, metadata)
        validation.overall_confidence = confidence

        should_flag, flag_reason = self._flag_decision(validation, metadata)
        validation.requires_manual_review = should_flag
        validation.flagged_reason = flag_reason if should_flag else ""
        validation.save(update_fields=["overall_confidence", "requires_manual_review", "flagged_reason"])

        if should_flag:
            self._queue(validation, self._priority(validation), flag_reason)

        # ── Risk calculation ─────────────────────────────────────────────────
        self._step(validation, "risk_analysis")
        try:
            self.risk.calculate(document.vendor)
        except Exception as exc:
            # Risk calc is non-fatal — document validation still completes
            logger.warning("orchestrator: risk calc failed document=%s — %s", document.id, exc)

        return self._complete(validation, document, metadata)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _parallel_classify(
        self, image_b64: str, validation, file_path: str
    ) -> tuple[dict, dict]:
        """Run relevance and authenticity concurrently. Returns (rel, auth) dicts."""
        rel_result = None
        auth_result = None

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_rel = pool.submit(self.relevance.classify, image_b64, validation, file_path)
                f_auth = pool.submit(self.authenticity.analyze, image_b64, validation, file_path)

                for future in as_completed([f_rel, f_auth], timeout=_PARALLEL_TIMEOUT):
                    try:
                        _, result, _ = future.result()
                        if future is f_rel:
                            rel_result = result
                        else:
                            auth_result = result
                    except Exception as exc:
                        logger.warning("orchestrator: parallel step error — %s", exc)

        except FuturesTimeout:
            logger.warning(
                "orchestrator: parallel classify timed out after %ds validation=%s",
                _PARALLEL_TIMEOUT, validation.id,
            )
        except Exception as exc:
            logger.error("orchestrator: parallel classify unexpected error — %s", exc)

        return (
            rel_result or self.relevance._default(),
            auth_result or self.authenticity._default(),
        )

    def _confidence(self, validation, metadata) -> Decimal:
        score = 0.0
        score += float(validation.relevance_confidence or 60) * 0.30
        score += float(validation.authenticity_score or 60) * 0.30

        if metadata:
            fields = [
                metadata.co2_extraction_confidence,
                metadata.issue_date_confidence,
                metadata.expiry_date_confidence,
                metadata.issuing_authority_confidence,
            ]
            values = [float(f) for f in fields if f is not None]
            avg_extraction = sum(values) / len(values) if values else 30.0
        else:
            avg_extraction = 30.0

        score += avg_extraction * 0.40
        return Decimal(str(round(score, 2)))

    def _flag_decision(self, validation, metadata) -> tuple[bool, str]:
        """
        Determines if a document needs human review.
        Covers all demo test cases: low confidence, red flags, expired docs,
        missing critical fields, irrelevant.
        """
        reasons = []

        conf = float(validation.overall_confidence or 0)
        if conf < MIN_AUTO_APPROVE_CONFIDENCE:
            reasons.append(f"low_confidence:{conf:.1f}")

        red_flags = validation.authenticity_red_flags or []
        if len(red_flags) >= 2:
            reasons.append(f"authenticity_red_flags:{','.join(red_flags[:3])}")

        if validation.is_relevant is False:
            reasons.append("irrelevant_document")

        # Expired document — caught here for the AI review queue demo case
        if metadata and metadata.expiry_date:
            from datetime import date
            if metadata.expiry_date < date.today():
                reasons.append("document_expired")

        # Missing CO2 value on a document that claims to be an emission report
        if (
            metadata
            and not metadata.co2_value
            and validation.detected_document_type in ("Emission Report", "GHG Inventory Report")
        ):
            reasons.append("missing_co2_value")

        if reasons:
            return True, ";".join(reasons)
        return False, ""

    def _priority(self, validation) -> str:
        red_flags = validation.authenticity_red_flags or []
        if len(red_flags) >= 3:
            return "high"
        conf = float(validation.overall_confidence or 0)
        if conf < 40:
            return "high"
        if conf < MIN_AUTO_APPROVE_CONFIDENCE:
            return "medium"
        return "low"

    def _queue(self, validation, priority: str, reason: str):
        try:
            ManualReviewQueue.objects.get_or_create(
                document_validation=validation,
                defaults={"priority": priority, "reason": reason[:255]},
            )
        except Exception as exc:
            logger.error(
                "orchestrator: review queue insert failed validation=%s — %s", validation.id, exc
            )

    def _step(self, validation, step: str):
        try:
            validation.current_step = step
            validation.save(update_fields=["current_step"])
        except Exception as exc:
            logger.warning("orchestrator: step save failed step=%s — %s", step, exc)

    def _complete(self, validation, document, metadata) -> DocumentValidation:
        completed_at = timezone.now()
        elapsed = self._elapsed(validation.started_at, completed_at)

        try:
            validation.status = "completed"
            validation.current_step = "completed"
            validation.completed_at = completed_at
            validation.total_processing_time_seconds = elapsed
            validation.save(update_fields=[
                "status", "current_step", "completed_at", "total_processing_time_seconds"
            ])

            if validation.requires_manual_review:
                document.status = "flagged"
            else:
                document.status = "valid"

            if metadata and metadata.expiry_date:
                document.expiry_date = metadata.expiry_date

            document.save()

        except Exception as exc:
            logger.error("orchestrator: complete save failed document=%s — %s", document.id, exc)

        logger.info(
            "orchestrator: done document=%s confidence=%.1f flagged=%s elapsed=%ss",
            document.id,
            float(validation.overall_confidence or 0),
            validation.requires_manual_review,
            elapsed,
        )
        return validation

    def _fail(self, validation, step: str, error: str | None) -> DocumentValidation:
        logger.error("orchestrator: fail validation=%s step=%s error=%s", validation.id, step, error)
        completed_at = timezone.now()
        try:
            validation.status = "failed"
            validation.current_step = step
            validation.error_message = str(error or "")[:1000]
            validation.completed_at = completed_at
            validation.requires_manual_review = True
            validation.flagged_reason = f"failed_at:{step}"
            validation.total_processing_time_seconds = self._elapsed(validation.started_at, completed_at)
            validation.save()

            self._queue(validation, "high", f"Validation failed at {step}")

            try:
                validation.document.status = "invalid"
                validation.document.save(update_fields=["status"])
            except Exception as exc:
                logger.error("orchestrator: document status update failed — %s", exc)

        except Exception as exc:
            logger.error("orchestrator: fail save itself failed validation=%s — %s", validation.id, exc)

        return validation

    @staticmethod
    def _elapsed(started_at, completed_at) -> int | None:
        try:
            if started_at and completed_at:
                return int((completed_at - started_at).total_seconds())
        except Exception:
            pass
        return None