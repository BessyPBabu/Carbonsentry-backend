import json
import re
from datetime import datetime, date


class ResponseParser:
    @staticmethod
    def parse_json(text):
        """Extract and parse JSON from AI response"""
        try:
            # Try direct parse first
            return True, json.loads(text), None
        except:
            pass
        
        try:
            # Extract JSON from markdown code blocks
            json_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
            matches = re.findall(json_pattern, text, re.DOTALL)
            
            if matches:
                return True, json.loads(matches[0]), None
            
            # Try to find JSON object directly
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            matches = re.findall(json_pattern, text, re.DOTALL)
            
            if matches:
                return True, json.loads(matches[-1]), None
            
            return False, None, "No valid JSON found in response"
            
        except Exception as e:
            return False, None, f"JSON parsing error: {str(e)}"


class DataValidator:
    @staticmethod
    def validate_date(date_str):
        """Validate date is in reasonable range"""
        if not date_str:
            return True, None
        
        try:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Check reasonable range (not in future, not before 2000)
            today = date.today()
            min_date = date(2000, 1, 1)
            
            if parsed_date > today:
                return False, "Date cannot be in the future"
            
            if parsed_date < min_date:
                return False, "Date too far in the past"
            
            return True, parsed_date
            
        except Exception as e:
            return False, f"Invalid date format: {str(e)}"
    
    @staticmethod
    def validate_co2_value(value):
        """Validate CO2 emission value"""
        if value is None:
            return True, None
        
        try:
            value_float = float(value)
            
            if value_float < 0:
                return False, "CO2 value cannot be negative"
            
            if value_float > 1000000000:  # 1 billion tonnes (unrealistic)
                return False, "CO2 value unrealistically high"
            
            return True, value_float
            
        except Exception as e:
            return False, f"Invalid CO2 value: {str(e)}"
    
    @staticmethod
    def normalize_unit(unit):
        """Normalize CO2 unit to standard format"""
        if not unit:
            return 'tonnes'
        
        unit_lower = unit.lower().strip()
        
        if 'kg' in unit_lower or 'kilogram' in unit_lower:
            return 'kg'
        elif 'ton' in unit_lower or 'tonne' in unit_lower:
            return 'tonnes'
        else:
            return 'tonnes'  # default