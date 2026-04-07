import io
import logging
from decimal import Decimal
from django.db.models import Avg, Count, Q, Sum

logger = logging.getLogger(__name__)

# ── EU CBAM applicable industries ────────────────────────────────────────────
_CBAM_INDUSTRIES = {'steel', 'iron', 'aluminium', 'aluminum', 'cement', 'fertilizer',
                    'fertilizers', 'electricity', 'hydrogen', 'energy'}

# ── SEBI BRSR Core: applies to Scope 3 supply chain of top-1000 listed companies
_SEBI_INDUSTRIES = {'manufacturing', 'energy', 'logistics', 'steel', 'cement',
                    'chemicals', 'construction', 'mining', 'textiles', 'auto',
                    'automotive', 'it services', 'technology', 'fmcg', 'retail',
                    'healthcare', 'pharmaceuticals'}

# ── NGT penalty base (India operations) ──────────────────────────────────────
_NGT_BASE_PENALTY_INR = 1_900_000  # ₹1.9 Crore maximum per NGT ruling

# ── EU ETS carbon price estimate (EUR per tonne CO2) ─────────────────────────
_EU_ETS_PRICE_EUR = 65.0

# ── INR/EUR rough rate ────────────────────────────────────────────────────────
_INR_PER_EUR = 90.0


def _sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(i) for i in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def _cbam_applicable(industry_name: str) -> bool:
    return industry_name.lower().strip() in _CBAM_INDUSTRIES


def _sebi_applicable(industry_name: str) -> bool:
    return industry_name.lower().strip() in _SEBI_INDUSTRIES


def _reasonable_assurance_met(confidence: float) -> bool:
    # Maps to SEBI BRSR "reasonable assurance" — we treat >= 75% AI confidence
    # as equivalent to third-party limited assurance on the document.
    return confidence >= 75.0


