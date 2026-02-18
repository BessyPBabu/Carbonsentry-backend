MIN_AUTO_APPROVE_CONFIDENCE = 55.0
MIN_EXTRACTION_CONFIDENCE = 40.0
MAX_RETRY_ATTEMPTS = 2

CONFIDENCE_WEIGHTS = {
    'readability':  0.10,
    'relevance':    0.25,
    'authenticity': 0.25,
    'extraction':   0.40,
}

VALID_DOCUMENT_TYPES = [
    'Carbon Credit Certificate',
    'Emission Report',
    'Carbon Offset Certificate',
    'GHG Inventory Report',
    'Sustainability Certificate',
    'ISO 14064 Certificate',
]

VERIFICATION_STANDARDS = [
    'ISO 14064',
    'GHG Protocol',
    'PAS 2060',
    'Carbon Neutral Certification',
    'Verra VCS',
    'Gold Standard',
]

DEFAULT_THRESHOLDS = {
    'Manufacturing': {'low': 1000,  'medium': 5000,  'high': 15000, 'critical': 50000},
    'Technology':    {'low': 300,   'medium': 1500,  'high': 5000,  'critical': 12000},
    'Retail':        {'low': 300,   'medium': 1500,  'high': 3000,  'critical': 8000},
    'Logistics':     {'low': 2000,  'medium': 10000, 'high': 30000, 'critical': 100000},
    'Energy':        {'low': 5000,  'medium': 20000, 'high': 80000, 'critical': 250000},
    'Healthcare':    {'low': 400,   'medium': 2000,  'high': 7000,  'critical': 20000},
    'default':       {'low': 1000,  'medium': 5000,  'high': 10000, 'critical': 50000},
}

# risk scores stored 0-100 in DB; divide by 20 to display as X.X / 5
RISK_SCORE_DISPLAY_DIVISOR = 20