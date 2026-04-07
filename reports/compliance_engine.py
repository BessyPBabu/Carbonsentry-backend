import logging
from datetime import date
from django.db.models import Avg

logger = logging.getLogger(__name__)

# ── Regulatory thresholds ─────────────────────────────────────────────────────
_EU_ETS_PRICE_EUR   = 65.0      # EUR per tonne CO2e (approximate ETS spot)
_INR_PER_EUR        = 90.0
_NGT_MAX_PENALTY    = 1_900_000  # INR 1.9 Crore

# Voluntary carbon credit market price range (USD / tonne CO2e)
_VCM_PRICE_LOW_USD  = 5.0
_VCM_PRICE_HIGH_USD = 50.0
_INR_PER_USD        = 83.0

# ── Industry sets ─────────────────────────────────────────────────────────────
_CBAM_SECTORS = {
    'steel', 'iron', 'aluminium', 'aluminum', 'cement',
    'fertilizer', 'fertilizers', 'electricity', 'hydrogen',
}
_SEBI_SECTORS = {
    'manufacturing', 'energy', 'logistics', 'steel', 'cement',
    'chemicals', 'construction', 'mining', 'textiles', 'auto',
    'automotive', 'technology', 'it services', 'fmcg', 'retail',
    'healthcare', 'pharmaceuticals',
}

# ── Emission thresholds by industry (tonnes CO2e/year) ───────────────────────
_THRESHOLDS = {
    'Manufacturing':   {'low': 1000,  'medium': 5000,  'high': 15000, 'critical': 50000},
    'Technology':      {'low': 300,   'medium': 1500,  'high': 5000,  'critical': 12000},
    'Retail':          {'low': 300,   'medium': 1500,  'high': 3000,  'critical': 8000},
    'Logistics':       {'low': 2000,  'medium': 10000, 'high': 30000, 'critical': 100000},
    'Energy':          {'low': 5000,  'medium': 20000, 'high': 80000, 'critical': 250000},
    'Healthcare':      {'low': 400,   'medium': 2000,  'high': 7000,  'critical': 20000},
    'default':         {'low': 1000,  'medium': 5000,  'high': 10000, 'critical': 50000},
}

# ── CO2 reduction strategies (used when emissions high) ──────────────────────
_REDUCTION_STRATEGIES = [
    {
        'strategy':    'Switch to renewable energy',
        'description': 'Source electricity from solar/wind PPAs or green tariffs.',
        'typical_reduction_pct': 40,
        'timeframe':   '6–18 months',
        'cost_level':  'medium',
    },
    {
        'strategy':    'Energy efficiency audit',
        'description': 'ISO 50001 audit + retrofit lighting, HVAC, motors.',
        'typical_reduction_pct': 15,
        'timeframe':   '3–6 months',
        'cost_level':  'low',
    },
    {
        'strategy':    'Supply chain electrification',
        'description': 'Replace diesel fleet with EVs; switch to electric furnaces.',
        'typical_reduction_pct': 25,
        'timeframe':   '12–36 months',
        'cost_level':  'high',
    },
    {
        'strategy':    'Process optimisation',
        'description': 'Lean manufacturing, waste heat recovery, digitalisation.',
        'typical_reduction_pct': 10,
        'timeframe':   '3–12 months',
        'cost_level':  'low',
    },
    {
        'strategy':    'Certified carbon offsets (short-term bridge)',
        'description': 'Purchase Verified Carbon Units (VCUs) or Gold Standard credits '
                       'to offset residual emissions while reduction measures roll out.',
        'typical_reduction_pct': 100,  # of residual, not total
        'timeframe':   'Immediate',
        'cost_level':  'variable',
    },
    {
        'strategy':    'Internal carbon price',
        'description': 'Set shadow price of USD 25–50/tonne internally to drive '
                       'low-carbon investment decisions.',
        'typical_reduction_pct': 0,
        'timeframe':   'Policy change',
        'cost_level':  'none',
    },
]


def _industry_key(industry_name: str) -> str:
    return (industry_name or '').lower().strip()


