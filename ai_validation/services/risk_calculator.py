from decimal import Decimal
from datetime import date, timedelta
from ..models import IndustryEmissionThreshold, VendorRiskProfile
from ..constants import DEFAULT_THRESHOLDS


class RiskCalculator:
    def calculate(self, vendor):
        """Calculate risk for vendor based on all documents"""
        
        # Get or create risk profile
        risk_profile, created = VendorRiskProfile.objects.get_or_create(
            vendor=vendor,
            defaults={'organization': vendor.organization}
        )
        
        # Get all validated documents
        validations = vendor.documents.filter(
            validation__status='completed'
        ).select_related('validation', 'validation__metadata')
        
        total_docs = vendor.documents.count()
        validated_docs = validations.count()
        flagged_docs = validations.filter(validation__requires_manual_review=True).count()
        
        # Calculate total emissions and confidence
        total_emissions = Decimal('0')
        confidences = []
        earliest_expiry = None
        
        for doc in validations:
            if hasattr(doc.validation, 'metadata'):
                metadata = doc.validation.metadata
                
                if metadata.co2_value:
                    # Convert to tonnes if in kg
                    if metadata.co2_unit == 'kg':
                        total_emissions += metadata.co2_value / Decimal('1000')
                    else:
                        total_emissions += metadata.co2_value
                
                # Track expiry
                if metadata.expiry_date:
                    if earliest_expiry is None or metadata.expiry_date < earliest_expiry:
                        earliest_expiry = metadata.expiry_date
            
            if doc.validation.overall_confidence:
                confidences.append(float(doc.validation.overall_confidence))
        
        # Get industry threshold
        threshold = self._get_threshold(vendor.industry)
        
        # Determine risk level
        risk_level = self._calculate_risk_level(total_emissions, threshold)
        
        # Calculate risk score (0-100, higher = riskier)
        risk_score = self._calculate_risk_score(
            total_emissions, threshold, flagged_docs, total_docs, earliest_expiry
        )
        
        # Update risk profile
        risk_profile.risk_level = risk_level
        risk_profile.risk_score = Decimal(str(risk_score))
        risk_profile.total_documents = total_docs
        risk_profile.validated_documents = validated_docs
        risk_profile.flagged_documents = flagged_docs
        risk_profile.total_co2_emissions = total_emissions
        risk_profile.exceeds_threshold = total_emissions > threshold.high_threshold
        risk_profile.avg_document_confidence = Decimal(str(sum(confidences) / len(confidences))) if confidences else None
        risk_profile.save()
        
        # Sync vendor risk level
        vendor.risk_level = risk_level
        vendor.save()
        
        return risk_profile
    
    def _get_threshold(self, industry):
        """Get or create threshold for industry"""
        try:
            return IndustryEmissionThreshold.objects.get(industry=industry)
        except IndustryEmissionThreshold.DoesNotExist:
            # Create default threshold
            defaults = DEFAULT_THRESHOLDS.get(
                industry.name,
                DEFAULT_THRESHOLDS['default']
            )
            
            return IndustryEmissionThreshold.objects.create(
                industry=industry,
                low_threshold=Decimal(str(defaults['low'])),
                medium_threshold=Decimal(str(defaults['medium'])),
                high_threshold=Decimal(str(defaults['high'])),
                critical_threshold=Decimal(str(defaults['critical']))
            )
    
    def _calculate_risk_level(self, emissions, threshold):
        """Determine risk level based on emissions"""
        if emissions <= threshold.low_threshold:
            return 'low'
        elif emissions <= threshold.medium_threshold:
            return 'medium'
        elif emissions <= threshold.high_threshold:
            return 'high'
        else:
            return 'critical'
    
    def _calculate_risk_score(self, emissions, threshold, flagged, total, expiry_date):
        """Calculate numerical risk score 0-100"""
        score = 0
        
        # Emission-based score (0-50)
        if emissions > threshold.critical_threshold:
            score += 50
        elif emissions > threshold.high_threshold:
            score += 40
        elif emissions > threshold.medium_threshold:
            score += 25
        else:
            score += 10
        
        # Flagged documents (0-25)
        if total > 0:
            flag_ratio = flagged / total
            score += flag_ratio * 25
        
        # Expiry risk (0-25)
        if expiry_date:
            today = date.today()
            if expiry_date < today:
                score += 25  # Already expired
            elif expiry_date < today + timedelta(days=30):
                score += 15  # Expiring soon
        
        return min(score, 100)