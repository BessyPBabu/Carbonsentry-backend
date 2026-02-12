# Confidence thresholds
MIN_AUTO_APPROVE_CONFIDENCE = 70.0
MIN_EXTRACTION_CONFIDENCE = 60.0
MAX_RETRY_ATTEMPTS = 2

# Confidence weights
CONFIDENCE_WEIGHTS = {
    'readability': 0.15,
    'relevance': 0.25,
    'authenticity': 0.30,
    'extraction': 0.30,
}

# Document types
VALID_DOCUMENT_TYPES = [
    'Carbon Credit Certificate',
    'Emission Report',
    'Carbon Offset Certificate',
    'GHG Inventory Report',
    'Sustainability Certificate',
    'ISO 14064 Certificate',
]

# Verification standards
VERIFICATION_STANDARDS = [
    'ISO 14064',
    'GHG Protocol',
    'PAS 2060',
    'Carbon Neutral Certification',
]

# Default thresholds (tonnes CO2e/year)
DEFAULT_THRESHOLDS = {
    'Manufacturing': {'low': 1000, 'medium': 5000, 'high': 10000, 'critical': 50000},
    'Technology': {'low': 500, 'medium': 2000, 'high': 5000, 'critical': 10000},
    'Retail': {'low': 300, 'medium': 1500, 'high': 3000, 'critical': 8000},
    'Logistics': {'low': 2000, 'medium': 10000, 'high': 20000, 'critical': 100000},
    'Energy': {'low': 5000, 'medium': 20000, 'high': 50000, 'critical': 200000},
    'default': {'low': 1000, 'medium': 5000, 'high': 10000, 'critical': 50000},
}