def _get_threshold(industry_name: str) -> dict:
    for key, val in _THRESHOLDS.items():
        if key.lower() in _industry_key(industry_name):
            return val
    return _THRESHOLDS['default']


def _reasonable_assurance(confidence: float) -> bool:
    return confidence >= 75.0


def _emission_band(co2: float, industry: str) -> str:
    t = _get_threshold(industry)
    if co2 <= 0:
        return 'unknown'
    if co2 < t['low']:
        return 'low'
    if co2 < t['medium']:
        return 'medium'
    if co2 < t['high']:
        return 'high'
    return 'critical'


def generate_vendor_compliance_report(vendor, organization) -> dict:
    """
    Main entry point.
    Returns a dict that is stored in Report.data and rendered by the frontend.
    """
    from vendors.models import Document
    from ai_validation.models import VendorRiskProfile, DocumentValidation

    industry_name = vendor.industry.name if vendor.industry else ''
    industry_key  = _industry_key(industry_name)
    country       = (vendor.country or '').lower()
    is_indian     = any(k in country for k in ('india', 'in'))

    # ── 1. Regulatory applicability ──────────────────────────────────────────
    regulations = _build_regulations(industry_key, is_indian)

    # ── 2. Document verification ──────────────────────────────────────────────
    docs       = Document.objects.filter(vendor=vendor).select_related('document_type')
    doc_counts = {
        'total':   docs.count(),
        'valid':   docs.filter(status='valid').count(),
        'flagged': docs.filter(status='flagged').count(),
        'invalid': docs.filter(status='invalid').count(),
        'pending': docs.filter(status='pending').count(),
        'expired': docs.filter(status='expired').count(),
    }

    validations = DocumentValidation.objects.filter(
        document__vendor=vendor
    ).select_related('document', 'document__document_type', 'metadata')

    avg_conf_raw = validations.aggregate(avg=Avg('overall_confidence'))['avg'] or 0
    avg_confidence = float(avg_conf_raw)
    ra_met         = _reasonable_assurance(avg_confidence)

    doc_details = []
    for v in validations.all():
        meta = getattr(v, 'metadata', None)
        doc_details.append({
            'document_type':         v.document.document_type.name,
            'document_status':       v.document.status,
            'validation_status':     v.status,
            'confidence':            float(v.overall_confidence) if v.overall_confidence else None,
            'assurance_met':         _reasonable_assurance(float(v.overall_confidence or 0)),
            'co2_extracted':         float(meta.co2_value) if meta and meta.co2_value else None,
            'co2_unit':              meta.co2_unit if meta else None,
            'issuing_authority':     meta.issuing_authority if meta else None,
            'cert_number':           meta.certificate_number if meta else None,
            'verification_standard': meta.verification_standard if meta else None,
            'issue_date':            str(meta.issue_date) if meta and meta.issue_date else None,
            'expiry_date':           str(meta.expiry_date) if meta and meta.expiry_date else None,
            'flagged_reasons':       v.flagged_reason.split(';') if v.flagged_reason else [],
        })

    # ── 3. Emissions ──────────────────────────────────────────────────────────
    try:
        rp = VendorRiskProfile.objects.get(vendor=vendor)
        total_co2     = float(rp.total_co2_emissions or 0)
        risk_score    = float(rp.risk_score or 0)
        risk_level    = rp.risk_level
        exceeds       = rp.exceeds_threshold
    except VendorRiskProfile.DoesNotExist:
        total_co2  = 0.0
        risk_score = 0.0
        risk_level = 'unknown'
        exceeds    = False

    # Collect all extracted CO2 values per doc for scope breakdown
    extracted_values = [
        d['co2_extracted'] for d in doc_details
        if d['co2_extracted'] is not None
    ]
    extracted_total = sum(extracted_values) if extracted_values else 0
    emission_band   = _emission_band(extracted_total or total_co2, industry_name)

    # ── 4. Regulatory risk exposure ───────────────────────────────────────────
    exposure = _build_exposure(
        industry_key, is_indian, total_co2, extracted_total,
        avg_confidence, ra_met, doc_counts,
    )

    # ── 5. Compliance gaps ────────────────────────────────────────────────────
    gaps = _build_gaps(doc_counts, avg_confidence, ra_met, total_co2, exceeds)

    # ── 6. CO2 reduction roadmap ──────────────────────────────────────────────
    reduction_roadmap = None
    carbon_credit_guidance = None
    if emission_band in ('high', 'critical') or exceeds:
        reduction_roadmap      = _build_reduction_roadmap(total_co2 or extracted_total, industry_name)
        carbon_credit_guidance = _build_carbon_credit_guidance(total_co2 or extracted_total)

    # ── 7. Vendor retention recommendation ───────────────────────────────────
    recommendation = _build_retention_recommendation(
        risk_level, avg_confidence, ra_met, doc_counts, emission_band, exceeds
    )

    # ── 8. Action checklist ───────────────────────────────────────────────────
    actions = _build_action_checklist(doc_counts, avg_confidence, ra_met, emission_band, exceeds)

    return {
        'generated_at': str(date.today()),
        'vendor': {
            'id':                str(vendor.id),
            'name':              vendor.name,
            'industry':          industry_name,
            'country':           vendor.country,
            'compliance_status': vendor.compliance_status,
            'risk_level':        risk_level,
        },
        'regulatory_applicability':   regulations,
        'emission_verification': {
            **doc_counts,
            'average_ai_confidence':    round(avg_confidence, 1),
            'reasonable_assurance_met': ra_met,
            'assurance_threshold':      75.0,
            'document_details':         doc_details,
        },
        'scope_emissions': {
            'total_co2_tonnes':    round(total_co2 or extracted_total, 2),
            'extracted_from_docs': round(extracted_total, 2),
            'unit':                'tonnes CO2e',
            'emission_band':       emission_band,
            'risk_score':          round(risk_score, 1),
            'risk_level':          risk_level,
            'exceeds_threshold':   exceeds,
            'industry_thresholds': _get_threshold(industry_name),
        },
        'regulatory_risk_exposure':  exposure,
        'compliance_gap_analysis':   gaps,
        'reduction_roadmap':         reduction_roadmap,
        'carbon_credit_guidance':    carbon_credit_guidance,
        'vendor_retention': {
            'recommendation':       recommendation['decision'],
            'reason':               recommendation['reason'],
            'confidence_level':     recommendation['confidence'],
            'review_date':          recommendation['review_date'],
        },
        'action_checklist': actions,
    }


