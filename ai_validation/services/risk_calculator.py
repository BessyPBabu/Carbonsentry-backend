import logging
from decimal import Decimal
from datetime import date, timedelta

from ..models import IndustryEmissionThreshold, VendorRiskProfile
from ..constants import DEFAULT_THRESHOLDS

logger = logging.getLogger(__name__)


class RiskCalculator:

    def calculate(self, vendor):
        logger.info("calculate: starting for vendor %s", vendor.id)

        risk_profile, _ = VendorRiskProfile.objects.get_or_create(
            vendor=vendor,
            defaults={'organization': vendor.organization}
        )

        all_docs = vendor.documents.all()
        total_docs = all_docs.count()

        completed = all_docs.filter(
            validation__status='completed'
        ).select_related('validation', 'validation__metadata')

        validated_docs = completed.count()
        flagged_docs = completed.filter(validation__requires_manual_review=True).count()

        total_emissions = Decimal('0')
        confidences = []
        earliest_expiry = None

        for doc in completed:
            v = doc.validation

            if v.overall_confidence:
                confidences.append(float(v.overall_confidence))

            if hasattr(v, 'metadata') and v.metadata:
                meta = v.metadata
                if meta.co2_value:
                    val = meta.co2_value / Decimal('1000') if meta.co2_unit == 'kg' else meta.co2_value
                    total_emissions += val

                if meta.expiry_date:
                    if earliest_expiry is None or meta.expiry_date < earliest_expiry:
                        earliest_expiry = meta.expiry_date

        try:
            threshold = self._get_threshold(vendor.industry)
        except Exception as e:
            logger.exception("calculate: failed to get threshold for vendor %s", vendor.id)
            raise

        risk_level = self._risk_level(total_emissions, threshold, total_docs, validated_docs)
        risk_score = self._risk_score(total_emissions, threshold, flagged_docs, total_docs, earliest_expiry)
        avg_confidence = Decimal(str(sum(confidences) / len(confidences))) if confidences else None

        risk_profile.risk_level = risk_level
        risk_profile.risk_score = Decimal(str(risk_score))
        risk_profile.total_documents = total_docs
        risk_profile.validated_documents = validated_docs
        risk_profile.flagged_documents = flagged_docs
        risk_profile.total_co2_emissions = total_emissions if total_emissions > 0 else None
        risk_profile.exceeds_threshold = total_emissions > threshold.high_threshold if total_emissions > 0 else False
        risk_profile.avg_document_confidence = avg_confidence

        try:
            risk_profile.save()
        except Exception as e:
            logger.exception("calculate: failed to save risk profile for vendor %s", vendor.id)
            raise

        vendor.risk_level = risk_level
        vendor.save(update_fields=['risk_level'])

        logger.info(
            "calculate: vendor=%s level=%s score=%.2f emissions=%s",
            vendor.id, risk_level, risk_score, total_emissions,
        )
        return risk_profile

    def _get_threshold(self, industry):
        try:
            return IndustryEmissionThreshold.objects.get(industry=industry)
        except IndustryEmissionThreshold.DoesNotExist:
            pass

        defaults = DEFAULT_THRESHOLDS.get(industry.name, DEFAULT_THRESHOLDS['default'])
        threshold, created = IndustryEmissionThreshold.objects.get_or_create(
            industry=industry,
            defaults={
                'low_threshold':      Decimal(str(defaults['low'])),
                'medium_threshold':   Decimal(str(defaults['medium'])),
                'high_threshold':     Decimal(str(defaults['high'])),
                'critical_threshold': Decimal(str(defaults['critical'])),
            }
        )
        if created:
            logger.info("_get_threshold: created default threshold for industry %s", industry.name)
        return threshold

    def _risk_level(self, emissions, threshold, total_docs, validated_docs):
        if validated_docs == 0:
            return 'medium'  # unknown until something is validated

        if emissions == 0:
            coverage = validated_docs / total_docs if total_docs > 0 else 0
            return 'high' if coverage < 0.5 else 'medium'

        if emissions <= threshold.low_threshold:
            return 'low'
        if emissions <= threshold.medium_threshold:
            return 'medium'
        if emissions <= threshold.high_threshold:
            return 'high'
        return 'critical'

    def _risk_score(self, emissions, threshold, flagged, total, expiry_date):
        # score is 0-100; frontend displays as score/20 → X.X / 5
        score = 0.0

        # emissions component (0-50)
        if emissions > 0:
            if emissions > threshold.critical_threshold:
                score += 50
            elif emissions > threshold.high_threshold:
                score += 40
            elif emissions > threshold.medium_threshold:
                score += 25
            elif emissions > threshold.low_threshold:
                score += 15
            else:
                score += 5
        else:
            score += 20  # no data — moderate penalty

        # flagged documents component (0-25)
        if total > 0:
            score += (flagged / total) * 25

        # expiry component (0-25)
        if expiry_date:
            today = date.today()
            if expiry_date < today:
                score += 25
            elif expiry_date < today + timedelta(days=30):
                score += 15
            elif expiry_date < today + timedelta(days=90):
                score += 5

        return round(min(score, 100), 2)