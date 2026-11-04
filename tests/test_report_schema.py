import typing

from reports.schema import EXPORTER_READS, SECTION_TYPES, VendorComplianceReport


def _keys(section):
    return set(typing.get_type_hints(SECTION_TYPES[section]))


class TestSchemaCoversExporter:

    def test_every_exporter_section_has_a_schema_type(self):
        assert set(EXPORTER_READS) <= set(SECTION_TYPES)

    def test_schema_contains_every_key_the_exporter_reads(self):
        for section, reads in EXPORTER_READS.items():
            missing = reads - _keys(section)
            assert not missing, f"{section or 'top-level'} schema lacks {sorted(missing)}"

    def test_long_form_document_keys_and_nullable_risk_score(self):
        ev = _keys("emission_verification")
        assert {"total_documents", "valid_documents", "flagged_documents",
                "invalid_documents", "pending_documents", "expired_documents"} <= ev
        hints = typing.get_type_hints(SECTION_TYPES["scope_emissions"])
        assert type(None) in typing.get_args(hints["risk_score"])

    def test_top_level_declares_recommendations_and_checklist(self):
        keys = set(typing.get_type_hints(VendorComplianceReport))
        assert {"recommendations", "action_checklist"} <= keys