class ReportGenerator:

    def generate(self, report_type, organization, vendor=None, date_from=None, date_to=None):
        logger.info(
            "report.generate | type=%s org=%s vendor=%s",
            report_type, organization.id, vendor.id if vendor else None,
        )

        generators = {
            'vendor_risk':              self._generate_vendor_risk,
            'compliance_summary':       self._generate_compliance_summary,
            'emissions_overview':       self._generate_emissions_overview,
            'document_audit':           self._generate_document_audit,
            'vendor_compliance_report': self._generate_vendor_compliance_report,
        }

        fn = generators.get(report_type)
        if not fn:
            raise ValueError(f"Unknown report type: {report_type}")

        try:
            data = fn(
                organization=organization,
                vendor=vendor,
                date_from=date_from,
                date_to=date_to,
            )
            return _sanitize_for_json(data)
        except Exception as exc:
            logger.exception("report.generate_failed | type=%s org=%s", report_type, organization.id)
            raise

    # ── Vendor Compliance Report (new — SEBI BRSR + EU CBAM + NGT) ───────────

    def _generate_vendor_compliance_report(self, organization, vendor, date_from=None, date_to=None):
        if not vendor:
            raise ValueError("vendor is required for vendor_compliance_report")

        from vendors.models import Document
        from ai_validation.models import VendorRiskProfile, DocumentValidation, ExtractedMetadata

        industry_name = vendor.industry.name if vendor.industry else ''
        country       = (vendor.country or '').lower()

        # ── Regulatory applicability ──────────────────────────────────────────
        is_indian   = any(k in country for k in ('india', 'in'))
        cbam_applies = _cbam_applicable(industry_name)
        sebi_applies = _sebi_applicable(industry_name)
        ngt_applies  = is_indian

        applicable_regulations = []
        if sebi_applies:
            applicable_regulations.append({
                'regulation': 'SEBI BRSR Core',
                'requirement': 'Reasonable assurance on Scope 1, 2 & 3 emissions',
                'deadline': 'FY 2024-25 onwards',
                'applies_because': f'{industry_name} is a Scope 3 supply-chain category',
            })
        if cbam_applies:
            applicable_regulations.append({
                'regulation': 'EU CBAM',
                'requirement': 'Embedded carbon declaration for EU-bound goods',
                'deadline': 'Phased from 2026',
                'applies_because': f'{industry_name} is a CBAM-covered sector',
            })
        if ngt_applies:
            applicable_regulations.append({
                'regulation': 'NGT (India)',
                'requirement': 'Emission monitoring with documented verification',
                'deadline': 'Active enforcement',
                'applies_because': 'Vendor operates in India',
            })
        if not applicable_regulations:
            applicable_regulations.append({
                'regulation': 'General voluntary disclosure',
                'requirement': 'GHG Protocol Corporate Standard',
                'deadline': 'N/A',
                'applies_because': 'No mandatory regulation identified for this sector/country',
            })

        # ── Document verification status ──────────────────────────────────────
        docs = Document.objects.filter(vendor=vendor).select_related('document_type')
        validations = DocumentValidation.objects.filter(
            document__vendor=vendor
        ).select_related('metadata')

        total_docs    = docs.count()
        valid_docs    = docs.filter(status='valid').count()
        flagged_docs  = docs.filter(status='flagged').count()
        invalid_docs  = docs.filter(status='invalid').count()
        pending_docs  = docs.filter(status='pending').count()
        expired_docs  = docs.filter(status='expired').count()

        avg_conf_agg = validations.aggregate(avg=Avg('overall_confidence'))
        avg_confidence = float(avg_conf_agg['avg'] or 0)

        reasonable_assurance_met = _reasonable_assurance_met(avg_confidence)

        doc_status_list = []
        for doc in docs.prefetch_related('validation', 'validation__metadata'):
            v = getattr(doc, 'validation', None)
            confidence = float(v.overall_confidence) if v and v.overall_confidence else None
            meta = getattr(v, 'metadata', None) if v else None
            doc_status_list.append({
                'document_type':  doc.document_type.name,
                'status':         doc.status,
                'confidence':     confidence,
                'expiry_date':    str(doc.expiry_date) if doc.expiry_date else None,
                'co2_extracted':  float(meta.co2_value) if meta and meta.co2_value else None,
                'co2_unit':       meta.co2_unit if meta else None,
                'issuing_authority': meta.issuing_authority if meta else None,
                'cert_number':    meta.certificate_number if meta else None,
                'verification_standard': meta.verification_standard if meta else None,
                'assurance_met':  _reasonable_assurance_met(confidence) if confidence else False,
            })

        # ── Scope emissions breakdown ─────────────────────────────────────────
        try:
            risk_profile = VendorRiskProfile.objects.get(vendor=vendor)
            total_co2    = float(risk_profile.total_co2_emissions or 0)
            risk_level   = risk_profile.risk_level
            risk_score   = float(risk_profile.risk_score or 0)
            exceeds_threshold = risk_profile.exceeds_threshold
        except VendorRiskProfile.DoesNotExist:
            total_co2   = 0.0
            risk_level  = 'unknown'
            risk_score  = 0.0
            exceeds_threshold = False

        # ── Regulatory risk exposure ──────────────────────────────────────────
        regulatory_exposure = []

        if sebi_applies:
            assurance_gap = not reasonable_assurance_met
            regulatory_exposure.append({
                'regulation': 'SEBI BRSR Core',
                'status': 'non_compliant' if assurance_gap else 'compliant',
                'gap': 'AI confidence below 75% — reasonable assurance not met' if assurance_gap else None,
                'risk': 'Regulatory audit finding / stock exchange disclosure risk' if assurance_gap else None,
            })

        if cbam_applies and total_co2 > 0:
            cbam_cost_eur = total_co2 * _EU_ETS_PRICE_EUR
            cbam_cost_inr = cbam_cost_eur * _INR_PER_EUR
            regulatory_exposure.append({
                'regulation': 'EU CBAM',
                'status': 'exposure_calculated',
                'estimated_carbon_cost_eur': round(cbam_cost_eur, 2),
                'estimated_carbon_cost_inr': round(cbam_cost_inr, 2),
                'basis': f'{total_co2:.2f} tCO2e × EUR {_EU_ETS_PRICE_EUR} ETS price',
                'risk': 'Carbon tariff payable on EU exports' if cbam_applies else None,
            })

        if ngt_applies:
            has_monitoring_failure = invalid_docs > 0 or (total_docs > 0 and valid_docs == 0)
            regulatory_exposure.append({
                'regulation': 'NGT (India)',
                'status': 'at_risk' if has_monitoring_failure else 'compliant',
                'max_penalty_inr': _NGT_BASE_PENALTY_INR if has_monitoring_failure else 0,
                'max_penalty_display': '₹1.9 Crore' if has_monitoring_failure else '₹0',
                'risk': 'Penalty for emission monitoring failure' if has_monitoring_failure else None,
            })

        # ── Compliance gap analysis ───────────────────────────────────────────
        gaps = []
        if pending_docs > 0:
            gaps.append(f'{pending_docs} document(s) not yet submitted by vendor')
        if invalid_docs > 0:
            gaps.append(f'{invalid_docs} document(s) failed AI validation and rejected')
        if expired_docs > 0:
            gaps.append(f'{expired_docs} certificate(s) expired — renewal required')
        if flagged_docs > 0:
            gaps.append(f'{flagged_docs} document(s) flagged and pending human review')
        if not reasonable_assurance_met and avg_confidence > 0:
            gaps.append(f'Average AI confidence {avg_confidence:.1f}% is below the 75% reasonable assurance threshold')
        if total_co2 == 0:
            gaps.append('No CO2 emission data extracted — extraction may have failed or documents lack numeric emission values')
        if exceeds_threshold:
            gaps.append('Total emissions exceed industry threshold — vendor is classified as high/critical risk')

        # ── Recommendations ───────────────────────────────────────────────────
        recommendations = []
        if pending_docs > 0:
            recommendations.append('Request immediate document submission via resend link')
        if invalid_docs > 0:
            recommendations.append('Contact vendor to resubmit rejected documents with correct format and all required fields')
        if expired_docs > 0:
            recommendations.append('Issue renewal request for expired certificates')
        if not reasonable_assurance_met:
            recommendations.append('Request higher-quality document scans or certified originals to improve AI extraction confidence')
        if total_co2 == 0:
            recommendations.append('Request emission report with explicit total CO2e value clearly labelled in the document')
        if cbam_applies and total_co2 > 0:
            recommendations.append(f'Budget for EU CBAM carbon cost of approximately EUR {total_co2 * _EU_ETS_PRICE_EUR:,.0f} on EU exports')
        if exceeds_threshold:
            recommendations.append('Engage vendor on emission reduction plan — consider supply chain substitution for critical risk vendors')
        if not recommendations:
            recommendations.append('Maintain current monitoring schedule — vendor is in good standing')

        return {
            'vendor': {
                'id':                str(vendor.id),
                'name':              vendor.name,
                'industry':          industry_name,
                'country':           vendor.country,
                'compliance_status': vendor.compliance_status,
                'risk_level':        risk_level,
            },
            'regulatory_applicability': applicable_regulations,
            'emission_verification': {
                'total_documents':          total_docs,
                'valid_documents':          valid_docs,
                'flagged_documents':        flagged_docs,
                'invalid_documents':        invalid_docs,
                'pending_documents':        pending_docs,
                'expired_documents':        expired_docs,
                'average_ai_confidence':    round(avg_confidence, 1),
                'reasonable_assurance_met': reasonable_assurance_met,
                'assurance_threshold':      75.0,
                'document_details':         doc_status_list,
            },
            'scope_emissions': {
                'total_co2_tonnes':   total_co2,
                'unit':               'tonnes CO2e',
                'risk_score':         risk_score,
                'risk_level':         risk_level,
                'exceeds_threshold':  exceeds_threshold,
                'note': (
                    'Scope breakdown extracted from vendor documents. '
                    'Scope 1/2/3 split visible in individual document records.'
                ),
            },
            'compliance_gap_analysis': gaps,
            'regulatory_risk_exposure': regulatory_exposure,
            'recommendations': recommendations,
        }

    # ── Existing report types (unchanged logic, kept for completeness) ────────

    def _generate_vendor_risk(self, organization, vendor, date_from=None, date_to=None):
        from vendors.models import Document
        from ai_validation.models import VendorRiskProfile, DocumentValidation

        if not vendor:
            raise ValueError("vendor is required for vendor_risk report")

        try:
            risk_profile = VendorRiskProfile.objects.get(vendor=vendor)
        except VendorRiskProfile.DoesNotExist:
            risk_profile = None

        docs_qs = Document.objects.filter(vendor=vendor)
        if date_from:
            docs_qs = docs_qs.filter(uploaded_at__date__gte=date_from)
        if date_to:
            docs_qs = docs_qs.filter(uploaded_at__date__lte=date_to)

        total_docs   = docs_qs.count()
        valid_docs   = docs_qs.filter(status='valid').count()
        flagged_docs = docs_qs.filter(status='flagged').count()
        expired_docs = docs_qs.filter(status='expired').count()

        validations_qs = DocumentValidation.objects.filter(document__vendor=vendor)
        avg_confidence = float(
            validations_qs.aggregate(avg=Avg('overall_confidence'))['avg'] or 0
        )

        risk_factors = []
        if risk_profile:
            if flagged_docs > 0:
                ratio = round((flagged_docs / total_docs) * 100) if total_docs else 0
                risk_factors.append({'name': 'High flagged document ratio', 'impact': f'{ratio}% flagged'})
            if risk_profile.exceeds_threshold:
                risk_factors.append({'name': 'Emissions exceed industry threshold', 'impact': 'High'})
            if avg_confidence < 60:
                risk_factors.append({'name': 'Low AI confidence scores', 'impact': f'avg {round(avg_confidence)}%'})

        return {
            'vendor': {
                'id': str(vendor.id), 'name': vendor.name,
                'industry': vendor.industry.name if vendor.industry else None,
                'country': vendor.country, 'compliance_status': vendor.compliance_status,
            },
            'risk_summary': {
                'overall_score': float(risk_profile.risk_score) if risk_profile else 0,
                'risk_level':    risk_profile.risk_level if risk_profile else 'unknown',
                'factors':       risk_factors,
            },
            'emissions': {
                'total_co2':         float(risk_profile.total_co2_emissions) if risk_profile and risk_profile.total_co2_emissions else 0,
                'unit':              'tonnes CO2e',
                'exceeds_threshold': risk_profile.exceeds_threshold if risk_profile else False,
            },
            'documents': {
                'total': total_docs, 'valid': valid_docs,
                'flagged': flagged_docs, 'expired': expired_docs,
                'avg_confidence': round(avg_confidence, 1),
            },
            'recommendations': self._build_vendor_recommendations(
                risk_profile=risk_profile,
                flagged_docs=flagged_docs,
                expired_docs=expired_docs,
                avg_confidence=avg_confidence,
            ),
        }

    def _build_vendor_recommendations(self, risk_profile, flagged_docs, expired_docs, avg_confidence):
        actions = []
        if expired_docs > 0:
            actions.append("Request updated carbon certificates for expired documents")
        if avg_confidence < 70:
            actions.append("Review flagged documents and request higher quality scans")
        if risk_profile and risk_profile.risk_level in ('high', 'critical'):
            actions.append("Schedule immediate compliance audit")
        if risk_profile and risk_profile.exceeds_threshold:
            actions.append("Request emission reduction plan from vendor")
        if not actions:
            actions.append("Continue standard monitoring procedures")
        return actions

    def _generate_compliance_summary(self, organization, vendor=None, date_from=None, date_to=None):
        from vendors.models import Vendor
        from ai_validation.models import VendorRiskProfile

        vendors_qs = Vendor.objects.filter(organization=organization)
        total       = vendors_qs.count()
        compliant   = vendors_qs.filter(compliance_status='compliant').count()
        non_compliant = vendors_qs.filter(compliance_status='non_compliant').count()
        pending     = vendors_qs.filter(compliance_status='pending').count()
        expired_s   = vendors_qs.filter(compliance_status='expired').count()
        high_risk   = VendorRiskProfile.objects.filter(
            vendor__organization=organization, risk_level__in=['high', 'critical']
        ).count()

        vendor_rows = []
        for v in vendors_qs.select_related('industry')[:50]:
            try:
                rp = VendorRiskProfile.objects.get(vendor=v)
            except VendorRiskProfile.DoesNotExist:
                rp = None
            vendor_rows.append({
                'vendor_id': str(v.id), 'name': v.name,
                'industry': v.industry.name if v.industry else None,
                'country': v.country, 'compliance_status': v.compliance_status,
                'risk_level': rp.risk_level if rp else 'unknown',
            })

        return {
            'summary': {
                'total_vendors': total, 'compliant': compliant,
                'non_compliant': non_compliant, 'pending': pending,
                'expired': expired_s, 'high_risk': high_risk,
            },
            'vendors': vendor_rows,
        }

    def _generate_emissions_overview(self, organization, vendor=None, date_from=None, date_to=None):
        from ai_validation.models import VendorRiskProfile
        from django.db.models import Sum

        profiles_qs = VendorRiskProfile.objects.filter(
            vendor__organization=organization, total_co2_emissions__isnull=False
        ).select_related('vendor', 'vendor__industry')

        agg            = profiles_qs.aggregate(total=Sum('total_co2_emissions'))
        total_emissions = float(agg['total'] or 0)

        vendor_emissions = [
            {
                'vendor_id':   str(p.vendor.id),
                'vendor_name': p.vendor.name,
                'industry':    p.vendor.industry.name if p.vendor.industry else None,
                'total_co2':   float(p.total_co2_emissions),
                'risk_level':  p.risk_level,
                'exceeds_threshold': p.exceeds_threshold,
            }
            for p in profiles_qs.order_by('-total_co2_emissions')[:20]
        ]

        industry_breakdown = (
            profiles_qs
            .values('vendor__industry__name')
            .annotate(total=Sum('total_co2_emissions'), count=Count('id'))
            .order_by('-total')
        )

        return {
            'summary': {
                'total_vendors_with_data': profiles_qs.count(),
                'total_emissions': total_emissions,
                'unit': 'tonnes CO2e',
            },
            'vendor_emissions': vendor_emissions,
            'by_industry': [
                {'industry': r['vendor__industry__name'] or 'Unknown', 'total_co2': float(r['total']), 'vendor_count': r['count']}
                for r in industry_breakdown
            ],
        }

    def _generate_document_audit(self, organization, vendor=None, date_from=None, date_to=None):
        from ai_validation.models import DocumentValidation
        from vendors.models import Vendor

        validations_qs = DocumentValidation.objects.filter(document__vendor__organization=organization)
        if date_from:
            validations_qs = validations_qs.filter(created_at__date__gte=date_from)
        if date_to:
            validations_qs = validations_qs.filter(created_at__date__lte=date_to)

        total      = validations_qs.count()
        completed  = validations_qs.filter(status='completed').count()
        failed     = validations_qs.filter(status='failed').count()
        processing = validations_qs.filter(status='processing').count()
        flagged    = validations_qs.filter(requires_manual_review=True).count()
        auto_approved = completed - flagged if completed >= flagged else 0

        quality = validations_qs.aggregate(
            avg_overall=Avg('overall_confidence'),
            avg_readability=Avg('readability_score'),
            avg_relevance=Avg('relevance_confidence'),
            avg_authenticity=Avg('authenticity_score'),
        )

        def _f(val):
            return round(float(val), 1) if val is not None else 0

        vendor_summaries = []
        for v in Vendor.objects.filter(organization=organization)[:30]:
            v_qs = validations_qs.filter(document__vendor=v)
            if not v_qs.exists():
                continue
            agg = v_qs.aggregate(avg_conf=Avg('overall_confidence'), total=Count('id'), flagged=Count('id', filter=Q(requires_manual_review=True)))
            vendor_summaries.append({
                'vendor_id': str(v.id), 'vendor_name': v.name,
                'total_validations': agg['total'],
                'flagged': agg['flagged'],
                'avg_confidence': _f(agg['avg_conf']),
            })

        return {
            'validation_summary': {
                'total_validations': total, 'completed': completed,
                'failed': failed, 'processing': processing,
                'flagged_for_review': flagged, 'auto_approved': auto_approved,
                'auto_approval_rate': round((auto_approved / completed) * 100, 1) if completed else 0,
            },
            'quality_metrics': {
                'avg_overall_confidence':  _f(quality['avg_overall']),
                'avg_readability_score':   _f(quality['avg_readability']),
                'avg_relevance_score':     _f(quality['avg_relevance']),
                'avg_authenticity_score':  _f(quality['avg_authenticity']),
            },
            'vendor_summaries': vendor_summaries,
        }