# ── Private helpers ───────────────────────────────────────────────────────────

def _build_regulations(industry_key: str, is_indian: bool) -> list:
    regs = []
    if industry_key in _SEBI_SECTORS:
        regs.append({
            'regulation':    'SEBI BRSR Core',
            'applies':       True,
            'requirement':   'Reasonable assurance on Scope 1, 2 & 3 GHG emissions (FY 2024-25 onwards)',
            'assurance_bar': '≥ 75% AI confidence or third-party limited assurance report',
            'deadline':      'Annual — FY 2024-25',
            'consequence':   'Regulatory finding, stock exchange disclosure requirement',
        })
    if industry_key in _CBAM_SECTORS:
        regs.append({
            'regulation':    'EU CBAM (Carbon Border Adjustment Mechanism)',
            'applies':       True,
            'requirement':   'Report embedded carbon per tonne of goods exported to EU',
            'assurance_bar': 'Verified emission data with declared CO2e per unit',
            'deadline':      'Transitional: 2023-2026 | Full enforcement: 2026',
            'consequence':   f'Carbon tariff payable at EU ETS price (≈ EUR {_EU_ETS_PRICE_EUR}/tonne)',
        })
    if is_indian:
        regs.append({
            'regulation':    'NGT (National Green Tribunal) — India',
            'applies':       True,
            'requirement':   'Emission monitoring and documented third-party verification',
            'assurance_bar': 'Valid third-party verified certificate on record',
            'deadline':      'Active enforcement',
            'consequence':   f'Penalty up to INR {_NGT_MAX_PENALTY:,} (₹1.9 Crore)',
        })
    if not regs:
        regs.append({
            'regulation':    'GHG Protocol Corporate Standard (Voluntary)',
            'applies':       True,
            'requirement':   'Voluntary Scope 1, 2, 3 disclosure',
            'assurance_bar': 'Internal or external assurance',
            'deadline':      'N/A',
            'consequence':   'No mandatory penalty — reputational risk only',
        })
    return regs


