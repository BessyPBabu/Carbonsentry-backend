from django.core.management.base import BaseCommand
from decimal import Decimal
from vendors.models import Industry, DocumentType, IndustryRequiredDocument
from ai_validation.models import IndustryEmissionThreshold


class Command(BaseCommand):
    help = "Seed base configuration data for CarbonSentry"

    def handle(self, *args, **kwargs):

        self.stdout.write("=" * 80)
        self.stdout.write("CARBONSENTRY - BASE DATA SETUP")
        self.stdout.write("=" * 80)

        # ============================================================
        # STEP 1: Create Industries
        # ============================================================
        self.stdout.write("\n[1/4] Creating Industries...")

        industries_data = [
            ("Manufacturing", "Manufacturing, production facilities, factories, and industrial operations"),
            ("Technology", "Technology companies, software development, data centers, IT services"),
            ("Retail", "Retail stores, e-commerce, consumer goods, shopping centers"),
            ("Logistics", "Transportation, shipping, warehousing, supply chain, freight"),
            ("Energy", "Power generation, utilities, oil & gas, renewable energy"),
            ("Construction", "Construction, real estate development, building materials"),
            ("Agriculture", "Farming, food production, agribusiness, livestock"),
            ("Healthcare", "Hospitals, clinics, pharmaceutical, medical services"),
            ("Finance", "Banking, insurance, investment firms, financial services"),
            ("Hospitality", "Hotels, restaurants, tourism, entertainment, leisure"),
        ]

        industries = {}

        for name, description in industries_data:
            industry, created = Industry.objects.get_or_create(
                name=name,
                defaults={"description": description}
            )
            industries[name] = industry

            status = "Created" if created else "Exists"
            self.stdout.write(f"  ✓ {status}: {name}")

        self.stdout.write(f"\n  Total Industries: {Industry.objects.count()}")

        # ============================================================
        # STEP 2: Create Document Types
        # ============================================================
        self.stdout.write("\n[2/4] Creating Document Types...")

        document_types_data = [
            ("Carbon Credit Certificate", "Official carbon credit certification for offset purchases"),
            ("Emission Report", "Annual greenhouse gas emissions inventory and reporting"),
            ("Carbon Offset Certificate", "Verification of carbon offset projects and credits"),
            ("GHG Inventory Report", "Detailed greenhouse gas inventory following GHG Protocol"),
            ("Sustainability Certificate", "General sustainability and environmental compliance certification"),
            ("ISO 14064 Certificate", "ISO 14064 greenhouse gas accounting and verification standard"),
            ("Energy Audit Report", "Comprehensive energy consumption and efficiency assessment"),
            ("Renewable Energy Certificate", "Proof of renewable energy generation or purchase"),
            ("Environmental Impact Assessment", "Detailed environmental impact analysis and mitigation plans"),
            ("Carbon Neutrality Declaration", "Official declaration and proof of carbon neutral operations"),
        ]

        doc_types = {}

        for name, description in document_types_data:
            doc_type, created = DocumentType.objects.get_or_create(
                name=name,
                defaults={"description": description}
            )
            doc_types[name] = doc_type

            status = "Created" if created else "Exists"
            self.stdout.write(f"  ✓ {status}: {name}")

        self.stdout.write(f"\n  Total Document Types: {DocumentType.objects.count()}")

        # ============================================================
        # STEP 3: Industry-Document Mapping
        # ============================================================
        self.stdout.write("\n[3/4] Creating Industry-Document Mappings...")

        core_mandatory_documents = [
            "Carbon Credit Certificate",
            "Emission Report",
        ]

        industry_mandatory_docs = {
            "Manufacturing": ["GHG Inventory Report", "ISO 14064 Certificate", "Environmental Impact Assessment"],
            "Technology": ["ISO 14064 Certificate", "Energy Audit Report"],
            "Retail": ["Sustainability Certificate", "Carbon Offset Certificate"],
            "Logistics": ["GHG Inventory Report", "Carbon Offset Certificate", "Emission Report"],
            "Energy": ["GHG Inventory Report", "Environmental Impact Assessment", "Renewable Energy Certificate", "ISO 14064 Certificate"],
            "Construction": ["Environmental Impact Assessment", "Sustainability Certificate", "GHG Inventory Report"],
            "Agriculture": ["Environmental Impact Assessment", "Sustainability Certificate", "Carbon Offset Certificate"],
            "Healthcare": ["ISO 14064 Certificate", "Energy Audit Report", "Sustainability Certificate"],
            "Finance": ["Sustainability Certificate", "Carbon Neutrality Declaration"],
            "Hospitality": ["Energy Audit Report", "Sustainability Certificate", "Carbon Offset Certificate"],
        }

        industry_optional_docs = {
            "Manufacturing": ["Renewable Energy Certificate", "Carbon Neutrality Declaration"],
            "Technology": ["Carbon Neutrality Declaration", "Renewable Energy Certificate"],
            "Retail": ["Renewable Energy Certificate", "Energy Audit Report"],
            "Logistics": ["Environmental Impact Assessment", "Renewable Energy Certificate"],
            "Energy": ["Carbon Neutrality Declaration"],
            "Construction": ["Renewable Energy Certificate", "Energy Audit Report"],
            "Agriculture": ["Renewable Energy Certificate", "GHG Inventory Report"],
            "Healthcare": ["Renewable Energy Certificate", "Carbon Offset Certificate"],
            "Finance": ["ISO 14064 Certificate", "Renewable Energy Certificate"],
            "Hospitality": ["Environmental Impact Assessment", "Carbon Neutrality Declaration"],
        }

        mapping_count = 0

        for industry_name, industry_obj in industries.items():
            self.stdout.write(f"\n  Configuring: {industry_name}")

            # Core mandatory
            for doc_name in core_mandatory_documents:
                _, created = IndustryRequiredDocument.objects.get_or_create(
                    industry=industry_obj,
                    document_type=doc_types[doc_name],
                    defaults={"mandatory": True}
                )
                if created:
                    mapping_count += 1

            # Industry mandatory
            for doc_name in industry_mandatory_docs.get(industry_name, []):
                _, created = IndustryRequiredDocument.objects.get_or_create(
                    industry=industry_obj,
                    document_type=doc_types[doc_name],
                    defaults={"mandatory": True}
                )
                if created:
                    mapping_count += 1

            # Industry optional
            for doc_name in industry_optional_docs.get(industry_name, []):
                _, created = IndustryRequiredDocument.objects.get_or_create(
                    industry=industry_obj,
                    document_type=doc_types[doc_name],
                    defaults={"mandatory": False}
                )
                if created:
                    mapping_count += 1

        self.stdout.write(f"\n  Total Mappings Created: {mapping_count}")

        # ============================================================
        # STEP 4: Emission Thresholds
        # ============================================================
        self.stdout.write("\n[4/4] Creating Industry Emission Thresholds...")

        thresholds_config = {
            "Manufacturing": (800, 4000, 12000, 60000),
            "Technology": (400, 1800, 5500, 12000),
            "Retail": (250, 1200, 3500, 9000),
            "Logistics": (1500, 8000, 25000, 120000),
            "Energy": (3000, 15000, 60000, 250000),
            "Construction": (600, 3000, 10000, 40000),
            "Agriculture": (1000, 5000, 15000, 70000),
            "Healthcare": (500, 2500, 8000, 25000),
            "Finance": (200, 800, 2500, 8000),
            "Hospitality": (400, 2000, 6000, 20000),
        }

        for industry_name, values in thresholds_config.items():
            industry = industries[industry_name]
            low, medium, high, critical = values

            IndustryEmissionThreshold.objects.update_or_create(
                industry=industry,
                defaults={
                    "low_threshold": Decimal(str(low)),
                    "medium_threshold": Decimal(str(medium)),
                    "high_threshold": Decimal(str(high)),
                    "critical_threshold": Decimal(str(critical)),
                }
            )

            self.stdout.write(f"  ✓ Threshold Configured: {industry_name}")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("BASE DATA SETUP COMPLETE!")
        self.stdout.write("=" * 80)

        self.stdout.write("\nDatabase Summary:")
        self.stdout.write(f"  • Industries: {Industry.objects.count()}")
        self.stdout.write(f"  • Document Types: {DocumentType.objects.count()}")
        self.stdout.write(f"  • Industry-Document Mappings: {IndustryRequiredDocument.objects.count()}")
        self.stdout.write(f"  • Emission Thresholds: {IndustryEmissionThreshold.objects.count()}")

        self.stdout.write(self.style.SUCCESS("\nSample data seeded successfully."))
