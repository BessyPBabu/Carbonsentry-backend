import io
import logging
from decimal import Decimal
from django.db.models import Avg, Count, Q, Sum

logger = logging.getLogger(__name__)



def _sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(i) for i in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


class ReportGenerator:

    def generate(self, report_type, organization, vendor=None, date_from=None, date_to=None):
        logger.info(
            "Starting report generation | type=%s org=%s vendor=%s",
            report_type, organization.id, vendor.id if vendor else None
        )

        generators = {
            'vendor_risk': self._generate_vendor_risk,
            'compliance_summary': self._generate_compliance_summary,
            'emissions_overview': self._generate_emissions_overview,
            'document_audit': self._generate_document_audit,
        }

        generator_fn = generators.get(report_type)
        if not generator_fn:
            logger.error("Unknown report_type requested: %s", report_type)
            raise ValueError(f"Unknown report type: {report_type}")

        try:
            data = generator_fn(
                organization=organization,
                vendor=vendor,
                date_from=date_from,
                date_to=date_to,
            )
            data = _sanitize_for_json(data)
            logger.info("Report generation complete | type=%s", report_type)
            return data
        except Exception as exc:
            logger.exception(
                "Report generation failed | type=%s org=%s error=%s",
                report_type, organization.id, str(exc)
            )
            raise


    def _generate_vendor_risk(self, organization, vendor, date_from=None, date_to=None):
        from vendors.models import Document
        from ai_validation.models import VendorRiskProfile, DocumentValidation

        logger.debug("Generating vendor_risk for vendor=%s", vendor.id)

        if not vendor:
            raise ValueError("vendor is required for vendor_risk report")

        
        try:
            risk_profile = VendorRiskProfile.objects.get(vendor=vendor)
        except VendorRiskProfile.DoesNotExist:
            logger.warning("No VendorRiskProfile found for vendor=%s", vendor.id)
            risk_profile = None

        
        docs_qs = Document.objects.filter(vendor=vendor)
        if date_from:
            docs_qs = docs_qs.filter(uploaded_at__date__gte=date_from)
        if date_to:
            docs_qs = docs_qs.filter(uploaded_at__date__lte=date_to)

        total_docs = docs_qs.count()
        valid_docs = docs_qs.filter(status='valid').count()
        flagged_docs = docs_qs.filter(status='flagged').count()
        expired_docs = docs_qs.filter(status='expired').count()

        
        validations_qs = DocumentValidation.objects.filter(document__vendor=vendor)
        avg_confidence = validations_qs.aggregate(
            avg=Avg('overall_confidence')
        )['avg'] or 0

       
        risk_factors = []
        if risk_profile:
            if flagged_docs > 0:
                ratio = round((flagged_docs / total_docs) * 100) if total_docs else 0
                risk_factors.append({
                    'name': 'High flagged document ratio',
                    'impact': f"{ratio}% flagged"
                })
            if risk_profile.exceeds_threshold:
                risk_factors.append({
                    'name': 'Emissions exceed industry threshold',
                    'impact': 'High'
                })
            if avg_confidence < 60:
                risk_factors.append({
                    'name': 'Low AI confidence scores',
                    'impact': f"avg {round(avg_confidence)}%"
                })

        recommendations = self._build_vendor_recommendations(
            risk_profile=risk_profile,
            flagged_docs=flagged_docs,
            expired_docs=expired_docs,
            avg_confidence=avg_confidence,
        )

        return {
            'vendor': {
                'id': str(vendor.id),
                'name': vendor.name,
                'industry': vendor.industry.name if vendor.industry else None,
                'country': vendor.country,
                'compliance_status': vendor.compliance_status,
            },
            'risk_summary': {
                'overall_score': float(risk_profile.risk_score) if risk_profile else 0,
                'risk_level': risk_profile.risk_level if risk_profile else 'unknown',
                'factors': risk_factors,
            },
            'emissions': {
                'total_co2': float(risk_profile.total_co2_emissions) if risk_profile and risk_profile.total_co2_emissions else 0,
                'unit': 'tonnes CO2e',
                'exceeds_threshold': risk_profile.exceeds_threshold if risk_profile else False,
            },
            'documents': {
                'total': total_docs,
                'valid': valid_docs,
                'flagged': flagged_docs,
                'expired': expired_docs,
                'avg_confidence': round(avg_confidence, 1),
            },
            'recommendations': recommendations,
        }

    def _build_vendor_recommendations(self, risk_profile, flagged_docs, expired_docs, avg_confidence):
        actions = []
        if expired_docs > 0:
            actions.append("Request updated carbon certificates for expired documents")
        if avg_confidence < 70:
            actions.append("Review flagged documents and request higher quality scans from vendor")
        if risk_profile and risk_profile.risk_level in ('high', 'critical'):
            actions.append("Schedule immediate compliance audit")
            actions.append("Escalate to senior management for review")
        if risk_profile and risk_profile.exceeds_threshold:
            actions.append("Request emission reduction plan from vendor")
        if flagged_docs > 0 and risk_profile and (risk_profile.flagged_documents / max(risk_profile.total_documents, 1)) > 0.3:
            actions.append("Increase document verification frequency to quarterly")
        if not actions:
            actions.append("Continue standard monitoring procedures")
        return actions



    def _generate_compliance_summary(self, organization, vendor=None, date_from=None, date_to=None):
        from vendors.models import Vendor
        from ai_validation.models import VendorRiskProfile

        logger.debug("Generating compliance_summary for org=%s", organization.id)

        vendors_qs = Vendor.objects.filter(organization=organization)

        total = vendors_qs.count()
        compliant = vendors_qs.filter(compliance_status='compliant').count()
        non_compliant = vendors_qs.filter(compliance_status='non_compliant').count()
        pending = vendors_qs.filter(compliance_status='pending').count()
        expired_status = vendors_qs.filter(compliance_status='expired').count()

        high_risk = VendorRiskProfile.objects.filter(
            vendor__organization=organization,
            risk_level__in=['high', 'critical']
        ).count()


        vendor_rows = []
        for v in vendors_qs.select_related('industry')[:50]:
            risk_profile = getattr(v, 'risk_profile', None)
            try:
                risk_profile = VendorRiskProfile.objects.get(vendor=v)
            except VendorRiskProfile.DoesNotExist:
                risk_profile = None

            vendor_rows.append({
                'vendor_id': str(v.id),
                'name': v.name,
                'industry': v.industry.name if v.industry else None,
                'country': v.country,
                'compliance_status': v.compliance_status,
                'risk_level': risk_profile.risk_level if risk_profile else 'unknown',
            })

        return {
            'summary': {
                'total_vendors': total,
                'compliant': compliant,
                'non_compliant': non_compliant,
                'pending': pending,
                'expired': expired_status,
                'high_risk': high_risk,
            },
            'vendors': vendor_rows,
        }


    def _generate_emissions_overview(self, organization, vendor=None, date_from=None, date_to=None):
        from ai_validation.models import VendorRiskProfile

        logger.debug("Generating emissions_overview for org=%s", organization.id)

        profiles_qs = VendorRiskProfile.objects.filter(
            vendor__organization=organization,
            total_co2_emissions__isnull=False,
        ).select_related('vendor', 'vendor__industry')

        
        agg = profiles_qs.aggregate(total=Sum('total_co2_emissions'))
        total_emissions = float(agg['total'] or 0)
        vendors_with_data = profiles_qs.count()

        
        vendor_emissions = []
        for p in profiles_qs.order_by('-total_co2_emissions')[:20]:
            vendor_emissions.append({
                'vendor_id': str(p.vendor.id),
                'vendor_name': p.vendor.name,
                'industry': p.vendor.industry.name if p.vendor.industry else None,
                'total_co2': float(p.total_co2_emissions),
                'risk_level': p.risk_level,
                'exceeds_threshold': p.exceeds_threshold,
            })

        
        from django.db.models.functions import Coalesce
        industry_breakdown = (
            profiles_qs
            .values('vendor__industry__name')
            .annotate(total=Sum('total_co2_emissions'), count=Count('id'))
            .order_by('-total')
        )

        return {
            'summary': {
                'total_vendors_with_data': vendors_with_data,
                'total_emissions': total_emissions,
                'unit': 'tonnes CO2e',
            },
            'vendor_emissions': vendor_emissions,
            'by_industry': [
                {
                    'industry': row['vendor__industry__name'] or 'Unknown',
                    'total_co2': float(row['total']),
                    'vendor_count': row['count'],
                }
                for row in industry_breakdown
            ],
        }


    def _generate_document_audit(self, organization, vendor=None, date_from=None, date_to=None):
        from ai_validation.models import DocumentValidation

        logger.debug("Generating document_audit for org=%s", organization.id)

        validations_qs = DocumentValidation.objects.filter(
            document__vendor__organization=organization
        )

        if date_from:
            validations_qs = validations_qs.filter(created_at__date__gte=date_from)
        if date_to:
            validations_qs = validations_qs.filter(created_at__date__lte=date_to)

        total = validations_qs.count()

        completed = validations_qs.filter(status='completed').count()
        failed = validations_qs.filter(status='failed').count()
        processing = validations_qs.filter(status='processing').count()
        flagged = validations_qs.filter(requires_manual_review=True).count()
        auto_approved = completed - flagged if completed >= flagged else 0
        auto_approval_rate = round((auto_approved / completed) * 100, 1) if completed else 0


        quality = validations_qs.aggregate(
            avg_overall=Avg('overall_confidence'),
            avg_readability=Avg('readability_score'),
            avg_relevance=Avg('relevance_confidence'),   # was 'relevance_score' — FIXED
            avg_authenticity=Avg('authenticity_score'),
        )

        def _safe_round(val):
            return round(float(val), 1) if val is not None else 0

        from vendors.models import Vendor
        vendor_summaries = []
        for v in Vendor.objects.filter(organization=organization)[:30]:
            v_qs = validations_qs.filter(document__vendor=v)
            if not v_qs.exists():
                continue
            v_agg = v_qs.aggregate(
                avg_conf=Avg('overall_confidence'),
                total=Count('id'),
                flagged=Count('id', filter=Q(requires_manual_review=True)),
            )
            vendor_summaries.append({
                'vendor_id': str(v.id),
                'vendor_name': v.name,
                'total_validations': v_agg['total'],
                'flagged': v_agg['flagged'],
                'avg_confidence': _safe_round(v_agg['avg_conf']),
            })

        return {
            'validation_summary': {
                'total_validations': total,
                'completed': completed,
                'failed': failed,
                'processing': processing,
                'flagged_for_review': flagged,
                'auto_approved': auto_approved,
                'auto_approval_rate': auto_approval_rate,
            },
            'quality_metrics': {
                'avg_overall_confidence': _safe_round(quality['avg_overall']),
                'avg_readability_score': _safe_round(quality['avg_readability']),
                'avg_relevance_score': _safe_round(quality['avg_relevance']),
                'avg_authenticity_score': _safe_round(quality['avg_authenticity']),
            },
            'vendor_summaries': vendor_summaries,
        }