def _build_exposure(
    industry_key, is_indian, total_co2, extracted_total, avg_confidence, ra_met, doc_counts
) -> list:
    exposure = []
    co2_for_calc = total_co2 or extracted_total

    if industry_key in _SEBI_SECTORS:
        exposure.append({
            'regulation': 'SEBI BRSR Core',
            'status':     'compliant' if ra_met else 'non_compliant',
            'detail':     (
                'Average AI confidence meets 75% reasonable assurance threshold'
                if ra_met else
                f'Average AI confidence {avg_confidence:.1f}% is below the 75% threshold'
            ),
            'financial_exposure': None,
            'risk_rating': 'low' if ra_met else 'high',
        })

    if industry_key in _CBAM_SECTORS and co2_for_calc > 0:
        cbam_eur = round(co2_for_calc * _EU_ETS_PRICE_EUR, 2)
        cbam_inr = round(cbam_eur * _INR_PER_EUR, 2)
        exposure.append({
            'regulation': 'EU CBAM',
            'status':     'exposure_calculated',
            'detail':     f'{co2_for_calc:.2f} tCO2e × EUR {_EU_ETS_PRICE_EUR} ETS price',
            'financial_exposure': {
                'eur': cbam_eur,
                'inr': cbam_inr,
                'basis': 'EU ETS carbon price estimate',
            },
            'risk_rating': 'high' if cbam_eur > 50000 else 'medium',
        })

    if is_indian:
        has_failure = doc_counts['invalid'] > 0 or (
            doc_counts['total'] > 0 and doc_counts['valid'] == 0
        )
        exposure.append({
            'regulation': 'NGT India',
            'status':     'at_risk' if has_failure else 'compliant',
            'detail':     (
                'One or more documents failed verification — NGT compliance at risk'
                if has_failure else
                'Documents verified — NGT monitoring requirements met'
            ),
            'financial_exposure': {
                'inr': _NGT_MAX_PENALTY if has_failure else 0,
                'display': f'Up to ₹1.9 Crore' if has_failure else '₹0',
            } if has_failure else None,
            'risk_rating': 'high' if has_failure else 'low',
        })

    return exposure


def _build_gaps(doc_counts, avg_confidence, ra_met, total_co2, exceeds) -> list:
    gaps = []
    if doc_counts['pending'] > 0:
        gaps.append({
            'gap':      'Pending submissions',
            'detail':   f"{doc_counts['pending']} document(s) not yet submitted by vendor",
            'severity': 'high',
        })
    if doc_counts['invalid'] > 0:
        gaps.append({
            'gap':      'Failed AI validation',
            'detail':   f"{doc_counts['invalid']} document(s) rejected by AI — require resubmission",
            'severity': 'high',
        })
    if doc_counts['expired'] > 0:
        gaps.append({
            'gap':      'Expired certificates',
            'detail':   f"{doc_counts['expired']} certificate(s) expired — renewal required",
            'severity': 'high',
        })
    if doc_counts['flagged'] > 0:
        gaps.append({
            'gap':      'Flagged for review',
            'detail':   f"{doc_counts['flagged']} document(s) flagged — pending human review",
            'severity': 'medium',
        })
    if not ra_met and avg_confidence > 0:
        gaps.append({
            'gap':      'Reasonable assurance not met',
            'detail':   f'Average AI confidence {avg_confidence:.1f}% < 75% threshold',
            'severity': 'high',
        })
    if total_co2 == 0:
        gaps.append({
            'gap':      'No CO2 data extracted',
            'detail':   'Emission values could not be extracted from documents',
            'severity': 'medium',
        })
    if exceeds:
        gaps.append({
            'gap':      'Emissions exceed threshold',
            'detail':   'Vendor total emissions are above the industry compliance threshold',
            'severity': 'critical',
        })
    return gaps


