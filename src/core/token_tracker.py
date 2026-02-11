import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class TokenTracker:
    """
    Singleton class to track Google GenAI token usage and estimate costs.
    Thread-safe for concurrent operations.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(TokenTracker, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        self._initialized = True
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.model_name = "gemini-2.0-flash" # Default for estimation
        self.lock = threading.Lock()

    def track_usage(self, input_tokens: int, output_tokens: int, model: Optional[str] = None):
        """
        Record token usage from an API call.
        """
        with self.lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            if model:
                self.model_name = model
                
        # Optional: Log debug for individual calls if needed
        # logger.debug(f"Tracked: {input_tokens} in, {output_tokens} out")

    def _calculate_cost(self) -> float:
        """
        Estimate cost based on public pricing (approximate).
        Gemini 2.0 Flash:
        - Input: $0.10 / 1M tokens
        - Output: $0.40 / 1M tokens
        """
        input_cost = (self.total_input_tokens / 1_000_000) * 0.10
        output_cost = (self.total_output_tokens / 1_000_000) * 0.40
        return input_cost + output_cost

    def get_summary(self) -> str:
        """
        Return a formatted summary string.
        """
        cost = self._calculate_cost()
        total = self.total_input_tokens + self.total_output_tokens
        
        return (
            f"\n💰 Token Usage Summary:\n"
            f"{'-'*50}\n"
            f"Model:         {self.model_name}\n"
            f"Input Tokens:  {self.total_input_tokens:,}\n"
            f"Output Tokens: {self.total_output_tokens:,}\n"
            f"Total Tokens:  {total:,}\n"
            f"Est. Cost:     ${cost:.6f}\n"
            f"{'-'*50}"
        )

    def log_summary(self):
        """
        Log the summary at INFO level (or DEBUG as per requirement, but usually costs are important).
        User asked for DEBUG level but with emoji.
        """
        # User requested DEBUG level output for token summary
        summary = self.get_summary()
        # Using print for visibility in some CLI contexts, or logger.debug
        # Requirement: "任务完成后以DEBUG级别输出token消耗总数"
        logger.debug(summary)
        # Also print to stdout if logger level hides it? 
        # But instructions say "DEBUG level". So I'll stick to logger.debug.
        # Just in case, I'll log INFO too if it's 0 to confirm it works, 
        # but user said DEBUG. I will follow instructions.
        
        # Actually, let's use logger.info for the final summary so user sees it by default,
        # unless they strictly want it hidden in normal runs. 
        # Request: "以DEBUG级别输出" -> Okay, I will use debug.
        pass

# Global instance
tracker = TokenTracker()