class PDFExporter:

    BRAND_GREEN = (26 / 255, 143 / 255, 112 / 255)
    LIGHT_GRAY = (0.95, 0.95, 0.95)
    MID_GRAY = (0.6, 0.6, 0.6)

    def export(self, report):
        logger.info("Building PDF for report=%s type=%s", report.id, report.report_type)

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate
        except ImportError:
            logger.error("reportlab is not installed — cannot generate PDF")
            raise RuntimeError("reportlab is required for PDF export")

        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=20 * mm,
            rightMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
        )

        story = []
        story += self._build_cover(report)
        story += self._build_body(report)

        try:
            doc.build(story, onFirstPage=self._draw_header_footer, onLaterPages=self._draw_header_footer)
        except Exception as exc:
            logger.exception("ReportLab build failed for report=%s | error=%s", report.id, str(exc))
            raise

        pdf_bytes = buffer.getvalue()
        buffer.close()

        logger.info("PDF built successfully for report=%s | size=%d bytes", report.id, len(pdf_bytes))
        return pdf_bytes


    def _build_cover(self, report):
        from reportlab.platypus import Paragraph, Spacer, HRFlowable
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.lib.enums import TA_CENTER

        title_style = ParagraphStyle(
            'CoverTitle',
            fontSize=24,
            fontName='Helvetica-Bold',
            textColor=self._rl_color(self.BRAND_GREEN),
            alignment=TA_CENTER,
            spaceAfter=6,
        )
        sub_style = ParagraphStyle(
            'CoverSub',
            fontSize=11,
            fontName='Helvetica',
            textColor=self._rl_color(self.MID_GRAY),
            alignment=TA_CENTER,
            spaceAfter=4,
        )

        generated_str = report.generated_at.strftime("%d %b %Y, %H:%M") if report.generated_at else '—'
        generated_by = report.generated_by.get_full_name() or report.generated_by.email if report.generated_by else '—'

        elements = [
            Spacer(1, 30 * mm),
            Paragraph(report.title, title_style),
            Spacer(1, 4 * mm),
            Paragraph(self._report_type_label(report.report_type), sub_style),
            Paragraph(f"Generated by {generated_by} on {generated_str}", sub_style),
            Spacer(1, 6 * mm),
            HRFlowable(width='100%', thickness=1, color=self._rl_color(self.BRAND_GREEN)),
            Spacer(1, 10 * mm),
        ]

        if report.status == 'approved' and report.approved_by:
            approved_str = report.approved_at.strftime("%d %b %Y") if report.approved_at else '—'
            approver = report.approved_by.get_full_name() or report.approved_by.email
            approved_style = ParagraphStyle(
                'Approved',
                fontSize=10,
                fontName='Helvetica',
                textColor=self._rl_color((0.1, 0.6, 0.3)),
                alignment=TA_CENTER,
            )
            elements.append(Paragraph(f"✓ Approved by {approver} on {approved_str}", approved_style))
            elements.append(Spacer(1, 6 * mm))

        return elements


    def _build_body(self, report):
        builders = {
            'vendor_risk': self._build_vendor_risk_body,
            'compliance_summary': self._build_compliance_summary_body,
            'emissions_overview': self._build_emissions_overview_body,
            'document_audit': self._build_document_audit_body,
        }
        builder = builders.get(report.report_type)
        if not builder:
            logger.warning("No PDF body builder for report_type=%s", report.report_type)
            return []
        return builder(report.data)

    def _build_vendor_risk_body(self, data):
        elements = []
        elements += self._section_heading("Risk Summary")
        elements += self._kv_table([
            ("Overall Risk Score", f"{data.get('risk_summary', {}).get('overall_score', 0)} / 100"),
            ("Risk Level", data.get('risk_summary', {}).get('risk_level', '—').upper()),
            ("Compliance Status", data.get('vendor', {}).get('compliance_status', '—')),
        ])

        factors = data.get('risk_summary', {}).get('factors', [])
        if factors:
            elements += self._section_heading("Risk Factors")
            elements += self._bullet_list([f"{f['name']} — {f['impact']}" for f in factors])

        elements += self._section_heading("Emissions Data")
        em = data.get('emissions', {})
        elements += self._kv_table([
            ("Total CO₂", f"{em.get('total_co2', 0):,.1f} {em.get('unit', 'tonnes')}"),
            ("Exceeds Threshold", "Yes" if em.get('exceeds_threshold') else "No"),
        ])

        elements += self._section_heading("Document Status")
        docs = data.get('documents', {})
        elements += self._kv_table([
            ("Total Documents", str(docs.get('total', 0))),
            ("Valid", str(docs.get('valid', 0))),
            ("Flagged", str(docs.get('flagged', 0))),
            ("Expired", str(docs.get('expired', 0))),
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
            ("Compliant", str(s.get('compliant', 0))),
            ("Non-Compliant", str(s.get('non_compliant', 0))),
            ("Pending", str(s.get('pending', 0))),
            ("Expired", str(s.get('expired', 0))),
            ("High / Critical Risk", str(s.get('high_risk', 0))),
        ])

        vendors = data.get('vendors', [])
        if vendors:
            elements += self._section_heading("Vendor Status")
            headers = ["Vendor", "Industry", "Compliance", "Risk Level"]
            rows = [
                [v['name'], v.get('industry') or '—', v['compliance_status'], v['risk_level']]
                for v in vendors[:30]
            ]
            elements += self._data_table(headers, rows)

        return elements

    def _build_emissions_overview_body(self, data):
        elements = []
        s = data.get('summary', {})
        elements += self._section_heading("Emissions Summary")
        elements += self._kv_table([
            ("Vendors with Data", str(s.get('total_vendors_with_data', 0))),
            ("Total Emissions", f"{s.get('total_emissions', 0):,.1f} {s.get('unit', 'tonnes CO2e')}"),
        ])

        top_emitters = data.get('vendor_emissions', [])
        if top_emitters:
            elements += self._section_heading("Top Emitters")
            headers = ["Rank", "Vendor", "Industry", "CO₂ (tonnes)", "Risk"]
            rows = [
                [str(i + 1), v['vendor_name'], v.get('industry') or '—', f"{v['total_co2']:,.1f}", v['risk_level']]
                for i, v in enumerate(top_emitters[:15])
            ]
            elements += self._data_table(headers, rows)

        by_industry = data.get('by_industry', [])
        if by_industry:
            elements += self._section_heading("By Industry")
            headers = ["Industry", "Total CO₂ (tonnes)", "Vendors"]
            rows = [
                [row['industry'], f"{row['total_co2']:,.1f}", str(row['vendor_count'])]
                for row in by_industry
            ]
            elements += self._data_table(headers, rows)

        return elements

    def _build_document_audit_body(self, data):
        elements = []

        vs = data.get('validation_summary', {})
        elements += self._section_heading("Validation Summary")
        elements += self._kv_table([
            ("Total Validations", str(vs.get('total_validations', 0))),
            ("Completed", str(vs.get('completed', 0))),
            ("Auto-Approved", str(vs.get('auto_approved', 0))),
            ("Flagged for Review", str(vs.get('flagged_for_review', 0))),
            ("Auto-Approval Rate", f"{vs.get('auto_approval_rate', 0)}%"),
            ("Failed", str(vs.get('failed', 0))),
        ])

        qm = data.get('quality_metrics', {})
        elements += self._section_heading("Quality Metrics")
        elements += self._kv_table([
            ("Avg Overall Confidence", f"{qm.get('avg_overall_confidence', 0)}%"),
            ("Avg Readability Score", f"{qm.get('avg_readability_score', 0)}%"),
            ("Avg Relevance Score", f"{qm.get('avg_relevance_score', 0)}%"),
            ("Avg Authenticity Score", f"{qm.get('avg_authenticity_score', 0)}%"),
        ])

        vendor_summaries = data.get('vendor_summaries', [])
        if vendor_summaries:
            elements += self._section_heading("Per-Vendor Summary")
            headers = ["Vendor", "Validations", "Flagged", "Avg Confidence"]
            rows = [
                [v['vendor_name'], str(v['total_validations']), str(v['flagged']), f"{v['avg_confidence']}%"]
                for v in vendor_summaries[:25]
            ]
            elements += self._data_table(headers, rows)

        return elements


    def _section_heading(self, text):
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm

        style = ParagraphStyle(
            'SectionHeading',
            fontSize=13,
            fontName='Helvetica-Bold',
            textColor=self._rl_color(self.BRAND_GREEN),
            spaceBefore=8 * mm,
            spaceAfter=3 * mm,
        )
        return [Paragraph(text, style)]

    def _kv_table(self, rows):
        from reportlab.platypus import Table, TableStyle, Spacer
        from reportlab.lib import colors
        from reportlab.lib.units import mm

        if not rows:
            return []

        table_data = [[k, v] for k, v in rows]
        col_widths = [80 * mm, 90 * mm]

        table = Table(table_data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TEXTCOLOR', (0, 0), (0, -1), self._rl_color(self.MID_GRAY)),
            ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.white, self._rl_color(self.LIGHT_GRAY)]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#DDDDDD')),
        ]))
        return [table, Spacer(1, 3 * mm)]

    def _data_table(self, headers, rows):
        from reportlab.platypus import Table, TableStyle, Spacer
        from reportlab.lib import colors
        from reportlab.lib.units import mm

        if not rows:
            return []

        table_data = [headers] + rows
        table = Table(table_data, repeatRows=1)
        table.setStyle(TableStyle([
            # header row
            ('BACKGROUND', (0, 0), (-1, 0), self._rl_color(self.BRAND_GREEN)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            # body rows
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, self._rl_color(self.LIGHT_GRAY)]),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#DDDDDD')),
        ]))
        return [table, Spacer(1, 4 * mm)]

    def _bullet_list(self, items):
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle

        style = ParagraphStyle(
            'Bullet',
            fontSize=10,
            fontName='Helvetica',
            leftIndent=12,
            spaceAfter=3,
        )
        elements = []
        for item in items:
            elements.append(Paragraph(f"• {item}", style))
        elements.append(Spacer(1, 4))
        return elements

    def _draw_header_footer(self, canvas, doc):
        from reportlab.lib.units import mm

        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColorRGB(*self.MID_GRAY)
        canvas.drawString(20 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.drawRightString(
            doc.pagesize[0] - 20 * mm,
            10 * mm,
            "CarbonSentry — Confidential"
        )
        canvas.restoreState()



    def _rl_color(self, rgb_tuple):
        from reportlab.lib.colors import Color
        return Color(*rgb_tuple)

    def _report_type_label(self, report_type):
        labels = {
            'vendor_risk': 'Vendor Risk Report',
            'compliance_summary': 'Compliance Summary',
            'emissions_overview': 'Emissions Overview',
            'document_audit': 'Document Audit Report',
        }
        return labels.get(report_type, report_type.replace('_', ' ').title())