def _build_reduction_roadmap(total_co2: float, industry: str) -> dict:
    t              = _get_threshold(industry)
    target_co2     = t['low'] * 0.75  # aim for 75% of the low threshold
    reduction_needed = max(0, total_co2 - target_co2)
    reduction_pct  = round((reduction_needed / total_co2) * 100, 1) if total_co2 > 0 else 0

    return {
        'current_emissions_tco2e': round(total_co2, 2),
        'target_emissions_tco2e':  round(target_co2, 2),
        'reduction_needed_tco2e':  round(reduction_needed, 2),
        'reduction_needed_pct':    reduction_pct,
        'strategies':              _REDUCTION_STRATEGIES,
        'note': (
            'Strategies are ordered from quickest wins to longer-term structural changes. '
            'Carbon offsets provide an immediate bridge while reductions are implemented.'
        ),
    }


def _build_carbon_credit_guidance(total_co2: float) -> dict:
    low_cost_inr  = round(total_co2 * _VCM_PRICE_LOW_USD  * _INR_PER_USD, 0)
    high_cost_inr = round(total_co2 * _VCM_PRICE_HIGH_USD * _INR_PER_USD, 0)
    low_cost_usd  = round(total_co2 * _VCM_PRICE_LOW_USD, 0)
    high_cost_usd = round(total_co2 * _VCM_PRICE_HIGH_USD, 0)

    return {
        'credits_needed_tco2e':     round(total_co2, 2),
        'estimated_cost_usd_low':   low_cost_usd,
        'estimated_cost_usd_high':  high_cost_usd,
        'estimated_cost_inr_low':   low_cost_inr,
        'estimated_cost_inr_high':  high_cost_inr,
        'credit_types': [
            {
                'type':        'Verified Carbon Unit (VCU) — Verra Registry',
                'standard':    'Verified Carbon Standard (VCS)',
                'price_range': 'USD 5–15 per tonne',
                'best_for':    'Cost-effective offsetting, wide project variety',
                'how_to_buy':  'Via Xpansiv CBL, ACX, or direct project developers',
            },
            {
                'type':        'Gold Standard Credit',
                'standard':    'Gold Standard Foundation',
                'price_range': 'USD 15–50 per tonne',
                'best_for':    'Premium quality, SDG co-benefits, ESG reporting',
                'how_to_buy':  'Via Gold Standard marketplace or certified brokers',
            },
            {
                'type':        'CBAM Compliance Credit (EU ETS)',
                'standard':    'EU Emissions Trading System',
                'price_range': f'EUR {_EU_ETS_PRICE_EUR} per tonne (approximate)',
                'best_for':    'EU regulatory compliance for CBAM-covered goods',
                'how_to_buy':  'Via EU ETS auctions or secondary market brokers',
            },
            {
                'type':        'REC (Renewable Energy Certificate)',
                'standard':    'I-REC / RE100',
                'price_range': 'USD 1–5 per MWh',
                'best_for':    'Offsetting Scope 2 (electricity) emissions only',
                'how_to_buy':  'Via I-REC Standard marketplace',
            },
        ],
        'steps': [
            'Step 1: Calculate verified emission baseline from validated documents',
            'Step 2: Set internal reduction targets (science-based if possible)',
            'Step 3: Implement reduction measures — energy efficiency, renewables',
            'Step 4: Calculate residual emissions after reductions',
            'Step 5: Purchase credits equal to residual emissions',
            'Step 6: Retire credits on the registry (never trade retired credits)',
            'Step 7: Obtain retirement certificate for SEBI BRSR / regulatory disclosure',
            'Step 8: Report in annual sustainability report with credit serial numbers',
        ],
        'important_note': (
            'Carbon credits are a bridge measure, not a substitute for emission reductions. '
            'SEBI BRSR and EU CBAM require actual emission data — credits supplement but '
            'do not replace the verified emission reporting requirement.'
        ),
    }


