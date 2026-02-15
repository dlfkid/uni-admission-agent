"""
Translation Agent for automating multilingual field population.

This module provides a specialized agent that uses the RouterAgent
to translate program names between English and Chinese when one
is missing during data import.
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

from src.agents.factory import RouterAgent, create_router

logger = logging.getLogger(__name__)


class TranslationResponse(BaseModel):
    translated_text: str = Field(..., description="The translated text.")


class TranslationAgent:
    """
    Agent specialized in translating university admission content.
    """

    def __init__(self, router: Optional[RouterAgent] = None) -> None:
        if router is not None:
            self.router = router
        else:
            self.router = create_router()

    def translate_program_name(self, name: str, to_lang: str = "zh") -> str:
        """
        Translate a program name to the target language.

        Args:
            name: The source name (e.g., "MSc Computer Science").
            to_lang: Target language code ('zh' or 'en').

        Returns:
            Translated string. Returns original if translation fails.
        """
        if not name:
            return ""

        target_desc = "Simplified Chinese (zh-CN)" if to_lang == "zh" else "English"
        
        prompt = f"""
        You are a professional translator for university admission data.
        Translate the following academic program name to {target_desc}.
        
        Rules:
        1. Maintain academic formal tone.
        2. Keep common acronyms if standard (e.g. MBA).
        3. Output strict JSON with key 'translated_text'.
        
        Input Name: "{name}"
        """

        try:
            response = self.router.generate(prompt, TranslationResponse)
            
            if not response.text:
                logger.warning(f"Empty translation response for: {name}")
                return name
                
            result = TranslationResponse.model_validate_json(response.text)
            return result.translated_text.strip()

        except Exception as e:
            logger.error(f"Translation failed for '{name}': {e}")
            return name
