import re
from decimal import Decimal
from typing import Optional, Tuple, List, Dict, Any, Pattern
from datetime import datetime
import logging
from src.models.admission import CurrencyCode, StudyMode, RoundType
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

class DataCleaner:
    """
    Parses and cleans raw data strings into structured formats.
    """
    
    # Pre-compile regex for performance
    CURRENCY_MAP = {
        r'HK\$': CurrencyCode.HKD,
        r'HKD': CurrencyCode.HKD,
        r'US\$': CurrencyCode.USD,
        r'USD': CurrencyCode.USD,
        r'RMB': CurrencyCode.CNY,
        r'CNY': CurrencyCode.CNY,
        r'£': CurrencyCode.GBP,
        r'GBP': CurrencyCode.GBP
    }
    
    # "HK$ 350,000" -> group 1 (currency), group 2 (amount)
    # Using specific regex for robustness
    RE_TUITION = re.compile(r'([A-Z]{3}|HK\$|US\$|£|RMB)\s*([\d,]+)')
    
    # Support English and Chinese duration
    RE_DURATION_YEAR = re.compile(r'(\d+(\.\d+)?)[\s-]*(year|yr|年)', re.IGNORECASE)
    RE_DURATION_MONTH = re.compile(r'(\d+)[\s-]*(month|mth|个月)', re.IGNORECASE)

    @staticmethod
    def parse_tuition(text: Optional[str]) -> Tuple[Optional[Decimal], Optional[CurrencyCode]]:
        """
        Parses tuition string like "HK$ 350,000" or "RMB 100000".
        Returns (amount, currency_code).
        """
        if not text:
            return None, None
            
        clean_text = str(text).strip()
        
        # Iteratively check currency patterns if regex fails or to guide regex
        match = DataCleaner.RE_TUITION.search(clean_text)
        if match:
            curr_str, amount_str = match.groups()
            
            # Resolve currency
            currency = CurrencyCode.OTHER
            for pattern, code in DataCleaner.CURRENCY_MAP.items():
                if re.match(pattern, curr_str, re.IGNORECASE):
                    currency = code
                    break
            
            # Resolve amount
            try:
                # Remove commas
                amount = Decimal(amount_str.replace(',', ''))
                return amount, currency
            except Exception:
                pass
                
        return None, None

    @staticmethod
    def parse_study_options(text: Optional[str]) -> List[Dict[str, Any]]:
        """
        Parses duration like "Full-time 1 year" or "PT 2 years".
        Returns list of dicts: [{"mode": "FullTime", "duration_months": 12}]
        """
        if not text:
            return []
            
        text = str(text).lower()
        options = []
        
        # Determine mode
        mode = StudyMode.UNKNOWN
        if 'full-time' in text or 'full time' in text or '全日制' in text:
            mode = StudyMode.FULL_TIME
        elif 'part-time' in text or 'part time' in text or '兼读制' in text or '非全日制' in text:
            mode = StudyMode.PART_TIME
        elif 'mode' not in text: 
             # Default assumption if not specified? 
             # Let's verify context. If text is "1 year", usually FT. 
             # But safe to say Unknown for now or infer FT implies mostly.
             pass

        # Parse duration
        months = 0
        
        # Year pattern
        y_match = DataCleaner.RE_DURATION_YEAR.search(text)
        if y_match:
            years = float(y_match.group(1))
            months = int(years * 12)
        
        # Month pattern lookup if year not found or addition? 
        # Usually it's either/or
        if months == 0:
            m_match = DataCleaner.RE_DURATION_MONTH.search(text)
            if m_match:
                months = int(m_match.group(1))
                
        if months > 0:
            options.append({
                "mode": mode.value,  # serialization safe
                "duration_months": months
            })
            
        return options

    @staticmethod
    def parse_deadlines(text: Optional[str]) -> List[Dict[str, Any]]:
        """
        Parses deadline string.
        Returns list of dicts: [{"round": "Main", "cutoff_date": datetime}]
        """
        if not text:
            return []
            
        text = str(text).strip()
        deadlines = []
        
        # Simple split by newline or comma if multiple dates
        # Heuristic: verify if it contains keywords like "Round 1", "Main", etc.
        
        lines = re.split(r'[\n;]', text)
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Try to parse date using dateutil (fuzzy)
            try:
                # Extract date portion? "Round 1: Dec 01, 2025"
                # dateutil fuzzy=True is powerful
                dt = date_parser.parse(line, fuzzy=True)
                
                # Infer round type
                round_type = RoundType.MAIN
                l_lower = line.lower()
                if 'early' in l_lower or 'round 1' in l_lower:
                    round_type = RoundType.EARLY
                elif 'clearing' in l_lower or 'rolling' in l_lower:
                    round_type = RoundType.EXTENDED
                elif 'extended' in l_lower:
                    round_type = RoundType.EXTENDED
                
                if isinstance(dt, datetime):
                    deadlines.append({
                        "round": round_type.value,
                        "cutoff_date": dt.isoformat()
                    })
            except (ValueError, OverflowError):
                continue
                
        return deadlines