def _build_retention_recommendation(
    risk_level, avg_confidence, ra_met, doc_counts, emission_band, exceeds
) -> dict:
    score = 0

    # Score factors
    if risk_level == 'low':        score += 30
    elif risk_level == 'medium':   score += 20
    elif risk_level == 'high':     score += 5
    elif risk_level == 'critical': score -= 10

    if ra_met:                     score += 25
    elif avg_confidence >= 60:     score += 10

    if doc_counts['invalid'] == 0 and doc_counts['expired'] == 0:  score += 20
    if doc_counts['pending'] == 0:                                   score += 15

    if emission_band == 'low':     score += 10
    elif emission_band == 'medium': score += 5
    elif emission_band == 'high':   score -= 5
    elif emission_band == 'critical': score -= 15

    if exceeds:                    score -= 10

    score = max(0, min(100, score))

    if score >= 70:
        decision   = 'retain'
        reason     = 'Vendor demonstrates good compliance posture with acceptable emission levels and valid documents.'
        confidence = 'high'
        review     = '12 months'
    elif score >= 40:
        decision   = 'monitor'
        reason     = 'Vendor has compliance gaps or elevated emissions. Retain with conditions and enhanced monitoring.'
        confidence = 'medium'
        review     = '3 months'
    else:
        decision   = 'review_for_replacement'
        reason     = (
            'Vendor has critical compliance failures, very high emissions, or persistent '
            'document validation issues. Consider alternative suppliers.'
        )
        confidence = 'high'
        review     = 'Immediate'

    return {
        'decision':       decision,
        'score':          score,
        'reason':         reason,
        'confidence':     confidence,
        'review_date':    review,
    }


def _build_action_checklist(doc_counts, avg_confidence, ra_met, emission_band, exceeds) -> list:
    actions = []
    priority = 1

    if doc_counts['pending'] > 0:
        actions.append({
            'priority': priority,
            'action':   f"Send upload link to vendor for {doc_counts['pending']} pending document(s)",
            'owner':    'Compliance Officer',
            'urgency':  'high',
        })
        priority += 1

    if doc_counts['invalid'] > 0:
        actions.append({
            'priority': priority,
            'action':   f"Request resubmission of {doc_counts['invalid']} invalid document(s) with correct format",
            'owner':    'Compliance Officer',
            'urgency':  'high',
        })
        priority += 1

    if doc_counts['expired'] > 0:
        actions.append({
            'priority': priority,
            'action':   f"Request certificate renewal for {doc_counts['expired']} expired document(s)",
            'owner':    'Compliance Officer',
            'urgency':  'high',
        })
        priority += 1

    if not ra_met:
        actions.append({
            'priority': priority,
            'action':   'Request higher-quality scans or certified originals to improve AI extraction confidence',
            'owner':    'Compliance Officer',
            'urgency':  'medium',
        })
        priority += 1

    if emission_band in ('high', 'critical') or exceeds:
        actions.append({
            'priority': priority,
            'action':   'Request emission reduction plan from vendor within 30 days',
            'owner':    'Compliance Manager',
            'urgency':  'high',
        })
        priority += 1
        actions.append({
            'priority': priority,
            'action':   'Evaluate carbon credit purchase to offset excess emissions in the interim',
            'owner':    'Sustainability Team',
            'urgency':  'medium',
        })
        priority += 1

    if emission_band == 'critical':
        actions.append({
            'priority': priority,
            'action':   'Escalate to senior management — consider supply chain substitution',
            'owner':    'Senior Management',
            'urgency':  'critical',
        })
        priority += 1

    if not actions:
        actions.append({
            'priority': 1,
            'action':   'Continue standard quarterly monitoring — vendor is in good standing',
            'owner':    'Compliance Officer',
            'urgency':  'low',
        })

    return actions