# ── PDF Exporter (unchanged from original, + vendor_compliance_report section) ──

class PDFExporter:

    BRAND_GREEN = (26 / 255, 143 / 255, 112 / 255)
    LIGHT_GRAY  = (0.95, 0.95, 0.95)
    MID_GRAY    = (0.6, 0.6, 0.6)

    def export(self, report):
        logger.info("PDFExporter: building report=%s type=%s", report.id, report.report_type)
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate
        except ImportError:
            raise RuntimeError("reportlab is required for PDF export")

        buffer = io.BytesIO()
        doc    = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=20*mm, rightMargin=20*mm,
            topMargin=20*mm, bottomMargin=20*mm,
        )

        story  = self._build_cover(report) + self._build_body(report)
        doc.build(story, onFirstPage=self._draw_header_footer, onLaterPages=self._draw_header_footer)

        pdf_bytes = buffer.getvalue()
        buffer.close()
        logger.info("PDFExporter: done report=%s size=%d bytes", report.id, len(pdf_bytes))
        return pdf_bytes

    def _build_cover(self, report):
        from reportlab.platypus import Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_CENTER

        title_style = ParagraphStyle('CT', fontSize=24, fontName='Helvetica-Bold',
                                     textColor=self._rl_color(self.BRAND_GREEN), alignment=TA_CENTER, spaceAfter=6)
        sub_style   = ParagraphStyle('CS', fontSize=11, fontName='Helvetica',
                                     textColor=self._rl_color(self.MID_GRAY), alignment=TA_CENTER, spaceAfter=4)

        generated_str = report.generated_at.strftime("%d %b %Y, %H:%M") if report.generated_at else '—'
        generated_by  = (report.generated_by.full_name() or report.generated_by.email) if report.generated_by else '—'

        elements = [
            Spacer(1, 30*mm),
            Paragraph(report.title, title_style),
            Spacer(1, 4*mm),
            Paragraph(self._report_type_label(report.report_type), sub_style),
            Paragraph(f"Generated by {generated_by} on {generated_str}", sub_style),
            Spacer(1, 6*mm),
            HRFlowable(width='100%', thickness=1, color=self._rl_color(self.BRAND_GREEN)),
            Spacer(1, 10*mm),
        ]

        if report.status == 'approved' and report.approved_by:
            approved_str = report.approved_at.strftime("%d %b %Y") if report.approved_at else '—'
            approver     = report.approved_by.full_name() or report.approved_by.email
            ap_style     = ParagraphStyle('Ap', fontSize=10, fontName='Helvetica',
                                          textColor=self._rl_color((0.1, 0.6, 0.3)), alignment=TA_CENTER)
            elements.append(Paragraph(f"Approved by {approver} on {approved_str}", ap_style))
            elements.append(Spacer(1, 6*mm))

        return elements

    def _build_body(self, report):
        builders = {
            'vendor_risk':              self._build_vendor_risk_body,
            'compliance_summary':       self._build_compliance_summary_body,
            'emissions_overview':       self._build_emissions_overview_body,
            'document_audit':           self._build_document_audit_body,
            'vendor_compliance_report': self._build_vendor_compliance_body,
        }
        builder = builders.get(report.report_type)
        if not builder:
            return []
        return builder(report.data)

    def _build_vendor_compliance_body(self, data):
        elements = []
        vendor = data.get('vendor', {})

        elements += self._section_heading("Vendor Overview")
        elements += self._kv_table([
            ("Vendor",             vendor.get('name', '—')),
            ("Industry",           vendor.get('industry', '—')),
            ("Country",            vendor.get('country', '—')),
            ("Compliance Status",  vendor.get('compliance_status', '—').upper()),
            ("Risk Level",         vendor.get('risk_level', '—').upper()),
        ])

        regs = data.get('regulatory_applicability', [])
        if regs:
            elements += self._section_heading("Regulatory Applicability")
            headers = ["Regulation", "Requirement", "Deadline"]
            rows    = [[r['regulation'], r['requirement'], r['deadline']] for r in regs]
            elements += self._data_table(headers, rows)

        ev = data.get('emission_verification', {})
        elements += self._section_heading("Document Verification Status")
        elements += self._kv_table([
            ("Total Documents",         str(ev.get('total_documents', 0))),
            ("Valid",                   str(ev.get('valid_documents', 0))),
            ("Flagged for Review",      str(ev.get('flagged_documents', 0))),
            ("Invalid",                 str(ev.get('invalid_documents', 0))),
            ("Expired",                 str(ev.get('expired_documents', 0))),
            ("Avg AI Confidence",       f"{ev.get('average_ai_confidence', 0):.1f}%"),
            ("Reasonable Assurance",    "MET" if ev.get('reasonable_assurance_met') else "NOT MET"),
        ])

        doc_details = ev.get('document_details', [])
        if doc_details:
            elements += self._section_heading("Document Detail")
            headers = ["Document Type", "Status", "Confidence", "CO2 (tonnes)", "Assurance"]
            rows    = [
                [
                    d['document_type'],
                    d['status'],
                    f"{d['confidence']:.1f}%" if d['confidence'] else '—',
                    f"{d['co2_extracted']:,.2f}" if d['co2_extracted'] else '—',
                    "Met" if d['assurance_met'] else "Not met",
                ]
                for d in doc_details
            ]
            elements += self._data_table(headers, rows)

        em = data.get('scope_emissions', {})
        elements += self._section_heading("Emission Data")
        elements += self._kv_table([
            ("Total CO2 Emissions",  f"{em.get('total_co2_tonnes', 0):,.2f} tonnes CO2e"),
            ("Risk Score",           f"{em.get('risk_score', 0):.1f} / 100"),
            ("Exceeds Threshold",    "Yes" if em.get('exceeds_threshold') else "No"),
        ])

        exposure = data.get('regulatory_risk_exposure', [])
        if exposure:
            elements += self._section_heading("Regulatory Risk & Financial Exposure")
            for e in exposure:
                rows = [("Regulation", e.get('regulation', '—')), ("Status", e.get('status', '—'))]
                if e.get('estimated_carbon_cost_eur'):
                    rows.append(("Estimated CBAM Cost (EUR)", f"EUR {e['estimated_carbon_cost_eur']:,.2f}"))
                    rows.append(("Estimated CBAM Cost (INR)", f"INR {e['estimated_carbon_cost_inr']:,.2f}"))
                if e.get('max_penalty_display'):
                    rows.append(("Max NGT Penalty", e['max_penalty_display']))
                if e.get('gap'):
                    rows.append(("Gap", e['gap']))
                elements += self._kv_table(rows)

        gaps = data.get('compliance_gap_analysis', [])
        if gaps:
            elements += self._section_heading("Compliance Gaps")
            elements += self._bullet_list(gaps)

        recs = data.get('recommendations', [])
        if recs:
            elements += self._section_heading("Recommendations")
            elements += self._bullet_list(recs)

        return elements

    def _build_vendor_risk_body(self, data):
        elements = []
        elements += self._section_heading("Risk Summary")
        elements += self._kv_table([
            ("Overall Risk Score", f"{data.get('risk_summary', {}).get('overall_score', 0)} / 100"),
            ("Risk Level",         data.get('risk_summary', {}).get('risk_level', '—').upper()),
            ("Compliance Status",  data.get('vendor', {}).get('compliance_status', '—')),
        ])
        factors = data.get('risk_summary', {}).get('factors', [])
        if factors:
            elements += self._section_heading("Risk Factors")
            elements += self._bullet_list([f"{f['name']} — {f['impact']}" for f in factors])
        em = data.get('emissions', {})
        elements += self._section_heading("Emissions Data")
        elements += self._kv_table([
            ("Total CO2",          f"{em.get('total_co2', 0):,.1f} {em.get('unit', 'tonnes')}"),
            ("Exceeds Threshold",  "Yes" if em.get('exceeds_threshold') else "No"),
        ])
        docs = data.get('documents', {})
        elements += self._section_heading("Document Status")
        elements += self._kv_table([
            ("Total", str(docs.get('total', 0))), ("Valid", str(docs.get('valid', 0))),
            ("Flagged", str(docs.get('flagged', 0))), ("Expired", str(docs.get('expired', 0))),
            ("Avg AI Confidence", f"{docs.get('avg_confidence', 0)}%"),
        ])
        recs = data.get('recommendations', [])
        if recs:
            elements += self._section_heading("Recommendations")
            elements += self._bullet_list(recs)
        return elements

    def _build_compliance_summary_body(self, data):
        elements = []
        s = data.get('summary', {})
        elements += self._section_heading("Organisation Summary")
        elements += self._kv_table([
            ("Total Vendors", str(s.get('total_vendors', 0))),
            ("Compliant",     str(s.get('compliant', 0))),
            ("Non-Compliant", str(s.get('non_compliant', 0))),
            ("Pending",       str(s.get('pending', 0))),
            ("High / Critical Risk", str(s.get('high_risk', 0))),
        ])
        vendors = data.get('vendors', [])
        if vendors:
            elements += self._section_heading("Vendor Status")
            elements += self._data_table(
                ["Vendor", "Industry", "Compliance", "Risk"],
                [[v['name'], v.get('industry') or '—', v['compliance_status'], v['risk_level']] for v in vendors[:30]],
            )
        return elements

    def _build_emissions_overview_body(self, data):
        elements = []
        s = data.get('summary', {})
        elements += self._section_heading("Emissions Summary")
        elements += self._kv_table([
            ("Vendors with Data", str(s.get('total_vendors_with_data', 0))),
            ("Total Emissions",   f"{s.get('total_emissions', 0):,.1f} {s.get('unit', 'tonnes CO2e')}"),
        ])
        top = data.get('vendor_emissions', [])
        if top:
            elements += self._section_heading("Top Emitters")
            elements += self._data_table(
                ["Rank", "Vendor", "Industry", "CO2 (tonnes)", "Risk"],
                [[str(i+1), v['vendor_name'], v.get('industry') or '—', f"{v['total_co2']:,.1f}", v['risk_level']] for i, v in enumerate(top[:15])],
            )
        return elements

    def _build_document_audit_body(self, data):
        elements = []
        vs = data.get('validation_summary', {})
        elements += self._section_heading("Validation Summary")
        elements += self._kv_table([
            ("Total Validations",    str(vs.get('total_validations', 0))),
            ("Completed",            str(vs.get('completed', 0))),
            ("Auto-Approved",        str(vs.get('auto_approved', 0))),
            ("Flagged for Review",   str(vs.get('flagged_for_review', 0))),
            ("Auto-Approval Rate",   f"{vs.get('auto_approval_rate', 0)}%"),
        ])
        qm = data.get('quality_metrics', {})
        elements += self._section_heading("Quality Metrics")
        elements += self._kv_table([
            ("Avg Overall Confidence",  f"{qm.get('avg_overall_confidence', 0)}%"),
            ("Avg Relevance Score",     f"{qm.get('avg_relevance_score', 0)}%"),
            ("Avg Authenticity Score",  f"{qm.get('avg_authenticity_score', 0)}%"),
        ])
        vs2 = data.get('vendor_summaries', [])
        if vs2:
            elements += self._section_heading("Per-Vendor Summary")
            elements += self._data_table(
                ["Vendor", "Validations", "Flagged", "Avg Confidence"],
                [[v['vendor_name'], str(v['total_validations']), str(v['flagged']), f"{v['avg_confidence']}%"] for v in vs2[:25]],
            )
        return elements

    def _section_heading(self, text):
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        style = ParagraphStyle('SH', fontSize=13, fontName='Helvetica-Bold',
                               textColor=self._rl_color(self.BRAND_GREEN), spaceBefore=8*mm, spaceAfter=3*mm)
        return [Paragraph(text, style)]

    def _kv_table(self, rows):
        from reportlab.platypus import Table, TableStyle, Spacer
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        if not rows:
            return []
        t = Table([[k, v] for k, v in rows], colWidths=[80*mm, 90*mm])
        t.setStyle(TableStyle([
            ('FONTNAME',       (0,0), (0,-1), 'Helvetica-Bold'),
            ('FONTNAME',       (1,0), (1,-1), 'Helvetica'),
            ('FONTSIZE',       (0,0), (-1,-1), 10),
            ('TEXTCOLOR',      (0,0), (0,-1),  self._rl_color(self.MID_GRAY)),
            ('TOPPADDING',     (0,0), (-1,-1), 5),
            ('BOTTOMPADDING',  (0,0), (-1,-1), 5),
            ('LEFTPADDING',    (0,0), (-1,-1), 8),
            ('GRID',           (0,0), (-1,-1), 0.25, colors.HexColor('#DDDDDD')),
        ]))
        return [t, Spacer(1, 3*mm)]

    def _data_table(self, headers, rows):
        from reportlab.platypus import Table, TableStyle, Spacer
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        if not rows:
            return []
        t = Table([headers] + rows, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,0),  self._rl_color(self.BRAND_GREEN)),
            ('TEXTCOLOR',     (0,0), (-1,0),  colors.white),
            ('FONTNAME',      (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,0),  9),
            ('FONTNAME',      (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE',      (0,1), (-1,-1), 9),
            ('TOPPADDING',    (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
            ('LEFTPADDING',   (0,0), (-1,-1), 6),
            ('GRID',          (0,0), (-1,-1), 0.25, colors.HexColor('#DDDDDD')),
        ]))
        return [t, Spacer(1, 4*mm)]

    def _bullet_list(self, items):
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        style = ParagraphStyle('Bullet', fontSize=10, fontName='Helvetica', leftIndent=12, spaceAfter=3)
        elements = [Paragraph(f"• {item}", style) for item in items]
        elements.append(Spacer(1, 4))
        return elements

    def _draw_header_footer(self, canvas, doc):
        from reportlab.lib.units import mm
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColorRGB(*self.MID_GRAY)
        canvas.drawString(20*mm, 10*mm, f"Page {canvas.getPageNumber()}")
        canvas.drawRightString(doc.pagesize[0] - 20*mm, 10*mm, "CarbonSentry — Confidential")
        canvas.restoreState()

    def _rl_color(self, rgb):
        from reportlab.lib.colors import Color
        return Color(*rgb)

    def _report_type_label(self, rt):
        return {
            'vendor_risk':              'Vendor Risk Report',
            'compliance_summary':       'Compliance Summary',
            'emissions_overview':       'Emissions Overview',
            'document_audit':           'Document Audit Report',
            'vendor_compliance_report': 'Vendor Compliance Report (SEBI BRSR / EU CBAM)',
        }.get(rt, rt.replace('_', ' ').title())