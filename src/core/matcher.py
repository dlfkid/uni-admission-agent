import logging
import re
from difflib import SequenceMatcher
from typing import List, Optional, Dict, Tuple, Set
from src.models.scraper_models import ProgramContext

logger = logging.getLogger(__name__)

class ProgramMatcher:
    """
    Optimized matcher for Program Group Codes.
    
    Strategies:
    1. Exact Match (Fast)
    2. Normalized Match (Fast - lower case, no special chars)
    3. Fuzzy Match (Slow - Levenshtein + Metadata Boosting)
    """

    def __init__(self, contexts: List[ProgramContext]):
        self.contexts = contexts
        self.exact_map: Dict[str, str] = {}
        self.norm_map: Dict[str, str] = {}
        
        # 2. Build normalized map for case/punctuation-insensitive lookup
        # 3. Build a set of existing group codes for fast slug validation
        self.slug_set: Set[str] = set()
        
        # Build Fast Access Maps
        for ctx in contexts:
            # Name maps
            if ctx.name_en:
                # Exact
                self.exact_map[ctx.name_en] = ctx.program_group_code
            
                # Normalized
                norm = self._normalize(ctx.name_en)
                if norm:
                    self.norm_map[norm] = ctx.program_group_code
            
            # Slug set
            if ctx.program_group_code:
                self.slug_set.add(ctx.program_group_code)

        self.stats = {
            "fast_match_count": 0,
            "slow_match_count": 0,
            "cache_hit_slug_count": 0, # Track deterministic slug hits
        }
        
        logger.info(
            f"ProgramMatcher initialized with {len(contexts)} contexts. "
            f"Exact keys: {len(self.exact_map)}, Norm keys: {len(self.norm_map)}"
        )

    def has_group_code(self, code: str) -> bool:
        """Check if a program group code already exists (O(1))."""
        return code in self.slug_set

    def _normalize(self, text: str) -> str:
        """Remove non-alphanumeric, to_lower."""
        return re.sub(r'[^a-z0-9]', '', text.lower())

    def match_fast(self, name_en: str) -> Optional[str]:
        """
        Check exact and normalized maps.
        Returns group_code if found, else None.
        """
        if not name_en:
            return None
            
        # 1. Exact
        if name_en in self.exact_map:
            return self.exact_map[name_en]
            
        # 2. Normalized
        norm = self._normalize(name_en)
        if norm in self.norm_map:
            return self.norm_map[norm]
            
        return None

    def find_top_matches(
        self, 
        target_name: str, 
        target_faculty: Optional[str] = None, 
        target_tuition: Optional[float] = None,
        limit: int = 5
    ) -> List[ProgramContext]:
        """
        Slow Path: Find top meaningful matches using heuristics.
        
        Scoring:
        - Name Similarity (0.0 - 1.0)
        - Faculty Match Bonus (+0.2)
        - Tuition Match Bonus (+0.1)
        """
        candidates = []
        target_norm = self._normalize(target_name)
        
        for ctx in self.contexts:
            score = 0.0
            
            # 1. Name Similarity (SequenceMatcher ratio)
            # Use raw strings for better distinctness, or normalized?
            # Normalized is safer against punctuation diffs.
            ctx_norm = self._normalize(ctx.name_en)
            sim = SequenceMatcher(None, target_norm, ctx_norm).ratio()
            score += sim
            
            # 2. Faculty Bonus
            if target_faculty and ctx.faculty:
                # Simple loose equality
                if target_faculty.lower() in ctx.faculty.lower() or ctx.faculty.lower() in target_faculty.lower():
                    score += 0.2
            
            # 3. Tuition Bonus (within 5% diff)
            if target_tuition and ctx.tuition_amount:
                # check overlap?
                try:
                    diff = abs(target_tuition - ctx.tuition_amount)
                    avg = (target_tuition + ctx.tuition_amount) / 2
                    if avg > 0 and (diff / avg) < 0.05:
                        score += 0.1
                except:
                    pass

            candidates.append((score, ctx))
        
        # Sort desc by score
        candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Return top N (unwrap context)
        # Filter purely low scores? e.g. < 0.3?
        # User implies we pass these to LLM, so even weak matches might help LLM decide "None".
        return [c[1] for c in candidates[:limit]]
